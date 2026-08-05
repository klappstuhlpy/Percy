"""News ingest: enabled ``news_sources`` -> fetched, parsed, tagged ``news_items``.

Mirrors ``app/services/comics/ingest.py`` in shape and in temperament: a report dataclass, an
idempotent run, and **nothing raises into the caller**. The caller is a ``@tasks.loop``, and a
task loop that raises stops running -- a stopped loop is a silent one, which is the failure mode
this whole module exists to make visible.

The pipeline, per due source
----------------------------
``fetch`` (:class:`~app.clients.news.NewsClient`, never raises) -> ``parse``
(:func:`~app.services.news.feeds.parse_feed`, never raises) -> per item ``upsert_item`` ->
:func:`~app.services.news.tagging.tag` -> ``replace_subjects`` -> ``mark_fetched(slug, status)``.

Three rules that are not obvious from that list:

* **The term index is compiled once per run, not per item.** Tagging a few hundred stories
  against a catalogue of thousands of subjects is the shape that gets slow first, and
  :class:`~app.services.news.tagging.TermIndex` is precisely the object that makes it a token
  walk instead of a scan. It is a constructor argument, so a caller cannot get this wrong.
* **A dead feed is recorded, not thrown.** ``mark_fetched`` is called on *every* attempt --
  ``'200'``, ``'404'``, ``'timeout'``, ``'breaker-open'``, a parse error -- and the run
  continues to the next source. One broken publisher must never cost the other five.
* **A story that matched no term is still stored.** It is catalogue content: ``GET /news``
  browses it and the ticker shows it. It simply can never be *delivered*, because delivery is
  ``news_subjects`` joined against subscriptions and it has no rows there. That is the correct
  direction to fail in (the plan's §8: a false tag notifies the wrong people, a missing tag
  notifies nobody).
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.services.news.feeds import parse_feed
from app.services.news.tagging import compile_terms, tag, tags_as_rows
from app.services.news.vocabulary import catalogue_terms

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from app.clients.news import NewsClient
    from app.database.repositories.news import NewsRepository
    from app.database.repositories.releases import ReleasesRepository
    from app.database.repositories.watchlist import WatchlistRepository
    from app.services.news.tagging import TermIndex

__all__ = ('MIN_POLL_INTERVAL', 'NewsIngest', 'NewsIngestReport', 'due_sources', 'load_vocabulary')

log = logging.getLogger(__name__)

#: Shortest gap between two polls of the *same* source.
#:
#: Thirty minutes, and the number is a politeness budget rather than a freshness one. An
#: entertainment newsroom publishes on the order of a few items an hour, and the RSS convention
#: for "do not re-fetch sooner than this" (``<ttl>``) is 60 minutes on most of the feeds in
#: :data:`~app.services.news.sources.DEFAULT_SOURCES`; half of that is comfortably inside what
#: any publisher considers polite while still being finer than the hourly digest, so a story
#: never misses the digest it belongs in for want of a poll. The polling loop ticks more often
#: than this on purpose (see the cog) -- the tick decides how quickly a *new* or re-enabled
#: source is picked up, this decides how often an existing one is asked.
MIN_POLL_INTERVAL = datetime.timedelta(minutes=30)

#: Longest ``last_status`` string written. The column is for an operator's eyes, and a feed that
#: fails with a paragraph of XML syntax errors should not make the sources table unreadable.
STATUS_LIMIT = 120


@dataclass(slots=True)
class NewsIngestReport:
    """What one :meth:`NewsIngest.run` did -- the one INFO line a polling run leaves behind."""

    polled: int = 0
    failed: int = 0
    seen: int = 0
    created: int = 0
    tagged: int = 0

    def __str__(self) -> str:
        return (f'{self.polled} source(s) polled ({self.failed} failed), '
                f'{self.seen} item(s) seen, {self.created} new, {self.tagged} tagged')


def due_sources(
    sources: Iterable[Mapping[str, Any]],
    now: datetime.datetime,
    *,
    interval: datetime.timedelta = MIN_POLL_INTERVAL,
    limit: int | None = None,
) -> list[Mapping[str, Any]]:
    """Filters source rows down to the ones due for a poll, oldest-polled first.

    Pure, so the cadence rule is testable without a clock or a pool. A source that has never
    been fetched (``last_fetched IS NULL``) is always due -- that is how a newly seeded or
    re-enabled feed gets its first run immediately rather than half an hour later.

    ``limit`` caps one run's work. The repository already returns rows oldest-polled first, so
    truncating is fair by construction: whatever is dropped this tick is at the head of the next
    one, and no source can starve behind a long list.
    """
    due = [row for row in sources if _is_due(row, now, interval)]
    return due if limit is None else due[:limit]


def _is_due(row: Mapping[str, Any], now: datetime.datetime, interval: datetime.timedelta) -> bool:
    last = row['last_fetched']
    return last is None or (now - last) >= interval


async def load_vocabulary(
    releases: ReleasesRepository, watchlist: WatchlistRepository, *, limit: int = 2000,
) -> TermIndex:
    """Reads the whole catalogue and compiles it into the index a run tags against.

    The one impure function in this module, and it sits here rather than in the pure
    ``vocabulary`` module for that reason: it is the six catalogue queries, and both callers of
    the ingest (the cog's polling loop and ``main.py news poll``) need exactly the same six.

    Compiled **once per run**. A polling run tags a few hundred stories against a catalogue of
    thousands of subjects, and the whole point of :class:`~app.services.news.tagging.TermIndex`
    is that the cost of turning terms into a lookup is paid once instead of per item.
    """
    return compile_terms(catalogue_terms(
        series=await releases.list_series(limit=limit),
        creators=await releases.list_names('comic_creators', limit=limit),
        characters=await releases.list_names('comic_characters', limit=limit),
        universes=await watchlist.list_universes(),
        franchises=await watchlist.list_franchises(),
        people=await watchlist.list_people(limit=limit),
    ))


class NewsIngest:
    """Polls feeds and writes the stories through to ``news_items``/``news_subjects``.

    ``index`` is the compiled catalogue vocabulary (see
    :func:`~app.services.news.vocabulary.catalogue_terms`), built once by the caller for the
    whole run.
    """

    __slots__ = ('client', 'index', 'repository')

    def __init__(self, client: NewsClient, repository: NewsRepository, index: TermIndex) -> None:
        self.client = client
        self.repository = repository
        self.index = index

    async def run(self, sources: Sequence[Mapping[str, Any]]) -> NewsIngestReport:
        """Polls every source in ``sources``. Never raises.

        The caller decides *which* sources are due (:func:`due_sources`); this decides nothing
        about scheduling, so a CLI run can poll one source on demand without arguing with the
        cadence rule.
        """
        report = NewsIngestReport()
        for source in sources:
            report.polled += 1
            try:
                await self._poll(source, report)
            except Exception:
                # A source whose failure is not the client's or the parser's -- a pool blip, a
                # row shaped unexpectedly. Counted, logged, and the run moves on.
                report.failed += 1
                log.exception('News ingest failed for source %r.', source['slug'])
        return report

    async def _poll(self, source: Mapping[str, Any], report: NewsIngestReport) -> None:
        """Fetches, parses and stores one source, recording the outcome either way."""
        slug = source['slug']
        response = await self.client.fetch_feed(source['feed_url'])
        if response.body is None:
            report.failed += 1
            await self.repository.mark_fetched(slug, response.status[:STATUS_LIMIT])
            log.warning('News source %r is failing: %s (%s).', slug, response.status, response.error)
            return

        parsed = parse_feed(response.body)
        if parsed.error is not None:
            report.failed += 1
            await self.repository.mark_fetched(slug, f'{response.status} {parsed.error}'[:STATUS_LIMIT])
            log.warning('News source %r returned nothing usable: %s', slug, parsed.error)
            return

        for item in parsed.items:
            report.seen += 1
            try:
                await self._store(slug, item, source['media'], report)
            except Exception as exc:
                # One malformed story must not cost the rest of the feed, exactly as in
                # ``ComicIngest.ingest``. It is not counted as a source failure: the source
                # answered fine.
                log.warning('News ingest failed for %s item %r: %s', slug, item.url, exc)

        await self.repository.mark_fetched(slug, response.status[:STATUS_LIMIT])

    async def _store(self, slug: str, item: Any, media: str, report: NewsIngestReport) -> None:
        """Upserts one story and re-derives its tags."""
        news_id, created = await self.repository.upsert_item({
            'source_slug': slug,
            'external_id': item.external_id,
            'url': item.url,
            'headline': item.headline,
            'excerpt': item.excerpt,
            'image_url': item.image_url,
            'author': item.author,
            'published_at': item.published_at,
        })
        if created:
            report.created += 1

        # Re-tagged on every poll rather than only on insert: the catalogue grows between runs,
        # so a series added today should pick up the story that mentioned it yesterday. The tags
        # of a story a source's ``media`` does not cover are dropped here rather than in the
        # tagger -- a comics newsroom mentioning an actor is not a film/TV tag.
        tags = [t for t in tag(item.headline, item.excerpt, self.index)
                if media == 'both' or t.subject.media == media]
        await self.repository.replace_subjects(news_id, tags_as_rows(tags))
        if tags:
            report.tagged += 1
