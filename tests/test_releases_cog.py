"""Tests for the releases Discord surface (``app/cogs/releases/``).

No bot, no pool: the cog is instantiated with ``__new__`` and handed a fake ``bot.db``, which is
enough because every method under test is a controller over the pure services and the
repository. What is worth proving here is the part that is neither pure nor SQL:

* the subject key survives the round trip through a Discord choice value, and a key that would
  not fit the 100-character cap is *dropped* rather than truncated into a key nothing matches;
* the picker resolves the right column per subject type -- slugs for series/universe/person,
  names for brand/creator/character. A subscription keyed on a display name is a subscription
  the fan-out can never match;
* a digest always renders into a sendable embed, and the capped tail is visible;
* a blocked DM hands its delivery claims back and mutes the user exactly once (the plan's D8);
* the nightly TMDB re-check writes only the dates that actually moved, and survives an
  unreachable TMDB -- a task loop that raises stops running, and a stopped loop is a silent one.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from app.clients.base import CircuitBreakerOpen
from app.cogs.releases import cog as cog_module
from app.cogs.releases.cog import Releases, catalogue_subjects
from app.cogs.releases.ui import (
    MAX_VALUE_LEN,
    build_digest_embed,
    build_subscriptions_embed,
    decode_subject,
    encode_subject,
    subject_choice,
)
from app.services.releases import Releasable, Subject, build_digest

DATE = datetime.date(2026, 8, 5)
NOW = datetime.datetime.combine(DATE, datetime.time.min)


def releasable(name: str, *, media: str = 'comic', url: str | None = None) -> Releasable:
    return Releasable(
        media=media,
        key=f'{media}/{name.lower().replace(" ", "-")}',
        name=name,
        release_date=DATE,
        first_seen=None,
        cover_url=None,
        url=url,
        subjects=frozenset(),
    )


def fake_bot(
    *,
    series: list[dict[str, Any]] | None = None,
    creators: list[dict[str, Any]] | None = None,
    characters: list[dict[str, Any]] | None = None,
    universes: list[dict[str, Any]] | None = None,
    people: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """A bot whose catalogue repositories answer with the rows a test cares about."""
    bot = MagicMock(name='Bot')
    bot.db.releases.list_series = AsyncMock(return_value=series or [])
    bot.db.releases.list_names = AsyncMock(
        side_effect=lambda table, **_: (creators or []) if table == 'comic_creators' else (characters or []))
    bot.db.watchlist.list_universes = AsyncMock(return_value=universes or [])
    bot.db.watchlist.search_people = AsyncMock(return_value=people or [])
    return bot


def cog_with(bot: MagicMock) -> Releases:
    """A ``Releases`` cog without its ``__init__`` -- which would start the dispatch loop."""
    cog = Releases.__new__(Releases)
    cog.bot = bot
    return cog


# -- Subject key codec --------------------------------------------------------


def test_subject_key_round_trips() -> None:
    assert decode_subject(encode_subject('comic', 'series', 'x-men')) == ('comic', 'series', 'x-men')


def test_subject_value_may_contain_a_colon() -> None:
    """Split from the left twice, so a name with a colon is not silently mangled."""
    key = encode_subject('comic', 'character', 'spider-man: noir')
    assert decode_subject(key) == ('comic', 'character', 'spider-man: noir')


@pytest.mark.parametrize('key', ['', 'comic', 'comic:series', 'comic:series:', 'nope:series:x', 'comic:person:x'])
def test_unreadable_or_mismatched_keys_are_rejected(key: str) -> None:
    """``person`` is a film/TV type: a comic subscription to one could never match anything."""
    assert decode_subject(key) is None


def test_a_long_label_is_truncated_but_the_value_is_not() -> None:
    choice = subject_choice('comic', 'creator', 'jack kirby', 'K' * 400)
    assert choice is not None
    assert len(choice.name) <= MAX_VALUE_LEN
    assert choice.value == 'comic:creator:jack kirby'


def test_a_candidate_whose_key_would_not_fit_is_dropped() -> None:
    """Truncating the value would store a corrupted key that looks fine and never matches."""
    assert subject_choice('comic', 'character', 'x' * MAX_VALUE_LEN, 'Long One') is None


# -- Picker: the slug-vs-name rule -------------------------------------------


async def test_catalogue_keys_series_and_universes_by_slug_and_names_by_name() -> None:
    bot = fake_bot(
        series=[{'series_slug': 'x-men', 'series_name': 'X-Men', 'issue_count': 12}],
        creators=[{'name': 'Jonathan Hickman', 'appearances': 9}],
        characters=[{'name': 'Storm', 'appearances': 4}],
        universes=[{'slug': 'mcu', 'name': 'Marvel Cinematic Universe', 'title_count': 38}],
    )
    try:
        keyed = {
            (candidate.media, candidate.subject_type): (candidate.value, candidate.label)
            for candidate in await catalogue_subjects(bot)
        }
    finally:
        catalogue_subjects.invalidate(bot)

    assert keyed['comic', 'series'] == ('x-men', 'X-Men')
    assert keyed['title', 'universe'] == ('mcu', 'Marvel Cinematic Universe')
    assert keyed['comic', 'creator'] == ('Jonathan Hickman', 'Jonathan Hickman')
    assert keyed['comic', 'character'] == ('Storm', 'Storm')
    assert keyed['comic', 'brand'][0] in ('marvel', 'dc', 'manga')


async def test_person_autocomplete_offers_the_slug_not_the_display_name() -> None:
    """``title_releasable`` derives ``person`` subjects from ``watch_people.slug``."""
    bot = fake_bot(people=[{'slug': 'pedro-pascal', 'name': 'Pedro Pascal', 'credit_count': 7}])
    cog = cog_with(bot)
    try:
        choices = await cog.subject_autocomplete(None, 'pascal')  # type: ignore[arg-type]
    finally:
        catalogue_subjects.invalidate(bot)

    assert [(choice.name, choice.value) for choice in choices] == [('Pedro Pascal', 'title:person:pedro-pascal')]


async def test_unsubscribe_autocomplete_only_offers_the_callers_own_rows() -> None:
    bot = MagicMock(name='Bot')
    bot.db.releases.list_subscriptions = AsyncMock(return_value=[
        {'media': 'title', 'subject_type': 'person', 'subject_value': 'pedro-pascal',
         'subject_label': 'Pedro Pascal'},
        {'media': 'comic', 'subject_type': 'series', 'subject_value': 'x-men', 'subject_label': 'X-Men'},
    ])
    interaction = MagicMock(user=MagicMock(id=42))

    choices = await cog_with(bot).subscription_autocomplete(interaction, 'pascal')

    bot.db.releases.list_subscriptions.assert_awaited_once_with(42)
    assert [choice.value for choice in choices] == ['title:person:pedro-pascal']


# -- Digest embed -------------------------------------------------------------


def test_a_digest_with_no_groups_still_renders_a_non_empty_embed() -> None:
    """An embed with neither description nor fields is an API error, not a display quirk."""
    embed = build_digest_embed(build_digest(1, []))
    assert embed.description
    assert embed.fields == []


def test_the_capped_tail_is_rendered_as_and_n_more() -> None:
    items = [releasable(f'Issue {index}') for index in range(4)]
    embed = build_digest_embed(build_digest(1, items, limit=1))
    assert any('and 3 more' in (field.value or '') for field in embed.fields)


def test_items_render_as_links_when_they_have_a_url() -> None:
    embed = build_digest_embed(build_digest(1, [releasable('Uncanny', url='https://example.test/1')]))
    assert '[Uncanny](https://example.test/1)' in (embed.fields[0].value or '')


def test_the_empty_subscription_list_points_at_subscribe() -> None:
    embed = build_subscriptions_embed(MagicMock(spec=discord.User), [])
    assert '/subscribe' in (embed.description or '')


# -- Delivery -----------------------------------------------------------------


class FakeReleasesRepository:
    """Only the four methods :meth:`Releases.deliver_to` reaches for."""

    def __init__(self, claimed: set[tuple[int, str, str]]) -> None:
        self.claimed = claimed
        self.released: list[list[tuple[int, str, str]]] = []
        self.muted: list[int] = []

    async def claim_deliveries(self, rows: Any) -> set[tuple[int, str, str]]:
        self.requested = list(rows)
        return set(self.claimed)

    async def release_claims(self, rows: Any) -> None:
        self.released.append(list(rows))

    async def disable_all_delivery(self, user_id: int) -> int:
        self.muted.append(user_id)
        return 2


def fake_user(*, send: AsyncMock) -> MagicMock:
    user = MagicMock(spec=discord.User)
    user.id = 7
    user.send = send
    return user


def forbidden() -> discord.Forbidden:
    return discord.Forbidden(MagicMock(status=403, reason='Forbidden'), 'Cannot send messages to this user')


async def test_a_blocked_dm_hands_the_claims_back_and_mutes_the_user_once() -> None:
    items = [releasable('Uncanny'), releasable('Astonishing')]
    claims = {(7, item.media, item.key) for item in items}
    repo = FakeReleasesRepository(claims)
    bot = MagicMock(name='Bot')
    bot.db.releases = repo
    user = fake_user(send=AsyncMock(side_effect=forbidden()))

    delivered = await cog_with(bot).deliver_to(user, items)

    assert delivered is False
    assert repo.released == [sorted(claims)]
    assert repo.muted == [7]


async def test_an_unexpected_failure_hands_the_claims_back_without_muting() -> None:
    """A transient error must not cost the user the release, nor mute them for it."""
    items = [releasable('Uncanny')]
    repo = FakeReleasesRepository({(7, items[0].media, items[0].key)})
    bot = MagicMock(name='Bot')
    bot.db.releases = repo
    user = fake_user(send=AsyncMock(side_effect=RuntimeError('gateway ate it')))

    assert await cog_with(bot).deliver_to(user, items) is False
    assert repo.released == [[(7, 'comic', 'comic/uncanny')]]
    assert repo.muted == []


async def test_only_newly_claimed_items_are_sent() -> None:
    """The claim, not the query window, is what stops a user hearing about a book twice."""
    items = [releasable('Uncanny'), releasable('Astonishing')]
    repo = FakeReleasesRepository({(7, 'comic', 'comic/uncanny')})
    bot = MagicMock(name='Bot')
    bot.db.releases = repo
    send = AsyncMock()

    assert await cog_with(bot).deliver_to(fake_user(send=send), items) is True

    embed = send.await_args.kwargs['embed']
    rendered = '\n'.join(field.value or '' for field in embed.fields)
    assert 'Uncanny' in rendered
    assert 'Astonishing' not in rendered
    assert repo.released == []


COMIC_ROW = {'id': 1, 'brand': 'marvel', 'slug': 'x-men-1', 'title': 'X-Men #1', 'series_slug': 'x-men',
             'release_date': DATE, 'first_seen': None, 'cover_url': None, 'url': None}


def news_row(news_id: int, headline: str) -> dict[str, Any]:
    return {'id': news_id, 'source_slug': 'cbr', 'headline': headline, 'url': f'https://cbr.test/{news_id}',
            'image_url': None, 'published_at': DATE, 'fetched_at': None}


def subscription(subject_type: str, value: str, *, media: str = 'comic', streams: str = 'releases') -> dict[str, Any]:
    return {'user_id': 7, 'media': media, 'subject_type': subject_type, 'subject_value': value,
            'streams': streams, 'delivery': 'dm', 'created_at': None}


def dispatch_bot(
    *,
    comics: list[dict[str, Any]] | None = None,
    stories: list[dict[str, Any]] | None = None,
    tags: list[dict[str, Any]] | None = None,
    subscriptions: list[dict[str, Any]] | None = None,
    claims: set[tuple[int, str, str]] | None = None,
) -> MagicMock:
    """A bot whose every repository the dispatch loop touches answers with fixture rows.

    ``claims`` defaults to "everything offered is new", so a test that cares about matching does
    not have to restate the delivery keys it expects.
    """
    bot = MagicMock(name='Bot')
    bot.db.releases.recent_comic_releases = AsyncMock(return_value=comics or [])
    bot.db.releases.credits_for_releases = AsyncMock(return_value=[
        {'release_id': 1, 'name': 'Jonathan Hickman', 'role': 'writer'}] if comics else [])
    bot.db.releases.characters_for_releases = AsyncMock(return_value=[])
    bot.db.releases.subscribers_for_subjects = AsyncMock(return_value=subscriptions or [])
    bot.db.releases.claim_deliveries = (
        AsyncMock(return_value=claims) if claims is not None else AsyncMock(side_effect=set))
    bot.db.releases.prune_deliveries = AsyncMock(return_value=0)
    bot.db.watchlist.titles_releasing_between = AsyncMock(return_value=[])
    bot.db.watchlist.list_credits = AsyncMock(return_value=[])
    bot.db.news.recent_news = AsyncMock(return_value=stories or [])
    bot.db.news.subjects_for_items = AsyncMock(return_value=tags or [])
    bot.db.news.list_sources = AsyncMock(return_value=[{'slug': 'cbr', 'name': 'CBR'}])
    return bot


async def run_dispatch(bot: MagicMock) -> AsyncMock:
    """Runs one dispatch pass and hands back the DM mock, for the assertions to read."""
    send = AsyncMock()
    bot.get_user = MagicMock(return_value=fake_user(send=send))
    cog = cog_with(bot)
    await cog.dispatch_releases.coro(cog)
    return send


def rendered(send: AsyncMock) -> str:
    embed = send.await_args.kwargs['embed']
    return '\n'.join(f'{field.name}\n{field.value or ""}' for field in embed.fields)


async def test_a_dispatch_run_fans_a_comic_out_to_its_subscriber() -> None:
    """End to end over fakes: catalogue rows in, one claimed DM out, deliveries pruned."""
    bot = dispatch_bot(
        comics=[COMIC_ROW],
        subscriptions=[subscription('creator', 'jonathan hickman')],
        claims={(7, 'comic', 'marvel/x-men-1')},
    )

    send = await run_dispatch(bot)

    assert ('comic', 'creator', 'jonathan hickman') in bot.db.releases.subscribers_for_subjects.await_args.args[0]
    assert 'X-Men #1' in rendered(send)
    bot.db.releases.prune_deliveries.assert_awaited_once()


# -- News in the digest -------------------------------------------------------


async def test_a_news_only_subscriber_gets_the_story_and_its_publisher() -> None:
    """§9: the DM has to name the source and link the original, not just the headline."""
    bot = dispatch_bot(
        stories=[news_row(3, 'X-Men movie casting news')],
        tags=[{'news_id': 3, 'media': 'comic', 'subject_type': 'series', 'subject_value': 'x-men'}],
        subscriptions=[subscription('series', 'x-men', streams='news')],
    )

    body = rendered(await run_dispatch(bot))

    assert 'X-Men movie casting news' in body
    assert 'CBR' in body
    assert 'News' in body


async def test_a_releases_only_subscriber_is_told_nothing_about_news() -> None:
    """``streams`` is the volume dial: news is opt-in, and the default is releases only."""
    bot = dispatch_bot(
        stories=[news_row(3, 'X-Men movie casting news')],
        tags=[{'news_id': 3, 'media': 'comic', 'subject_type': 'series', 'subject_value': 'x-men'}],
        subscriptions=[subscription('series', 'x-men', streams='releases')],
    )

    send = AsyncMock()
    bot.get_user = MagicMock(return_value=fake_user(send=send))
    cog = cog_with(bot)
    await cog.dispatch_releases.coro(cog)

    send.assert_not_awaited()
    bot.db.releases.claim_deliveries.assert_not_awaited()


async def test_a_story_tagged_in_both_media_is_delivered_once() -> None:
    """Two ``news_subjects`` rows must not become two claims and two lines in one DM."""
    bot = dispatch_bot(
        stories=[news_row(3, 'X-Men film adds a lead')],
        tags=[
            {'news_id': 3, 'media': 'comic', 'subject_type': 'series', 'subject_value': 'x-men'},
            {'news_id': 3, 'media': 'title', 'subject_type': 'universe', 'subject_value': 'mcu'},
        ],
        subscriptions=[
            subscription('series', 'x-men', streams='both'),
            subscription('universe', 'mcu', media='title', streams='news'),
        ],
    )

    body = rendered(await run_dispatch(bot))

    assert bot.db.releases.claim_deliveries.await_args.args[0] == [(7, 'news', 'news/3')]
    assert body.count('X-Men film adds a lead') == 1


async def test_a_subscriber_of_both_streams_gets_one_dm_carrying_both_halves() -> None:
    bot = dispatch_bot(
        comics=[COMIC_ROW],
        stories=[news_row(3, 'X-Men movie casting news')],
        tags=[{'news_id': 3, 'media': 'comic', 'subject_type': 'series', 'subject_value': 'x-men'}],
        subscriptions=[subscription('series', 'x-men', streams='both')],
    )

    send = await run_dispatch(bot)
    body = rendered(send)

    assert send.await_count == 1
    assert 'X-Men #1' in body
    assert 'X-Men movie casting news' in body


# -- The separate news cap ----------------------------------------------------


def story(index: int) -> Releasable:
    return Releasable(media='news', key=f'news/{index}', name=f'Story {index}', release_date=DATE,
                      first_seen=None, cover_url=None, url=None, subjects=frozenset(), source='CBR')


async def test_the_news_cap_holds_while_the_release_half_still_gets_through() -> None:
    """A busy news week must never crowd out the release someone actually subscribed for."""
    stories = [story(index) for index in range(12)]
    repo = FakeReleasesRepository(set())
    repo.claimed = {(7, 'comic', 'comic/uncanny'), *((7, 'news', item.key) for item in stories)}
    bot = MagicMock(name='Bot')
    bot.db.releases = repo
    send = AsyncMock()

    assert await cog_with(bot).deliver_to(fake_user(send=send), [releasable('Uncanny')], stories) is True

    offered = [key for _, media, key in repo.requested if media == 'news']
    assert len(offered) == cog_module.NEWS_LIMIT == 5
    body = '\n'.join(field.value or '' for field in send.await_args.kwargs['embed'].fields)
    assert 'Uncanny' in body
    assert body.count('Story ') == cog_module.NEWS_LIMIT


async def test_uncapped_stories_are_never_claimed_so_they_lead_the_next_run() -> None:
    """The slice happens before the claim, or the overflow would be lost rather than deferred."""
    stories = [story(index) for index in range(12)]
    repo = FakeReleasesRepository(set())
    repo.claimed = {(7, 'news', item.key) for item in stories[:cog_module.NEWS_LIMIT]}

    bot = MagicMock(name='Bot')
    bot.db.releases = repo
    await cog_with(bot).deliver_to(fake_user(send=AsyncMock()), [], stories)

    assert (7, 'news', 'news/11') not in repo.requested


async def test_a_blocked_dm_hands_back_the_news_claims_too_and_mutes_once() -> None:
    """D8 is unchanged by the second stream: every claim goes back, every subscription mutes."""
    items = [releasable('Uncanny')]
    stories = [story(0)]
    claims = {(7, 'comic', 'comic/uncanny'), (7, 'news', 'news/0')}
    repo = FakeReleasesRepository(claims)
    bot = MagicMock(name='Bot')
    bot.db.releases = repo

    delivered = await cog_with(bot).deliver_to(
        fake_user(send=AsyncMock(side_effect=forbidden())), items, stories)

    assert delivered is False
    assert repo.released == [sorted(claims)]
    assert repo.muted == [7]


async def test_nothing_claimed_means_nothing_sent() -> None:
    repo = FakeReleasesRepository(set())
    bot = MagicMock(name='Bot')
    bot.db.releases = repo
    send = AsyncMock()

    assert await cog_with(bot).deliver_to(fake_user(send=send), [releasable('Uncanny')]) is False
    send.assert_not_awaited()
    assert repo.released == []


# -- The news polling loop ----------------------------------------------------


def poll_bot(sources: list[dict[str, Any]], **overrides: Any) -> MagicMock:
    bot = MagicMock(name='Bot')
    bot.db.news.get_source = AsyncMock(return_value={'slug': 'cbr'})
    bot.db.news.list_sources = AsyncMock(return_value=sources)
    bot.db.releases.list_series = AsyncMock(return_value=[])
    bot.db.releases.list_names = AsyncMock(return_value=[])
    bot.db.watchlist.list_universes = AsyncMock(return_value=[])
    bot.db.watchlist.list_franchises = AsyncMock(return_value=[])
    bot.db.watchlist.list_people = AsyncMock(return_value=[])
    for name, value in overrides.items():
        setattr(bot.db.news, name, value)
    return bot


async def run_poll(bot: MagicMock, monkeypatch: pytest.MonkeyPatch, report: Any = None) -> None:
    monkeypatch.setattr(cog_module, 'NewsClient', lambda session: MagicMock(name='NewsClient'))
    monkeypatch.setattr(cog_module, 'NewsIngest', lambda *_: MagicMock(run=AsyncMock(return_value=report)))
    cog = cog_with(bot)
    await cog.poll_news.coro(cog)


async def test_a_polling_run_logs_one_line_and_takes_the_head_of_the_due_list(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """``list_sources`` is oldest-polled first, so the per-tick budget cannot starve a source."""
    sources = [{'slug': f'source-{index}', 'feed_url': 'x', 'media': 'both', 'last_fetched': None}
               for index in range(cog_module.MAX_SOURCES_PER_RUN + 3)]
    captured: list[Any] = []
    bot = poll_bot(sources)

    monkeypatch.setattr(cog_module, 'seed_sources', AsyncMock(return_value=[]))
    monkeypatch.setattr(cog_module, 'NewsIngest',
                        lambda *_: MagicMock(run=AsyncMock(side_effect=lambda rows: captured.extend(rows) or 'report')))
    monkeypatch.setattr(cog_module, 'NewsClient', lambda session: MagicMock(name='NewsClient'))

    cog = cog_with(bot)
    with caplog.at_level(logging.INFO):
        await cog.poll_news.coro(cog)

    assert [row['slug'] for row in captured] == [f'source-{index}' for index in range(cog_module.MAX_SOURCES_PER_RUN)]
    assert any('News poll: report' in record.getMessage() for record in caplog.records)


async def test_a_tick_with_nothing_due_never_loads_the_vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = poll_bot([{'slug': 'cbr', 'feed_url': 'x', 'media': 'both',
                     'last_fetched': discord.utils.utcnow().replace(tzinfo=None)}])
    monkeypatch.setattr(cog_module, 'seed_sources', AsyncMock(return_value=[]))

    await run_poll(bot, monkeypatch)

    bot.db.releases.list_series.assert_not_awaited()


async def test_a_failing_polling_run_is_logged_rather_than_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """A task loop that raises stops running, and a stopped loop is a silent one."""
    bot = poll_bot([], list_sources=AsyncMock(side_effect=RuntimeError('relation does not exist')))
    monkeypatch.setattr(cog_module, 'seed_sources', AsyncMock(return_value=[]))

    with caplog.at_level(logging.ERROR):
        await run_poll(bot, monkeypatch)

    assert any('news polling run failed' in record.getMessage() for record in caplog.records)


# -- Nightly release-date re-check --------------------------------------------


class FakeTMDB:
    """A TMDB client that answers with one payload per title, or refuses to answer at all."""

    def __init__(self, payloads: dict[int, dict[str, Any]] | None = None, *, error: Exception | None = None) -> None:
        self.payloads = payloads or {}
        self.error = error
        self.available = True

    async def movie(self, movie_id: int) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        return self.payloads[movie_id]

    async def tv(self, tv_id: int) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        return self.payloads[tv_id]


def refresh_bot(rows: list[dict[str, Any]]) -> MagicMock:
    bot = MagicMock(name='Bot')
    bot.db.watchlist.titles_releasing_between = AsyncMock(return_value=rows)
    bot.db.watchlist.set_release_date = AsyncMock()
    return bot


def title_row(**kwargs: Any) -> dict[str, Any]:
    row: dict[str, Any] = {'id': 10, 'tmdb_type': 'movie', 'tmdb_id': 617126, 'release_date': DATE}
    row.update(kwargs)
    return row


async def run_refresh(bot: MagicMock, client: FakeTMDB, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cog_module, 'TMDBClient', lambda session: client)
    cog = cog_with(bot)
    await cog.refresh_release_dates.coro(cog)


async def test_a_moved_release_date_is_written_once(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = refresh_bot([title_row()])
    client = FakeTMDB({617126: {'release_date': '2026-09-01'}})

    await run_refresh(bot, client, monkeypatch)

    bot.db.watchlist.set_release_date.assert_awaited_once_with(10, datetime.date(2026, 9, 1))


async def test_an_unchanged_release_date_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = refresh_bot([title_row()])
    client = FakeTMDB({617126: {'release_date': DATE.isoformat()}})

    await run_refresh(bot, client, monkeypatch)

    bot.db.watchlist.set_release_date.assert_not_awaited()


async def test_a_show_is_re_checked_against_its_first_air_date(monkeypatch: pytest.MonkeyPatch) -> None:
    """TMDB spells a show's date ``first_air_date``; reading ``release_date`` would find nothing
    and silently never correct a single series."""
    bot = refresh_bot([title_row(tmdb_type='tv', tmdb_id=88396)])
    client = FakeTMDB({88396: {'first_air_date': '2026-08-19'}})

    await run_refresh(bot, client, monkeypatch)

    bot.db.watchlist.set_release_date.assert_awaited_once_with(10, datetime.date(2026, 8, 19))


async def test_a_date_tmdb_no_longer_publishes_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """One empty payload must not null a date out of the catalogue."""
    bot = refresh_bot([title_row()])

    await run_refresh(bot, FakeTMDB({617126: {'release_date': None}}), monkeypatch)

    bot.db.watchlist.set_release_date.assert_not_awaited()


async def test_an_unreachable_tmdb_logs_and_survives(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """The loop must come back tomorrow: a raised exception would stop it for good."""
    bot = refresh_bot([title_row(), title_row(id=11, tmdb_id=999)])
    client = FakeTMDB(error=CircuitBreakerOpen('TMDB', 30.0))

    with caplog.at_level(logging.INFO):
        await run_refresh(bot, client, monkeypatch)

    bot.db.watchlist.set_release_date.assert_not_awaited()
    assert sum('re-check failed' in record.message for record in caplog.records) == 2
    assert any('checked 2 title(s), 0 date(s) moved' in record.getMessage() for record in caplog.records)


async def test_an_unconfigured_tmdb_skips_the_run_without_querying(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    bot = refresh_bot([title_row()])
    client = FakeTMDB()
    client.available = False

    with caplog.at_level(logging.INFO):
        await run_refresh(bot, client, monkeypatch)

    bot.db.watchlist.titles_releasing_between.assert_not_awaited()
    assert any('TMDB is not configured' in record.getMessage() for record in caplog.records)


async def test_a_database_failure_is_logged_rather_than_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    bot = refresh_bot([])
    bot.db.watchlist.titles_releasing_between = AsyncMock(side_effect=RuntimeError('pool is gone'))

    with caplog.at_level(logging.ERROR):
        await run_refresh(bot, FakeTMDB(), monkeypatch)

    assert any('release-date refresh failed' in record.getMessage() for record in caplog.records)


async def test_the_refresh_window_is_the_next_thirty_days(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = refresh_bot([])

    await run_refresh(bot, FakeTMDB(), monkeypatch)

    start, end = bot.db.watchlist.titles_releasing_between.await_args.args
    assert end - start == cog_module.REFRESH_HORIZON == datetime.timedelta(days=30)
    assert start == discord.utils.utcnow().date()


# -- Gathering releasables: where a season's credits come from ----------------


SERIES_ID, FILM_ID = 147, 900


def watch_row(row_id: int, slug: str, **kwargs: Any) -> dict[str, Any]:
    """One ``titles_releasing_between`` row -- exactly the columns that query selects."""
    row: dict[str, Any] = {
        'id': row_id, 'universe': 'mcu', 'slug': slug, 'title': slug, 'release_date': DATE,
        'poster_path': None, 'sub_universe': None, 'tmdb_type': 'tv', 'tmdb_id': 1,
        'season_number': None, 'parent_id': None,
    }
    row.update(kwargs)
    return row


def credit_row(title_id: int, slug: str, character: str) -> dict[str, Any]:
    return {'title_id': title_id, 'role': 'cast', 'slug': slug, 'character_name': character}


def gather_bot(titles: list[dict[str, Any]], credits: list[dict[str, Any]]) -> MagicMock:
    """A bot whose comic half is empty and whose ``list_credits`` behaves like the real query:
    it answers only for the ids it was actually asked for, so a lookup on the wrong id gets
    nothing back rather than the fixture's whole table."""
    bot = MagicMock(name='Bot')
    bot.db.releases.recent_comic_releases = AsyncMock(return_value=[])
    bot.db.releases.credits_for_releases = AsyncMock(return_value=[])
    bot.db.releases.characters_for_releases = AsyncMock(return_value=[])
    bot.db.watchlist.titles_releasing_between = AsyncMock(return_value=titles)
    bot.db.watchlist.list_credits = AsyncMock(
        side_effect=lambda ids: [row for row in credits if row['title_id'] in set(ids)])
    return bot


async def test_a_season_carries_its_series_person_subjects() -> None:
    """V45 made a multi-season show one row per season plus a container, and the container is
    the only row that holds credits -- while the season is the only row that can dispatch. Look
    credits up by the season's own id and a subscriber following an actor is simply never told.
    """
    bot = gather_bot(
        [watch_row(385, 'shield-s1', season_number=1, parent_id=SERIES_ID),
         watch_row(FILM_ID, 'no-way-home', tmdb_type='movie')],
        [credit_row(SERIES_ID, 'clark-gregg', 'Phil Coulson'), credit_row(FILM_ID, 'tom-holland', 'Peter Parker')],
    )

    items = {item.key: item for item in await cog_with(bot).gather_releasables(NOW)}

    season = items['mcu/shield-s1']
    assert Subject.of('title', 'person', 'clark-gregg') in season.subjects
    assert Subject.of('title', 'character', 'Phil Coulson') in season.subjects


async def test_a_film_keeps_its_own_credits() -> None:
    """The regression risk: a film and a single-season show have no ``parent_id`` at all."""
    bot = gather_bot(
        [watch_row(385, 'shield-s1', season_number=1, parent_id=SERIES_ID),
         watch_row(FILM_ID, 'no-way-home', tmdb_type='movie')],
        [credit_row(SERIES_ID, 'clark-gregg', 'Phil Coulson'), credit_row(FILM_ID, 'tom-holland', 'Peter Parker')],
    )

    film = {item.key: item for item in await cog_with(bot).gather_releasables(NOW)}['mcu/no-way-home']

    assert Subject.of('title', 'person', 'tom-holland') in film.subjects
    assert Subject.of('title', 'person', 'clark-gregg') not in film.subjects


async def test_credits_for_the_whole_window_are_one_query() -> None:
    """Two seasons of one show and a film are still a single batched lookup."""
    bot = gather_bot(
        [watch_row(385, 'shield-s1', season_number=1, parent_id=SERIES_ID),
         watch_row(386, 'shield-s2', season_number=2, parent_id=SERIES_ID),
         watch_row(FILM_ID, 'no-way-home', tmdb_type='movie')],
        [credit_row(SERIES_ID, 'clark-gregg', 'Phil Coulson')],
    )

    await cog_with(bot).gather_releasables(NOW)

    bot.db.watchlist.list_credits.assert_awaited_once()
    assert bot.db.watchlist.list_credits.await_args.args[0] == [SERIES_ID, SERIES_ID, FILM_ID]
