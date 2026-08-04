"""Tests for the watchlist payload shaping and the internal-API handlers built on it.

Two layers, matching how the code is split: :mod:`app.services.watchlist.payload` is pure
row-to-JSON shaping and is tested directly, while the handlers in
:mod:`app.internal_api.routers.watchlist` are plain async functions taking the bot first, so
they are called with a fake repository rather than through an HTTP client.

The JSON-safety assertions are the load-bearing ones: asyncpg hands back
:class:`~decimal.Decimal` and :mod:`datetime` objects, and the dashboard's Rust models are
generated against the converted shape.
"""

from __future__ import annotations

import datetime
import decimal
from typing import Any

import pytest
from fastapi import HTTPException

from app.internal_api.routers import watchlist as api
from app.services.watchlist.payload import (
    credit_payloads,
    era_payloads,
    person_payload,
    person_summary,
    title_payload,
    universe_payload,
    universe_stats,
    universe_summary,
)

UNIVERSE = {
    'slug': 'mcu',
    'name': 'Marvel Cinematic Universe',
    'short_name': 'MCU',
    'tagline': 'One story, many films.',
    'accent': 0xE62429,
    'logo_url': None,
    'backdrop_url': None,
    'sort_order': 0,
    'published': True,
    'synced_at': datetime.datetime(2026, 8, 2, 12, 0, 0),
}

PATHS = [
    {'slug': 'complete', 'name': 'Complete', 'description': 'Everything', 'is_default': True, 'sort_order': 0},
    {'slug': 'essentials', 'name': 'Essentials', 'description': None, 'is_default': False, 'sort_order': 1},
]


def _title(**overrides: Any) -> dict[str, Any]:
    """A watch_titles row as asyncpg would hand it back, with the path-joined importance."""
    row: dict[str, Any] = {
        'id': 1,
        'universe': 'mcu',
        'tmdb_type': 'movie',
        'tmdb_id': 1726,
        'slug': 'iron-man',
        'title': 'Iron Man',
        'kind': 'movie',
        'runtime': 126,
        'episodes': None,
        'release_date': datetime.date(2008, 5, 2),
        'poster_path': '/poster.jpg',
        'backdrop_path': None,
        'overview': 'Tony builds a suit.',
        'tmdb_rating': decimal.Decimal('7.6'),
        'tmdb_votes': 25000,
        'era': 'phase1',
        'era_order': 100,
        'story_order': 100,
        'release_order': 100,
        'milestone': True,
        'instruction': None,
        'context': None,
        'spoiler': None,
        'credits_of': None,
        'sub_universe': None,
        'importance': 'essential',
    }
    row.update(overrides)
    return row


TITLES = [
    _title(),
    _title(
        id=2, tmdb_id=1724, slug='the-incredible-hulk', title='The Incredible Hulk', runtime=112,
        release_date=datetime.date(2008, 6, 12), story_order=200, release_order=200,
        tmdb_rating=None, milestone=False, importance=None,
    ),
    _title(
        id=3, tmdb_type='tv', kind='tv', tmdb_id=88396, slug='loki', title='Loki', runtime=300,
        episodes=6, era='phase4', era_order=400, story_order=300, release_order=300,
        credits_of=1, importance='recommended',
    ),
]

PROVIDERS = [
    {'title_id': 1, 'region': 'DE', 'provider_id': 337, 'provider_name': 'Disney Plus',
     'logo_path': '/d.jpg', 'offer': 'flatrate'},
    {'title_id': 1, 'region': 'DE', 'provider_id': 3, 'provider_name': 'Google Play',
     'logo_path': None, 'offer': 'rent'},
]

PEOPLE = [
    {'tmdb_person_id': 3223, 'name': 'Robert Downey Jr.', 'slug': 'robert-downey-jr',
     'profile_path': '/rdj.jpg', 'updated_at': datetime.datetime(2026, 8, 4, 9, 0, 0)},
    {'tmdb_person_id': 15277, 'name': 'Jon Favreau', 'slug': 'jon-favreau',
     'profile_path': None, 'updated_at': datetime.datetime(2026, 8, 4, 9, 0, 0)},
]

#: ``list_credits`` rows: a credit joined to its person, in the query's (role, billing) order.
CREDITS = [
    {'title_id': 1, 'role': 'cast', 'character_name': 'Tony Stark', 'billing_order': 0,
     **{k: v for k, v in PEOPLE[0].items() if k != 'updated_at'}},
    {'title_id': 1, 'role': 'director', 'character_name': None, 'billing_order': None,
     **{k: v for k, v in PEOPLE[1].items() if k != 'updated_at'}},
]


# -- Payload shaping ----------------------------------------------------------


def test_universe_summary_converts_accent_and_timestamp() -> None:
    payload = universe_summary({**UNIVERSE, 'title_count': 38, 'total_runtime': 5014})
    assert payload['accent'] == '#E62429'
    assert payload['synced_at'] == '2026-08-02T12:00:00'
    assert (payload['title_count'], payload['total_runtime']) == (38, 5014)


def test_title_payload_is_json_safe() -> None:
    payload = title_payload(TITLES[0], providers=PROVIDERS)
    assert payload['release_date'] == '2008-05-02'
    assert payload['tmdb_rating'] == 7.6
    assert isinstance(payload['tmdb_rating'], float)
    assert [p['provider_name'] for p in payload['providers']] == ['Disney Plus', 'Google Play']
    assert 'title_id' not in payload['providers'][0]  # regrouping key is not echoed back


def test_title_payload_tolerates_missing_rating_and_importance() -> None:
    payload = title_payload({k: v for k, v in TITLES[1].items() if k != 'importance'})
    assert payload['tmdb_rating'] is None
    assert payload['importance'] is None
    assert payload['providers'] == []


def test_era_payloads_derive_from_titles_in_order() -> None:
    eras = era_payloads(TITLES)
    assert eras == [
        {'key': 'phase1', 'order': 100, 'title_count': 2},
        {'key': 'phase4', 'order': 400, 'title_count': 1},
    ]


def test_era_payloads_ignore_titles_without_an_era() -> None:
    assert era_payloads([_title(era=None), _title(era='')]) == []


def test_universe_payload_groups_providers_by_title() -> None:
    payload = universe_payload(
        UNIVERSE, PATHS, TITLES, PROVIDERS, path_slug='complete', region='DE', sort='story')
    assert payload['path'] == 'complete'
    assert payload['region'] == 'DE'
    assert [p['slug'] for p in payload['paths']] == ['complete', 'essentials']
    assert len(payload['titles'][0]['providers']) == 2
    assert payload['titles'][1]['providers'] == []  # a title with no rows still carries the key


def test_universe_stats_counts_importance_and_hours() -> None:
    stats = universe_stats(TITLES)
    assert stats['total'] == 3
    assert (stats['movies'], stats['shows']) == (2, 1)
    assert (stats['essential'], stats['recommended'], stats['optional']) == (1, 1, 0)
    assert stats['runtime'] == 126 + 112 + 300
    assert stats['hours'] == round(538 / 60, 1)


# -- Fake repository ----------------------------------------------------------


class FakeWatchlistRepository:
    """Stands in for :class:`WatchlistRepository`, recording the writes it is handed."""

    def __init__(self) -> None:
        self.universes: dict[str, dict[str, Any]] = {'mcu': dict(UNIVERSE)}
        self.progress: dict[int, dict[int, tuple[str, datetime.datetime]]] = {}
        self.prefs: dict[int, dict[str, Any]] = {}
        self.bulk_calls: list[Any] = []
        self.cleared: list[tuple[int, str | None]] = []
        self.upserted_prefs: list[dict[str, Any]] = []

    async def list_universes(self, *, published_only: bool = True) -> list[dict[str, Any]]:
        rows = [{**u, 'title_count': 3, 'total_runtime': 538} for u in self.universes.values()]
        return [r for r in rows if r['published']] if published_only else rows

    async def get_universe(self, slug: str) -> dict[str, Any] | None:
        return self.universes.get(slug)

    async def list_paths(self, universe: str) -> list[dict[str, Any]]:
        return list(PATHS) if universe in self.universes else []

    async def list_titles(
        self, _universe: str, *, path_slug: str | None = None, sort: str = 'story',
    ) -> list[dict[str, Any]]:
        titles = [dict(t) for t in TITLES]
        if path_slug != 'complete':
            for row in titles:
                row['importance'] = None
        titles.sort(key=lambda r: r['story_order' if sort == 'story' else 'release_order'])
        return titles

    async def get_title(self, _universe: str, slug: str) -> dict[str, Any] | None:
        return next((dict(t) for t in TITLES if t['slug'] == slug), None)

    async def list_providers(self, title_ids: Any, region: str) -> list[dict[str, Any]]:
        return [p for p in PROVIDERS if p['title_id'] in set(title_ids) and p['region'] == region]

    async def list_credits(self, title_ids: Any) -> list[dict[str, Any]]:
        return [dict(c) for c in CREDITS if c['title_id'] in set(title_ids)]

    async def get_person(self, slug: str) -> dict[str, Any] | None:
        return next((dict(p) for p in PEOPLE if p['slug'] == slug), None)

    async def list_person_credits(self, person_id: int) -> list[dict[str, Any]]:
        return [
            {**TITLES[0], 'role': credit['role'], 'character_name': credit['character_name'],
             'billing_order': credit['billing_order'], 'universe_name': UNIVERSE['name'],
             'universe_accent': UNIVERSE['accent']}
            for credit in CREDITS if credit['tmdb_person_id'] == person_id
        ]

    async def search_people(self, query: str, *, limit: int = 25) -> list[dict[str, Any]]:
        matches = [p for p in PEOPLE if query.lower() in p['name'].lower()]
        return [{**p, 'score': 0.9, 'credit_count': 1} for p in matches[:limit]]

    async def get_progress(self, user_id: int, *, universe: str | None = None) -> list[dict[str, Any]]:
        return [
            {'user_id': user_id, 'title_id': tid, 'status': status, 'updated_at': at}
            for tid, (status, at) in self.progress.get(user_id, {}).items()
        ]

    async def set_progress_bulk(self, user_id: int, entries: Any) -> None:
        self.bulk_calls.append(list(entries))
        store = self.progress.setdefault(user_id, {})
        for title_id, status, updated_at in entries:
            existing = store.get(title_id)
            if existing is None or existing[1] < updated_at:
                store[title_id] = (status, updated_at)

    async def clear_progress(self, user_id: int, *, universe: str | None = None) -> int:
        self.cleared.append((user_id, universe))
        return len(self.progress.pop(user_id, {}))

    async def get_prefs(self, user_id: int) -> dict[str, Any] | None:
        return self.prefs.get(user_id)

    async def upsert_prefs(self, user_id: int, values: dict[str, Any]) -> dict[str, Any]:
        self.upserted_prefs.append(dict(values))
        row = self.prefs.setdefault(user_id, {
            'region': 'US', 'path_slug': None, 'sort_mode': 'story', 'show_spoiler': False,
            'with_credits': True, 'hide_sub': False, 'weekly_hours': None, 'target_date': None,
        })
        row.update(values)
        return row


class FakeBot:
    def __init__(self, repo: FakeWatchlistRepository) -> None:
        self.db = type('DB', (), {'watchlist': repo})()


@pytest.fixture(autouse=True)
def clear_payload_cache() -> None:
    """Drops every cached catalogue payload between tests.

    The cache keys on ``repr(bot)``, and every fake bot reprs identically, so without this a
    payload built for one test's fake repository would be served to the next.
    """
    api.get_universe_payload.invalidate_containing('get_universe_payload')


@pytest.fixture
def repo() -> FakeWatchlistRepository:
    return FakeWatchlistRepository()


@pytest.fixture
def bot(repo: FakeWatchlistRepository) -> Any:
    return FakeBot(repo)


# -- Handlers -----------------------------------------------------------------


async def test_list_universes_hides_unpublished_by_default(bot: Any, repo: FakeWatchlistRepository) -> None:
    repo.universes['draft'] = {**UNIVERSE, 'slug': 'draft', 'published': False}
    assert [u['slug'] for u in (await api.list_universes(bot))['universes']] == ['mcu']
    everything = await api.list_universes(bot, include_unpublished=True)
    assert {u['slug'] for u in everything['universes']} == {'mcu', 'draft'}


async def test_get_universe_defaults_to_the_default_path(bot: Any) -> None:
    payload = await api.get_universe(bot, 'mcu')
    assert payload['path'] == 'complete'  # PATHS[0].is_default
    assert payload['titles'][0]['importance'] == 'essential'


async def test_get_universe_rejects_an_unknown_path(bot: Any) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.get_universe(bot, 'mcu', path='typo')
    assert exc.value.status_code == 404


async def test_get_universe_404s_an_unknown_universe(bot: Any) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.get_universe(bot, 'nope')
    assert exc.value.status_code == 404


async def test_get_universe_honours_sort_and_region(bot: Any) -> None:
    payload = await api.get_universe(bot, 'mcu', region='de', sort='release')
    assert payload['region'] == 'DE'  # normalised to upper case for the CHAR(2) column
    assert payload['sort'] == 'release'
    assert len(payload['titles'][0]['providers']) == 2

    us = await api.get_universe(bot, 'mcu', region='US')
    assert us['titles'][0]['providers'] == []


async def test_get_universe_rejects_bad_sort_and_region(bot: Any) -> None:
    for kwargs in ({'sort': 'alphabetical'}, {'region': 'USA'}, {'region': '12'}):
        with pytest.raises(HTTPException) as exc:
            await api.get_universe(bot, 'mcu', **kwargs)
        assert exc.value.status_code == 400


async def test_universe_payload_is_cached_between_calls(bot: Any, repo: FakeWatchlistRepository) -> None:
    calls: list[str] = []
    original = repo.list_titles

    async def counting(*args: Any, **kwargs: Any) -> Any:
        calls.append('list_titles')
        return await original(*args, **kwargs)

    repo.list_titles = counting  # type: ignore[method-assign]
    await api.get_universe(bot, 'mcu')
    await api.get_universe(bot, 'mcu')
    assert calls == ['list_titles']

    await api.get_universe(bot, 'mcu', sort='release')  # a different key must miss
    assert len(calls) == 2


async def test_universe_stats_endpoint_reports_the_path(bot: Any) -> None:
    stats = await api.get_universe_statistics(bot, 'mcu', path='essentials')
    assert stats['universe'] == 'mcu'
    assert stats['path'] == 'essentials'
    assert stats['total'] == 3
    assert stats['essential'] == 0  # the fake only tags importance on the 'complete' path


async def test_get_title_returns_neighbours_and_credits_parent(bot: Any) -> None:
    detail = await api.get_title(bot, 'mcu', 'the-incredible-hulk', region='DE')
    assert detail['title']['slug'] == 'the-incredible-hulk'
    assert detail['previous']['slug'] == 'iron-man'
    assert detail['next']['slug'] == 'loki'
    assert detail['credits_of'] is None

    loki = await api.get_title(bot, 'mcu', 'loki')
    assert loki['credits_of']['slug'] == 'iron-man'  # credits_of = 1
    assert loki['next'] is None


async def test_get_title_404s_an_unknown_slug(bot: Any) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.get_title(bot, 'mcu', 'not-a-film')
    assert exc.value.status_code == 404


# -- People -------------------------------------------------------------------


def test_credit_payloads_keep_query_order_and_are_flat() -> None:
    payloads = credit_payloads(CREDITS)
    assert [row['role'] for row in payloads] == ['cast', 'director']
    assert payloads[0]['character_name'] == 'Tony Stark'
    assert payloads[1]['billing_order'] is None
    assert 'title_id' not in payloads[0]  # the caller already knows which title it asked for


def test_person_payload_nests_a_json_safe_title() -> None:
    credits = [
        {**TITLES[0], 'role': 'cast', 'character_name': 'Tony Stark', 'billing_order': 0,
         'universe_name': 'Marvel Cinematic Universe', 'universe_accent': 0xE62429},
    ]
    payload = person_payload(PEOPLE[0], credits)
    assert payload['person']['slug'] == 'robert-downey-jr'
    assert payload['person']['updated_at'] == '2026-08-04T09:00:00'
    entry = payload['credits'][0]
    assert entry['universe_accent'] == '#E62429'
    assert entry['title']['release_date'] == '2008-05-02'
    assert isinstance(entry['title']['tmdb_rating'], float)


def test_person_summary_is_autocomplete_sized() -> None:
    summary = person_summary({**PEOPLE[0], 'score': 0.9, 'credit_count': 4})
    assert summary == {
        'tmdb_person_id': 3223, 'name': 'Robert Downey Jr.', 'slug': 'robert-downey-jr',
        'profile_path': '/rdj.jpg', 'credit_count': 4,
    }


async def test_get_title_carries_its_credits(bot: Any) -> None:
    detail = await api.get_title(bot, 'mcu', 'iron-man')
    assert [(c['name'], c['role']) for c in detail['credits']] == [
        ('Robert Downey Jr.', 'cast'), ('Jon Favreau', 'director'),
    ]


async def test_get_person_returns_the_filmography(bot: Any) -> None:
    payload = await api.get_person(bot, 'robert-downey-jr')
    assert payload['person']['name'] == 'Robert Downey Jr.'
    assert payload['credits'][0]['title']['slug'] == 'iron-man'
    assert payload['credits'][0]['role'] == 'cast'


async def test_get_person_404s_an_unknown_slug(bot: Any) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.get_person(bot, 'nobody')
    assert exc.value.status_code == 404


async def test_search_people_returns_summaries(bot: Any) -> None:
    payload = await api.search_people(bot, 'downey')
    assert [row['slug'] for row in payload['people']] == ['robert-downey-jr']
    assert payload['people'][0]['credit_count'] == 1


# -- Progress / prefs ---------------------------------------------------------


async def test_put_progress_writes_naive_utc(bot: Any, repo: FakeWatchlistRepository) -> None:
    body = api.ProgressBody(entries=[
        api.ProgressEntry(title_id=1, status='watched',
                          updated_at=datetime.datetime(2026, 8, 2, 14, 0, tzinfo=datetime.UTC)),
    ])
    result = await api.put_progress(bot, 42, body)

    (title_id, status, updated_at), = repo.bulk_calls[0]
    assert (title_id, status) == (1, 'watched')
    assert updated_at.tzinfo is None, "watch_progress.updated_at is a naive TIMESTAMP"
    assert updated_at == datetime.datetime(2026, 8, 2, 14, 0)
    assert result['entries']['1']['status'] == 'watched'


async def test_put_progress_converts_a_non_utc_offset(bot: Any, repo: FakeWatchlistRepository) -> None:
    plus_two = datetime.timezone(datetime.timedelta(hours=2))
    body = api.ProgressBody(entries=[
        api.ProgressEntry(title_id=1, status='skipped',
                          updated_at=datetime.datetime(2026, 8, 2, 16, 0, tzinfo=plus_two)),
    ])
    await api.put_progress(bot, 42, body)
    assert repo.bulk_calls[0][0][2] == datetime.datetime(2026, 8, 2, 14, 0)


async def test_put_progress_defaults_a_missing_timestamp_to_now(bot: Any, repo: FakeWatchlistRepository) -> None:
    body = api.ProgressBody(entries=[api.ProgressEntry(title_id=1, status='watched')])
    await api.put_progress(bot, 42, body)
    stamped = repo.bulk_calls[0][0][2]
    assert stamped.tzinfo is None
    assert abs(stamped - datetime.datetime.now(datetime.UTC).replace(tzinfo=None)) < datetime.timedelta(minutes=1)


async def test_put_progress_rejects_an_unknown_status(bot: Any, repo: FakeWatchlistRepository) -> None:
    body = api.ProgressBody(entries=[
        api.ProgressEntry(title_id=1, status='watched'),
        api.ProgressEntry(title_id=2, status='pending'),
    ])
    with pytest.raises(HTTPException) as exc:
        await api.put_progress(bot, 42, body)
    assert exc.value.status_code == 400
    assert repo.bulk_calls == [], "the whole batch is rejected before any write"


async def test_delete_progress_scopes_to_a_universe(bot: Any, repo: FakeWatchlistRepository) -> None:
    repo.progress[42] = {1: ('watched', datetime.datetime(2026, 8, 2))}
    assert await api.delete_progress(bot, 42, universe='mcu') == {'deleted': 1}
    assert repo.cleared == [(42, 'mcu')]


async def test_get_prefs_returns_defaults_for_a_new_user(bot: Any) -> None:
    prefs = await api.get_prefs(bot, 42)
    assert prefs == {
        'region': 'US', 'path_slug': None, 'sort_mode': 'story', 'show_spoiler': False,
        'with_credits': True, 'hide_sub': False, 'weekly_hours': None, 'target_date': None,
    }


async def test_patch_prefs_only_sends_the_given_fields(bot: Any, repo: FakeWatchlistRepository) -> None:
    prefs = await api.patch_prefs(bot, 42, api.PrefsBody(region='de', show_spoiler=True))
    assert repo.upserted_prefs == [{'region': 'DE', 'show_spoiler': True}]
    assert prefs['region'] == 'DE'
    assert prefs['sort_mode'] == 'story'  # untouched


async def test_patch_prefs_rejects_bad_values(bot: Any, repo: FakeWatchlistRepository) -> None:
    for body in (api.PrefsBody(region='DEU'), api.PrefsBody(sort_mode='alphabetical')):
        with pytest.raises(HTTPException) as exc:
            await api.patch_prefs(bot, 42, body)
        assert exc.value.status_code == 400
    assert repo.upserted_prefs == []


async def test_patch_prefs_serialises_a_target_date(bot: Any) -> None:
    prefs = await api.patch_prefs(bot, 42, api.PrefsBody(target_date=datetime.date(2026, 12, 24)))
    assert prefs['target_date'] == '2026-12-24'
