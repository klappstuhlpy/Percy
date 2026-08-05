"""Tests for the news ingest (``app/services/news/ingest.py``).

Pure: a fake client answering with fixture XML and a fake repository recording what it was
asked to write. No pool, no HTTP, no bot. What is worth proving here is everything the polling
loop delegates and therefore cannot check for itself:

* the report is the one INFO line a run leaves behind, and its numbers mean what they say;
* a story seen twice is stored once -- the upsert is the dedupe, not a set in the ingest;
* a source that fails records *why* in ``last_status`` and the run continues to the next one.
  A dead feed has to be visible, and one broken publisher must never cost the other five;
* a story that matched no term is still stored. It is catalogue content for ``GET /news``; it
  simply can never be delivered, which is the correct direction to fail in (§8);
* the cadence rule (``due_sources``) is testable without a clock.
"""

from __future__ import annotations

import datetime
from typing import Any

from app.clients.news import FeedResponse
from app.services.news import NewsIngest, build_terms, compile_terms, due_sources

NOW = datetime.datetime(2026, 8, 5, 12, 0)


def feed(*items: tuple[str, str]) -> str:
    """A minimal RSS document carrying ``(headline, link)`` pairs."""
    entries = ''.join(
        f'<item><title>{title}</title><link>{link}</link>'
        f'<pubDate>Tue, 04 Aug 2026 10:00:00 +0000</pubDate></item>'
        for title, link in items
    )
    return f'<rss version="2.0"><channel><title>Test</title>{entries}</channel></rss>'


def source(slug: str = 'cbr', *, media: str = 'both', last_fetched: datetime.datetime | None = None) -> dict[str, Any]:
    return {'slug': slug, 'feed_url': f'https://{slug}.test/feed/', 'media': media,
            'last_fetched': last_fetched, 'enabled': True}


class FakeClient:
    """Answers each feed URL with a canned :class:`FeedResponse`, like the real one never raising."""

    def __init__(self, responses: dict[str, FeedResponse]) -> None:
        self.responses = responses
        self.fetched: list[str] = []

    async def fetch_feed(self, url: str) -> FeedResponse:
        self.fetched.append(url)
        return self.responses.get(url, FeedResponse(url=url, status='404', error='not found'))


class FakeRepository:
    """The four methods the ingest reaches for, backed by dicts."""

    def __init__(self) -> None:
        self.items: dict[str, int] = {}
        self.subjects: dict[int, list[tuple[str, str, str, str, str | None]]] = {}
        self.statuses: list[tuple[str, str]] = []

    async def upsert_item(self, row: dict[str, Any]) -> tuple[int, bool]:
        url = row['url']
        if url in self.items:
            return self.items[url], False
        self.items[url] = len(self.items) + 1
        return self.items[url], True

    async def replace_subjects(self, news_id: int, tags: Any) -> None:
        self.subjects[news_id] = list(tags)

    async def mark_fetched(self, slug: str, status: str) -> None:
        self.statuses.append((slug, status))


def index_of(*subjects: tuple[str, str, str, str]) -> Any:
    """Compiles a term index from ``(media, subject_type, value, display name)`` tuples."""
    return compile_terms([
        term for media, kind, value, name in subjects for term in build_terms(media, kind, value, name)
    ])


def ingest(repository: FakeRepository, client: FakeClient, *subjects: tuple[str, str, str, str]) -> NewsIngest:
    return NewsIngest(client, repository, index_of(*subjects))  # type: ignore[arg-type]


# -- The cadence rule ---------------------------------------------------------


def test_a_never_fetched_source_is_always_due() -> None:
    """A newly seeded or re-enabled feed runs immediately, not half an hour later."""
    assert due_sources([source(last_fetched=None)], NOW) != []


def test_a_recently_polled_source_is_not_due() -> None:
    assert due_sources([source(last_fetched=NOW - datetime.timedelta(minutes=5))], NOW) == []


def test_the_limit_truncates_rather_than_reordering() -> None:
    """The repository hands them over oldest-polled first, so the head is the fair slice."""
    rows = [source(f'source-{index}') for index in range(5)]
    assert [row['slug'] for row in due_sources(rows, NOW, limit=2)] == ['source-0', 'source-1']


# -- The run report -----------------------------------------------------------


async def test_the_report_counts_what_one_run_did() -> None:
    repository = FakeRepository()
    client = FakeClient({'https://cbr.test/feed/': FeedResponse(
        url='https://cbr.test/feed/', status='200',
        body=feed(('X-Men returns this fall', 'https://cbr.test/1'), ('A quiet Tuesday', 'https://cbr.test/2')))})

    report = await ingest(repository, client, ('comic', 'series', 'x-men', 'X-Men')).run([source()])

    assert (report.polled, report.failed, report.seen, report.created, report.tagged) == (1, 0, 2, 2, 1)
    assert '2 item(s) seen, 2 new, 1 tagged' in str(report)


async def test_a_story_seen_twice_is_stored_once() -> None:
    """Dedupe is the upsert on ``url``, so a re-poll refreshes rather than duplicating."""
    repository = FakeRepository()
    body = feed(('X-Men returns this fall', 'https://cbr.test/1'))
    client = FakeClient({'https://cbr.test/feed/': FeedResponse(url='https://cbr.test/feed/', status='200', body=body)})
    news = ingest(repository, client, ('comic', 'series', 'x-men', 'X-Men'))

    first = await news.run([source()])
    second = await news.run([source()])

    assert (first.created, second.created) == (1, 0)
    assert (first.seen, second.seen) == (1, 1)
    assert len(repository.items) == 1


async def test_a_tagless_story_is_still_stored_but_carries_no_subjects() -> None:
    """Catalogue content for ``GET /news``; it simply can never be delivered."""
    repository = FakeRepository()
    client = FakeClient({'https://cbr.test/feed/': FeedResponse(
        url='https://cbr.test/feed/', status='200', body=feed(('A quiet Tuesday', 'https://cbr.test/2')))})

    report = await ingest(repository, client, ('comic', 'series', 'x-men', 'X-Men')).run([source()])

    assert report.created == 1
    assert report.tagged == 0
    assert repository.subjects == {1: []}


async def test_a_tag_outside_the_sources_medium_is_dropped() -> None:
    """A comics newsroom mentioning an actor is not a film/TV tag."""
    repository = FakeRepository()
    client = FakeClient({'https://comics-beat.test/feed/': FeedResponse(
        url='https://comics-beat.test/feed/', status='200',
        body=feed(('Pedro Pascal cast in something', 'https://comics-beat.test/1')))})

    report = await ingest(
        repository, client, ('title', 'person', 'pedro-pascal', 'Pedro Pascal'),
    ).run([source('comics-beat', media='comic')])

    assert report.tagged == 0
    assert repository.subjects == {1: []}


# -- A dead feed is visible, not silent ---------------------------------------


async def test_a_failing_source_records_its_status_and_the_run_continues() -> None:
    repository = FakeRepository()
    client = FakeClient({
        'https://dead.test/feed/': FeedResponse(url='https://dead.test/feed/', status='timeout', error='timed out'),
        'https://cbr.test/feed/': FeedResponse(
            url='https://cbr.test/feed/', status='200', body=feed(('X-Men returns', 'https://cbr.test/1'))),
    })

    report = await ingest(repository, client, ('comic', 'series', 'x-men', 'X-Men')).run(
        [source('dead'), source('cbr')])

    assert (report.polled, report.failed, report.created) == (2, 1, 1)
    assert ('dead', 'timeout') in repository.statuses
    assert ('cbr', '200') in repository.statuses
    assert repository.items == {'https://cbr.test/1': 1}


async def test_an_unparseable_body_records_the_parse_error_against_the_source() -> None:
    repository = FakeRepository()
    client = FakeClient({'https://cbr.test/feed/': FeedResponse(
        url='https://cbr.test/feed/', status='200', body='<not-a-feed>')})

    report = await ingest(repository, client).run([source()])

    assert report.failed == 1
    slug, status = repository.statuses[0]
    assert slug == 'cbr'
    assert status.startswith('200 ')


async def test_a_repository_failure_is_counted_rather_than_raised() -> None:
    """The caller is a task loop: a raise would stop it running for good."""
    repository = FakeRepository()

    async def boom(slug: str, status: str) -> None:
        raise RuntimeError('pool is gone')

    repository.mark_fetched = boom  # type: ignore[assignment]
    client = FakeClient({'https://cbr.test/feed/': FeedResponse(
        url='https://cbr.test/feed/', status='200', body=feed(('X-Men returns', 'https://cbr.test/1')))})

    report = await ingest(repository, client).run([source()])

    assert (report.polled, report.failed) == (1, 1)
