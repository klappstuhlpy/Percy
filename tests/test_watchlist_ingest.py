"""Tests for the watchlist seed format and TMDB ingest service.

A fake TMDB client (canned payloads / queued errors) and a fake watchlist repository
(records every call) stand in for the real network/DB layers, mirroring the pattern used for
:class:`~app.services.ai.AIService` in ``tests/test_ai_service.py``.
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Any

import asyncpg
import pytest

from app.clients.base import HTTPClientError
from app.services.watchlist import (
    DISCOVER_ORDER_BASE,
    DISCOVER_ORDER_STEP,
    DISCOVER_PAGE_CAP,
    DiscoverSeed,
    EraSeed,
    PathSeed,
    SeedError,
    TitleSeed,
    UniverseSeed,
    WatchlistIngest,
    accent_to_int,
    load_seed,
    movie_row,
    provider_rows,
    slugify,
    tv_row,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


# -- fakes ----------------------------------------------------------------------------------


class _FakeTMDBError(HTTPClientError):
    """A minimal ``HTTPClientError`` stand-in that doesn't need a real aiohttp response."""

    def __init__(self, message: str = 'boom') -> None:  # no super().__init__: no real response to build one from
        self._message = message

    def __str__(self) -> str:
        return self._message


class FakeTMDBClient:
    """Stand-in for :class:`~app.clients.tmdb.TMDBClient`: canned payloads, queued errors."""

    def __init__(
        self,
        *,
        movies: dict[int, Any] | None = None,
        tv: dict[int, Any] | None = None,
        seasons: dict[tuple[int, int], Any] | None = None,
        providers: dict[tuple[str, int], Any] | None = None,
        credits: dict[tuple[str, int], Any] | None = None,
        videos: dict[tuple[str, int], Any] | None = None,
        discover: dict[tuple[str, int], list[list[dict[str, Any]]]] | None = None,
        collections: dict[int, Any] | None = None,
        errors: dict[tuple[str, int], BaseException] | None = None,
    ) -> None:
        self._videos = videos or {}
        # Keyed (kind, source_id) -> one list of results per TMDB page.
        self._discover = discover or {}
        self._collections = collections or {}
        self._movies = movies or {}
        self._tv = tv or {}
        self._seasons = seasons or {}
        self._providers = providers or {}
        self._credits = credits or {}
        self._errors = errors or {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def movie(self, movie_id: int) -> Any:
        self.calls.append(('movie', (movie_id,)))
        if ('movie', movie_id) in self._errors:
            raise self._errors[('movie', movie_id)]
        return self._movies[movie_id]

    async def tv(self, tv_id: int) -> Any:
        self.calls.append(('tv', (tv_id,)))
        if ('tv', tv_id) in self._errors:
            raise self._errors[('tv', tv_id)]
        return self._tv[tv_id]

    async def tv_season(self, tv_id: int, season: int) -> Any:
        self.calls.append(('tv_season', (tv_id, season)))
        return self._seasons[(tv_id, season)]

    async def discover(
        self, kind: str, *, keyword: int | None = None, company: int | None = None, page: int = 1,
    ) -> Any:
        self.calls.append(('discover', (kind, keyword, company, page)))
        source_id = keyword if keyword is not None else company
        if ('discover', source_id) in self._errors:
            raise self._errors[('discover', source_id)]
        pages = self._discover.get((kind, source_id or 0), [])
        results = pages[page - 1] if 1 <= page <= len(pages) else []
        return {'page': page, 'results': results, 'total_pages': len(pages)}

    async def collection(self, collection_id: int) -> Any:
        self.calls.append(('collection', (collection_id,)))
        if ('collection', collection_id) in self._errors:
            raise self._errors[('collection', collection_id)]
        return self._collections[collection_id]

    async def watch_providers(self, kind: str, item_id: int) -> Any:
        self.calls.append(('watch_providers', (kind, item_id)))
        return self._providers.get((kind, item_id), {'results': {}})

    async def credits(self, kind: str, item_id: int) -> Any:
        self.calls.append(('credits', (kind, item_id)))
        # Keyed apart from the movie/tv errors so a test can fail *only* the credits fetch.
        if ('credits', item_id) in self._errors:
            raise self._errors[('credits', item_id)]
        return self._credits.get((kind, item_id), {'cast': [], 'crew': []})

    async def videos(self, kind: str, item_id: int) -> Any:
        self.calls.append(('videos', (kind, item_id)))
        # Keyed apart again, so a test can fail *only* the videos fetch.
        if ('videos', item_id) in self._errors:
            raise self._errors[('videos', item_id)]
        return self._videos.get((kind, item_id), {'results': []})


class FakeWatchlistRepository:
    """Stand-in for :class:`~app.database.repositories.watchlist.WatchlistRepository`."""

    def __init__(
        self,
        existing_titles: list[dict[str, Any]] | None = None,
        *,
        upsert_conflicts: set[tuple[str, int]] | None = None,
    ) -> None:
        self._existing_titles = existing_titles or []
        self._next_id = 1000
        self._upsert_conflicts = upsert_conflicts or set()
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.importance: list[tuple[int, int, str]] = []
        self.pruned_importance_with: list[tuple[int, tuple[int, ...]]] = []
        self.providers: dict[tuple[int, str], list[dict[str, Any]]] = {}
        self.credits_of: dict[int, int | None] = {}
        self.pruned_with: list[int] | None = None
        self.people: dict[int, dict[str, Any]] = {}
        self.credits: dict[int, list[dict[str, Any]]] = {}
        self.trailers: dict[int, dict[str, Any]] = {}
        self.people_pruned = 0

    async def list_titles(self, universe: str, *, path_slug: str | None = None, sort: str = 'story') -> list[dict[str, Any]]:
        self.calls.append(('list_titles', (universe,)))
        return list(self._existing_titles)

    async def upsert_universe(self, data: dict[str, Any]) -> None:
        self.calls.append(('upsert_universe', (data,)))

    async def upsert_path(self, universe: str, data: dict[str, Any]) -> int:
        self.calls.append(('upsert_path', (universe, data)))
        self._next_id += 1
        return self._next_id

    async def upsert_title(self, universe: str, data: dict[str, Any]) -> int:
        self.calls.append(('upsert_title', (universe, data)))
        key = (data['tmdb_type'], data['tmdb_id'])
        if key in self._upsert_conflicts:
            raise asyncpg.UniqueViolationError(
                f'duplicate key value violates unique constraint "watch_titles_universe_slug_key": {key}'
            )
        self._next_id += 1
        return self._next_id

    async def set_importance(self, title_id: int, path_id: int, importance: str) -> None:
        self.calls.append(('set_importance', (title_id, path_id, importance)))
        self.importance.append((title_id, path_id, importance))

    async def prune_importance(self, title_id: int, keep_path_ids: Sequence[int]) -> int:
        self.calls.append(('prune_importance', (title_id, tuple(keep_path_ids))))
        self.pruned_importance_with.append((title_id, tuple(keep_path_ids)))
        before = len(self.importance)
        self.importance = [
            entry for entry in self.importance if entry[0] != title_id or entry[1] in keep_path_ids
        ]
        return before - len(self.importance)

    async def replace_providers(self, title_id: int, region: str, rows: Sequence[dict[str, Any]]) -> None:
        self.calls.append(('replace_providers', (title_id, region, rows)))
        self.providers[(title_id, region)] = list(rows)

    async def prune_titles(self, universe: str, keep_ids: Sequence[int]) -> int:
        self.calls.append(('prune_titles', (universe, tuple(keep_ids))))
        self.pruned_with = list(keep_ids)
        return 0

    async def set_credits_of(self, title_id: int, credits_of_id: int | None) -> None:
        self.calls.append(('set_credits_of', (title_id, credits_of_id)))
        self.credits_of[title_id] = credits_of_id

    async def upsert_person(self, row: dict[str, Any]) -> int:
        self.calls.append(('upsert_person', (row['tmdb_person_id'],)))
        self.people[row['tmdb_person_id']] = dict(row)
        return row['tmdb_person_id']

    async def replace_credits(self, title_id: int, credits: Sequence[dict[str, Any]]) -> None:
        self.calls.append(('replace_credits', (title_id, len(credits))))
        self.credits[title_id] = [dict(row) for row in credits]

    async def set_trailer(self, title_id: int, trailer: dict[str, Any]) -> None:
        self.calls.append(('set_trailer', (title_id, trailer)))
        self.trailers[title_id] = dict(trailer)

    async def prune_people(self) -> int:
        self.calls.append(('prune_people', ()))
        self.people_pruned += 1
        return 0


def _title(**overrides: Any) -> TitleSeed:
    base: dict[str, Any] = {'tmdb_type': 'movie', 'tmdb_id': 1, 'era': 'p1', 'story': 1, 'release': 1}
    base.update(overrides)
    return TitleSeed(**base)


def _seed(
    titles: tuple[TitleSeed, ...],
    paths: tuple[PathSeed, ...] = (),
    discover: tuple[DiscoverSeed, ...] = (),
) -> UniverseSeed:
    return UniverseSeed(
        slug='mcu', name='MCU', short_name='MCU', paths=paths,
        eras=(EraSeed(key='p1', name='Phase 1', order=100),), titles=titles, discover=discover,
    )


def _write_seed(tmp_path: Path, content: str, name: str = 'seed.toml') -> Path:
    path = tmp_path / name
    path.write_text(content, encoding='utf-8')
    return path


# -- slugify ----------------------------------------------------------------------------------


@pytest.mark.parametrize(('title', 'expected'), [
    ('Avengers: Endgame', 'avengers-endgame'),
    ('WandaVision', 'wandavision'),
    ("Marvel's Daredevil", 'marvels-daredevil'),
    ('Spider-Man: No Way Home', 'spider-man-no-way-home'),
    ('Pokémon', 'pokemon'),
])
def test_slugify(title: str, expected: str) -> None:
    assert slugify(title) == expected


def test_slugify_can_return_empty_for_no_ascii_content() -> None:
    # slugify() itself may return "" here; movie_row/tv_row fall back to str(tmdb_id) so the
    # stored slug is never empty.
    assert slugify('???') == ''


# -- accent_to_int ------------------------------------------------------------------------------


def test_accent_to_int() -> None:
    assert accent_to_int('#E62429') == 15082537


# -- seed validation ------------------------------------------------------------------------------


_VALID_SEED = """
slug = "mcu"
name = "Marvel Cinematic Universe"
short_name = "MCU"

[[paths]]
slug = "complete"
name = "The Complete Story"
default = true

[[eras]]
key = "phase1"
name = "Phase 1"
order = 100

[[titles]]
tmdb_type = "movie"
tmdb_id = 1771
era = "phase1"
story = 100
release = 100
importance = { complete = "essential" }
"""

_MISSING_REQUIRED = """
name = "X"
short_name = "X"
"""

_DUP_TITLE_ID = """
slug = "u"
name = "U"
short_name = "U"

[[eras]]
key = "e1"
name = "E1"

[[titles]]
tmdb_type = "movie"
tmdb_id = 1
era = "e1"
story = 1
release = 1

[[titles]]
tmdb_type = "movie"
tmdb_id = 1
era = "e1"
story = 2
release = 2
"""

_DUP_PATH_SLUG = """
slug = "u"
name = "U"
short_name = "U"

[[paths]]
slug = "p"
name = "P1"

[[paths]]
slug = "p"
name = "P2"
"""

_DUP_ERA_KEY = """
slug = "u"
name = "U"
short_name = "U"

[[eras]]
key = "e"
name = "E1"

[[eras]]
key = "e"
name = "E2"
"""

_UNDECLARED_ERA = """
slug = "u"
name = "U"
short_name = "U"

[[titles]]
tmdb_type = "movie"
tmdb_id = 1
era = "nope"
story = 1
release = 1
"""

_UNDECLARED_IMPORTANCE_PATH = """
slug = "u"
name = "U"
short_name = "U"

[[eras]]
key = "e"
name = "E"

[[titles]]
tmdb_type = "movie"
tmdb_id = 1
era = "e"
story = 1
release = 1
importance = { nopath = "essential" }
"""

_BAD_IMPORTANCE_VALUE = """
slug = "u"
name = "U"
short_name = "U"

[[paths]]
slug = "p"
name = "P"

[[eras]]
key = "e"
name = "E"

[[titles]]
tmdb_type = "movie"
tmdb_id = 1
era = "e"
story = 1
release = 1
importance = { p = "super-essential" }
"""

_BAD_TMDB_TYPE = """
slug = "u"
name = "U"
short_name = "U"

[[eras]]
key = "e"
name = "E"

[[titles]]
tmdb_type = "book"
tmdb_id = 1
era = "e"
story = 1
release = 1
"""

_BAD_KIND = """
slug = "u"
name = "U"
short_name = "U"

[[eras]]
key = "e"
name = "E"

[[titles]]
tmdb_type = "movie"
tmdb_id = 1
era = "e"
story = 1
release = 1
kind = "documentary"
"""

_SEASONS_ON_MOVIE = """
slug = "u"
name = "U"
short_name = "U"

[[eras]]
key = "e"
name = "E"

[[titles]]
tmdb_type = "movie"
tmdb_id = 1
era = "e"
story = 1
release = 1
seasons = [1]
"""

_BAD_CREDITS_OF = """
slug = "u"
name = "U"
short_name = "U"

[[eras]]
key = "e"
name = "E"

[[titles]]
tmdb_type = "movie"
tmdb_id = 1
era = "e"
story = 1
release = 1
credits_of = 999
"""

_MULTIPLE_DEFAULT_PATHS = """
slug = "u"
name = "U"
short_name = "U"

[[paths]]
slug = "a"
name = "A"
default = true

[[paths]]
slug = "b"
name = "B"
default = true
"""

_BAD_ACCENT = """
slug = "u"
name = "U"
short_name = "U"
accent = "red"
"""

_NON_INT_STORY = """
slug = "u"
name = "U"
short_name = "U"

[[eras]]
key = "e"
name = "E"

[[titles]]
tmdb_type = "movie"
tmdb_id = 1
era = "e"
story = "100"
release = 1
"""

_BOOL_STORY_REJECTED_AS_NON_INT = """
slug = "u"
name = "U"
short_name = "U"

[[eras]]
key = "e"
name = "E"

[[titles]]
tmdb_type = "movie"
tmdb_id = 1
era = "e"
story = true
release = 1
"""


_VALID_DISCOVER = """
slug = "u"
name = "U"
short_name = "U"

[[paths]]
slug = "complete"
name = "Complete"

[[eras]]
key = "phase4"
name = "Phase 4"

[[discover]]
kind = "movie"
keyword = 180547
era = "phase4"
importance = "recommended"
min_date = "2008-01-01"
max_date = "2030-12-31"
exclude = [12345, 67890]
"""

_DISCOVER_HEAD = """
slug = "u"
name = "U"
short_name = "U"

[[eras]]
key = "e"
name = "E"

[[discover]]
"""

_DISCOVER_BAD_KIND = _DISCOVER_HEAD + 'kind = "book"\nkeyword = 1\n'
_DISCOVER_NO_SOURCE = _DISCOVER_HEAD + 'kind = "movie"\n'
_DISCOVER_TWO_SOURCES = _DISCOVER_HEAD + 'kind = "movie"\nkeyword = 1\ncompany = 2\n'
_DISCOVER_NEGATIVE_SOURCE = _DISCOVER_HEAD + 'kind = "movie"\ncompany = -5\n'
_DISCOVER_NON_INT_SOURCE = _DISCOVER_HEAD + 'kind = "movie"\ncollection = "86311"\n'
_DISCOVER_UNDECLARED_ERA = _DISCOVER_HEAD + 'kind = "tv"\nkeyword = 1\nera = "nope"\n'
_DISCOVER_BAD_IMPORTANCE = _DISCOVER_HEAD + 'kind = "movie"\nkeyword = 1\nimportance = "vital"\n'
_DISCOVER_BAD_DATE = _DISCOVER_HEAD + 'kind = "movie"\nkeyword = 1\nmin_date = "yesterday"\n'
_DISCOVER_BAD_EXCLUDE = _DISCOVER_HEAD + 'kind = "movie"\nkeyword = 1\nexclude = ["12345"]\n'


def test_load_seed_accepts_a_discover_block(tmp_path: Path) -> None:
    seed = load_seed(_write_seed(tmp_path, _VALID_DISCOVER))

    assert len(seed.discover) == 1
    block = seed.discover[0]
    assert block.kind == 'movie'
    assert block.source == ('keyword', 180547)
    assert (block.company, block.collection) == (None, None)
    assert block.era == 'phase4'
    assert block.importance == 'recommended'
    assert block.min_date == datetime.date(2008, 1, 1)
    assert block.max_date == datetime.date(2030, 12, 31)
    assert block.exclude == frozenset({12345, 67890})


def test_load_seed_defaults_a_discover_block_to_optional_and_no_window(tmp_path: Path) -> None:
    # Everything but kind + one source is optional, and the defaults must be the harmless ones:
    # no era, no date window, no exclusions, and the weakest importance.
    seed = load_seed(_write_seed(tmp_path, _DISCOVER_HEAD + 'kind = "tv"\ncompany = 420\n'))

    block = seed.discover[0]
    assert block.source == ('company', 420)
    assert (block.era, block.min_date, block.max_date) == (None, None, None)
    assert block.importance == 'optional'
    assert block.exclude == frozenset()


@pytest.mark.parametrize(('toml_text', 'expected_fragment'), [
    (_DISCOVER_BAD_KIND, 'discover[0] has invalid kind'),
    (_DISCOVER_NO_SOURCE, 'must set exactly one of keyword/company/collection'),
    (_DISCOVER_TWO_SOURCES, 'must set exactly one of keyword/company/collection'),
    (_DISCOVER_NEGATIVE_SOURCE, 'has invalid company -5'),
    (_DISCOVER_NON_INT_SOURCE, "has invalid collection '86311'"),
    (_DISCOVER_UNDECLARED_ERA, 'discover[0] references undeclared era'),
    (_DISCOVER_BAD_IMPORTANCE, 'discover[0] has invalid importance'),
    (_DISCOVER_BAD_DATE, 'has invalid min_date'),
    (_DISCOVER_BAD_EXCLUDE, 'must be a list of integer tmdb_ids'),
], ids=[
    'bad-kind', 'no-source', 'two-sources', 'negative-source', 'non-int-source',
    'undeclared-era', 'bad-importance', 'bad-date', 'non-int-exclude',
])
def test_discover_validation_rejects(tmp_path: Path, toml_text: str, expected_fragment: str) -> None:
    with pytest.raises(SeedError) as exc_info:
        load_seed(_write_seed(tmp_path, toml_text))
    assert any(expected_fragment in problem for problem in exc_info.value.problems)


def test_discover_seed_source_raises_when_none_is_set() -> None:
    # Regression: a hand-built DiscoverSeed (unreachable via load_seed, but DiscoverSeed is
    # exported and the tests construct it directly) with no source used to return the sentinel
    # ('?', 0) instead of raising. _discover_block would then send both filters as None,
    # silently discovering the 200 oldest titles on TMDB. It must raise instead.
    block = DiscoverSeed(kind='movie')
    with pytest.raises(ValueError, match='no source set'):
        _ = block.source


def test_load_seed_valid(tmp_path: Path) -> None:
    path = _write_seed(tmp_path, _VALID_SEED)
    seed = load_seed(path)
    assert seed.slug == 'mcu'
    assert len(seed.paths) == 1
    assert len(seed.eras) == 1
    assert len(seed.titles) == 1
    assert seed.titles[0].importance == {'complete': 'essential'}
    assert seed.discover == ()  # a seed with no [[discover]] block parses exactly as before


@pytest.mark.parametrize(('toml_text', 'expected_fragment'), [
    (_MISSING_REQUIRED, 'missing required field'),
    (_DUP_TITLE_ID, 'duplicate title'),
    (_DUP_PATH_SLUG, 'duplicate path slug'),
    (_DUP_ERA_KEY, 'duplicate era key'),
    (_UNDECLARED_ERA, 'undeclared era'),
    (_UNDECLARED_IMPORTANCE_PATH, 'undeclared path'),
    (_BAD_IMPORTANCE_VALUE, 'importance'),
    (_BAD_TMDB_TYPE, 'invalid tmdb_type'),
    (_BAD_KIND, 'invalid kind'),
    (_SEASONS_ON_MOVIE, '"seasons"'),
    (_BAD_CREDITS_OF, 'credits_of'),
    (_MULTIPLE_DEFAULT_PATHS, 'more than one path'),
    (_BAD_ACCENT, 'hex string'),
    (_NON_INT_STORY, 'non-integer'),
    (_BOOL_STORY_REJECTED_AS_NON_INT, 'non-integer'),
], ids=[
    'missing-required', 'duplicate-title-id', 'duplicate-path-slug', 'duplicate-era-key',
    'undeclared-era', 'undeclared-importance-path', 'bad-importance-value', 'bad-tmdb-type',
    'bad-kind', 'seasons-on-movie', 'bad-credits-of', 'multiple-default-paths', 'bad-accent',
    'non-integer-story-string', 'non-integer-story-bool',
])
def test_seed_validation_rejects(tmp_path: Path, toml_text: str, expected_fragment: str) -> None:
    path = _write_seed(tmp_path, toml_text)
    with pytest.raises(SeedError) as exc_info:
        load_seed(path)
    assert any(expected_fragment in problem for problem in exc_info.value.problems)


# -- movie_row / tv_row ------------------------------------------------------------------------


def test_movie_row_maps_curation_and_factual_columns() -> None:
    seed_title = _title(tmdb_type='movie', tmdb_id=42, era='p1', story=7, release=9, milestone=True)
    payload = {
        'title': 'Test Movie', 'runtime': 120, 'release_date': '2020-01-02',
        'vote_average': 8.1, 'vote_count': 500,
    }
    row = movie_row(seed_title, payload, universe='u', era_order=3)

    assert row['title'] == 'Test Movie'
    assert row['slug'] == 'test-movie'
    assert row['runtime'] == 120
    assert row['story_order'] == 7
    assert row['release_order'] == 9
    assert row['era_order'] == 3
    assert row['milestone'] is True
    assert row['tmdb_id'] == 42
    assert row['tmdb_type'] == 'movie'


def test_tv_row_sums_runtime_across_seeded_seasons() -> None:
    seed_title = _title(tmdb_type='tv', tmdb_id=1, seasons=(1, 2))
    payload = {'name': 'Show', 'episode_run_time': [30]}
    seasons = {
        1: {'episodes': [{'runtime': 40}, {'runtime': 45}]},
        2: {'episodes': [{'runtime': 42}]},
    }
    row = tv_row(seed_title, payload, seasons, universe='u', era_order=5)

    assert row['runtime'] == 40 + 45 + 42
    assert row['episodes'] == 3
    assert row['era_order'] == 5
    assert '_warnings' not in row


def test_tv_row_falls_back_to_show_episode_run_time() -> None:
    seed_title = _title(tmdb_type='tv', tmdb_id=1, seasons=(1,))
    payload = {'name': 'Show', 'episode_run_time': [25]}
    seasons = {1: {'episodes': [{'runtime': None}, {'runtime': 30}]}}
    row = tv_row(seed_title, payload, seasons, universe='u', era_order=0)

    assert row['runtime'] == 25 + 30


def test_tv_row_defaults_to_zero_with_warning_when_no_fallback_available() -> None:
    seed_title = _title(tmdb_type='tv', tmdb_id=1, seasons=(1,))
    payload = {'name': 'Show'}
    seasons = {1: {'episodes': [{'runtime': None}]}}
    row = tv_row(seed_title, payload, seasons, universe='u', era_order=0)

    assert row['runtime'] == 0
    assert row['_warnings']


def test_tv_row_excludes_season_zero_when_seasons_absent() -> None:
    seed_title = _title(tmdb_type='tv', tmdb_id=1, seasons=None)
    payload = {'name': 'Show'}
    seasons = {
        0: {'episodes': [{'runtime': 10}]},
        1: {'episodes': [{'runtime': 20}]},
    }
    row = tv_row(seed_title, payload, seasons, universe='u', era_order=0)

    assert row['runtime'] == 20
    assert row['episodes'] == 1


# -- provider_rows ------------------------------------------------------------------------------


def test_provider_rows_missing_region_returns_empty() -> None:
    assert provider_rows({'results': {}}, 'US') == []


def test_provider_rows_flattens_all_buckets() -> None:
    payload = {
        'results': {
            'US': {
                'flatrate': [{'provider_id': 8, 'provider_name': 'Netflix', 'logo_path': '/n.jpg'}],
                'rent': [{'provider_id': 2, 'provider_name': 'Apple TV', 'logo_path': None}],
                'buy': [{'provider_id': 2, 'provider_name': 'Apple TV', 'logo_path': None}],
            },
        },
    }
    rows = provider_rows(payload, 'US')

    assert len(rows) == 3
    assert {row['offer'] for row in rows} == {'flatrate', 'rent', 'buy'}
    assert rows[0]['provider_id'] == 8


# -- WatchlistIngest.sync -----------------------------------------------------------------------


async def test_sync_two_titles_upserts_and_sets_importance() -> None:
    path = PathSeed(slug='complete', name='Complete', default=True)
    t1 = _title(tmdb_id=1, importance={'complete': 'essential'})
    t2 = _title(tmdb_id=2, story=2, release=2, importance={'complete': 'recommended'})
    seed = _seed((t1, t2), paths=(path,))

    tmdb = FakeTMDBClient(movies={1: {'title': 'One', 'runtime': 100}, 2: {'title': 'Two', 'runtime': 110}})
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',))

    upsert_titles = [call for call in repo.calls if call[0] == 'upsert_title']
    assert len(upsert_titles) == 2
    assert report.titles_seen == 2
    assert report.titles_written == 2
    assert not report.warnings
    assert len(repo.importance) == 2
    assert {level for _, _, level in repo.importance} == {'essential', 'recommended'}
    assert repo.pruned_with is not None
    assert len(repo.pruned_with) == 2


async def test_sync_tmdb_failure_skips_title_and_keeps_existing_row() -> None:
    good = _title(tmdb_id=1)
    bad = _title(tmdb_id=2, story=2, release=2)
    seed = _seed((good, bad))

    tmdb = FakeTMDBClient(
        movies={1: {'title': 'One', 'runtime': 100}},
        errors={('movie', 2): _FakeTMDBError('tmdb is down')},
    )
    # title 2 was already synced by a previous run and lives at id 555 -- a transient TMDB
    # failure on this run must not let prune_titles delete it.
    repo = FakeWatchlistRepository(existing_titles=[{'tmdb_type': 'movie', 'tmdb_id': 2, 'id': 555}])
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',))

    assert report.titles_seen == 2
    assert report.titles_written == 1
    assert any('tmdb is down' in warning for warning in report.warnings)
    assert repo.pruned_with is not None
    assert 555 in repo.pruned_with


async def test_sync_dry_run_performs_no_writes() -> None:
    t1 = _title(tmdb_id=1)
    seed = _seed((t1,), paths=(PathSeed(slug='complete', name='Complete', default=True),))

    tmdb = FakeTMDBClient(movies={1: {'title': 'One', 'runtime': 100}})
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',), dry_run=True)

    write_call_names = {call[0] for call in repo.calls} - {'list_titles'}
    assert write_call_names == set()
    assert repo.pruned_with is None
    assert not repo.importance
    assert report.dry_run is True
    assert report.titles_written == 1


async def test_sync_resolves_credits_of_to_the_upserted_titles_row_id() -> None:
    # child.credits_of names the *parent's tmdb_id* (1) -- set_credits_of must be called with
    # the parent's resolved watch_titles.id, not 1 itself, and only after both are upserted
    # (the "second pass" the credits_of resolution needs, since the parent's row id doesn't
    # exist yet when the child itself is upserted).
    parent = _title(tmdb_id=1)
    child = _title(tmdb_id=2, story=2, release=2, credits_of=1)
    seed = _seed((parent, child))

    tmdb = FakeTMDBClient(movies={1: {'title': 'One', 'runtime': 100}, 2: {'title': 'Two', 'runtime': 10}})
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    await ingest.sync(seed, regions=('US',))

    call_names = [call[0] for call in repo.calls]
    upsert_indices = [i for i, name in enumerate(call_names) if name == 'upsert_title']
    assert len(upsert_indices) == 2
    assert call_names.index('set_credits_of') > max(upsert_indices)

    # FakeWatchlistRepository.upsert_title returns incrementing ids in call order; this seed
    # has no paths, so parent (upserted first) gets 1001 and child (upserted second) gets 1002.
    # The parent itself has no credits_of, so it also gets an explicit clear (I3) -- a seed is
    # authoritative for the column's absence too.
    assert repo.credits_of == {1002: 1001, 1001: None}


async def test_sync_clears_credits_of_when_seed_no_longer_sets_one() -> None:
    # A title with no credits_of in the seed must still get set_credits_of(id, None) -- covers
    # the case where a previous seed/run had set one and a later re-sync drops it. The repo is
    # pre-seeded with a *previously set* value so this asserts the flip 77 -> None, not merely
    # that None is written for a title that never had one.
    t1 = _title(tmdb_id=1)
    seed = _seed((t1,))

    tmdb = FakeTMDBClient(movies={1: {'title': 'One', 'runtime': 100}})
    repo = FakeWatchlistRepository()
    repo.credits_of[1001] = 77  # the id upsert_title will hand back for this title

    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    await ingest.sync(seed, regions=('US',))

    title_id = repo.pruned_with[0]
    assert title_id == 1001
    assert repo.credits_of == {title_id: None}


async def test_sync_prunes_importance_for_paths_no_longer_seeded() -> None:
    # A title's importance map only names 'complete' -- prune_importance must be called with
    # exactly that path's resolved id, so a later drop of 'essentials' (or 'complete' itself)
    # from the seed clears the stale watch_title_importance row rather than leaving it forever.
    complete = PathSeed(slug='complete', name='Complete', default=True)
    essentials = PathSeed(slug='essentials', name='Essentials')
    t1 = _title(tmdb_id=1, importance={'complete': 'essential'})
    seed = _seed((t1,), paths=(complete, essentials))

    tmdb = FakeTMDBClient(movies={1: {'title': 'One', 'runtime': 100}})
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    await ingest.sync(seed, regions=('US',))

    # FakeWatchlistRepository.upsert_path/upsert_title return incrementing ids in call order:
    # 'complete' (1001), 'essentials' (1002), then the title itself (1003).
    assert len(repo.pruned_importance_with) == 1
    title_id, kept_path_ids = repo.pruned_importance_with[0]
    assert title_id == 1003
    assert kept_path_ids == (1001,)  # only 'complete' -- 'essentials' got no importance entry


async def test_sync_dry_run_reports_titles_that_would_be_pruned() -> None:
    # I4: dry-run must compute the destructive prune count from data already in hand, not
    # always report 0 -- the operator's pre-flight check must see what would be deleted.
    t1 = _title(tmdb_id=1)
    seed = _seed((t1,))

    tmdb = FakeTMDBClient(movies={1: {'title': 'One', 'runtime': 100}})
    # title 2 exists in the database from a prior sync but is absent from this seed.
    repo = FakeWatchlistRepository(existing_titles=[{'tmdb_type': 'movie', 'tmdb_id': 2, 'id': 555}])
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',), dry_run=True)

    assert report.titles_pruned == 1
    assert any('movie:2' in warning for warning in report.warnings)
    assert repo.pruned_with is None  # still no actual write in dry-run


async def test_sync_records_provider_rows_and_report_count() -> None:
    # I6: the provider write path (region loop, providers_written accounting,
    # replace_providers wiring) is otherwise never exercised by any sync test.
    t1 = _title(tmdb_id=1)
    seed = _seed((t1,))

    providers_payload = {
        'results': {
            'US': {
                'flatrate': [{'provider_id': 8, 'provider_name': 'Netflix', 'logo_path': '/n.jpg'}],
                'rent': [],
                'buy': [],
            },
        },
    }
    tmdb = FakeTMDBClient(
        movies={1: {'title': 'One', 'runtime': 100}},
        providers={('movie', 1): providers_payload},
    )
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US', 'GB'))

    title_id = repo.pruned_with[0]
    assert repo.providers[(title_id, 'US')] == [
        {'provider_id': 8, 'provider_name': 'Netflix', 'logo_path': '/n.jpg', 'offer': 'flatrate'},
    ]
    assert repo.providers[(title_id, 'GB')] == []  # GB has no providers in the payload
    assert report.providers_written == 1


async def test_sync_skips_title_on_slug_conflict_and_keeps_existing_row() -> None:
    # I7: watch_titles' UNIQUE (universe, slug) is unguarded by upsert_title's ON CONFLICT
    # target (universe, tmdb_type, tmdb_id) -- a slug collision must be caught, warned about,
    # and must not abort the rest of the run or let prune_titles delete the existing row.
    good = _title(tmdb_id=1)
    conflicting = _title(tmdb_id=2, story=2, release=2)
    seed = _seed((good, conflicting))

    tmdb = FakeTMDBClient(movies={1: {'title': 'One', 'runtime': 100}, 2: {'title': 'Two', 'runtime': 50}})
    repo = FakeWatchlistRepository(
        existing_titles=[{'tmdb_type': 'movie', 'tmdb_id': 2, 'id': 555}],
        upsert_conflicts={('movie', 2)},
    )
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',))

    assert report.titles_seen == 2
    assert report.titles_written == 1  # the conflicting title was not actually written
    assert any('slug conflict' in warning for warning in report.warnings)
    assert repo.pruned_with is not None
    assert 555 in repo.pruned_with  # existing row preserved, not pruned


# -- credits (V41 people) -----------------------------------------------------------------------


async def test_sync_writes_people_and_credits_for_each_title() -> None:
    seed = _seed((_title(tmdb_id=1),))
    tmdb = FakeTMDBClient(
        movies={1: {'title': 'One', 'runtime': 100}},
        credits={('movie', 1): {
            'cast': [{'id': 3223, 'name': 'Robert Downey Jr.', 'order': 0, 'character': 'Tony Stark'}],
            'crew': [{'id': 15277, 'name': 'Jon Favreau', 'job': 'Director'}],
        }},
    )
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',))

    assert set(repo.people) == {3223, 15277}
    title_id = next(iter(repo.credits))
    assert [(row['tmdb_person_id'], row['role']) for row in repo.credits[title_id]] == [
        (3223, 'cast'), (15277, 'director'),
    ]
    assert report.credits_written == 2
    # People are upserted before the credit rows that reference them (FK ordering).
    names = [call[0] for call in repo.calls]
    assert names.index('upsert_person') < names.index('replace_credits')


async def test_sync_passes_a_shows_created_by_through_as_creator_credits() -> None:
    seed = _seed((_title(tmdb_type='tv', tmdb_id=7),))
    tmdb = FakeTMDBClient(
        tv={7: {
            'name': 'Show', 'episode_run_time': [45], 'seasons': [{'season_number': 1}],
            'created_by': [{'id': 20, 'name': 'Jac Schaeffer'}],
        }},
        seasons={(7, 1): {'episodes': [{'runtime': 45}]}},
    )
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    await ingest.sync(seed, regions=('US',))

    title_id = next(iter(repo.credits))
    assert [(row['tmdb_person_id'], row['role']) for row in repo.credits[title_id]] == [(20, 'creator')]
    # ``_created_by`` is an ingest-internal key and must never reach upsert_title.
    upsert = next(call for call in repo.calls if call[0] == 'upsert_title')
    assert '_created_by' not in upsert[1][1]


async def test_sync_credits_failure_warns_and_leaves_existing_credits_alone() -> None:
    seed = _seed((_title(tmdb_id=1),))
    tmdb = FakeTMDBClient(
        movies={1: {'title': 'One', 'runtime': 100}},
        errors={('credits', 1): _FakeTMDBError('credits are down')},
    )
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',))

    assert report.titles_written == 1  # the title itself still synced
    assert report.credits_written == 0
    assert any('credits are down' in warning for warning in report.warnings)
    assert repo.credits == {}  # never called -> an outage cannot empty a title's cast


async def test_sync_dry_run_fetches_no_credits() -> None:
    seed = _seed((_title(tmdb_id=1),))
    tmdb = FakeTMDBClient(movies={1: {'title': 'One', 'runtime': 100}})
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',), dry_run=True)

    assert not any(call[0] == 'credits' for call in tmdb.calls)
    assert report.credits_written == 0
    assert repo.people_pruned == 0


async def test_sync_writes_the_selected_trailer() -> None:
    seed = _seed((_title(tmdb_id=1),))
    tmdb = FakeTMDBClient(
        movies={1: {'title': 'One', 'runtime': 100}},
        videos={('movie', 1): {'results': [
            {'name': 'Teaser', 'key': 'teaser', 'site': 'YouTube', 'type': 'Teaser', 'official': True},
            {'name': 'Official Trailer', 'key': 'yt', 'site': 'YouTube', 'type': 'Trailer', 'official': True},
        ]}},
    )
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',))

    assert report.trailers_written == 1
    assert list(repo.trailers.values()) == [
        {'trailer_site': 'YouTube', 'trailer_key': 'yt', 'trailer_name': 'Official Trailer'}]


async def test_sync_writes_no_trailer_when_there_is_none() -> None:
    seed = _seed((_title(tmdb_id=1),))
    tmdb = FakeTMDBClient(movies={1: {'title': 'One', 'runtime': 100}})  # -> {'results': []}
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',))

    # A title with no trailer is normal, not an error: nothing written, nothing warned about.
    assert report.trailers_written == 0
    assert repo.trailers == {}
    assert report.warnings == []


async def test_sync_videos_failure_warns_and_leaves_the_stored_trailer_alone() -> None:
    seed = _seed((_title(tmdb_id=1),))
    tmdb = FakeTMDBClient(
        movies={1: {'title': 'One', 'runtime': 100}},
        errors={('videos', 1): _FakeTMDBError('videos are down')},
    )
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',))

    assert report.titles_written == 1  # the title itself still synced
    assert report.trailers_written == 0
    assert any('videos are down' in warning for warning in report.warnings)
    assert repo.trailers == {}  # never called -> an outage cannot clear a stored trailer


async def test_sync_dry_run_fetches_no_videos() -> None:
    seed = _seed((_title(tmdb_id=1),))
    tmdb = FakeTMDBClient(movies={1: {'title': 'One', 'runtime': 100}})
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',), dry_run=True)

    assert not any(call[0] == 'videos' for call in tmdb.calls)
    assert report.trailers_written == 0


# -- [[discover]] expansion ---------------------------------------------------------------------


def _movie(tmdb_id: int, date: str | None) -> dict[str, Any]:
    """One TMDB ``discover/movie`` result, trimmed to the fields the expansion reads."""
    return {'id': tmdb_id, 'title': f'Movie {tmdb_id}', 'release_date': date}


def _upserted_orders(repo: FakeWatchlistRepository) -> dict[int, tuple[int, int]]:
    """``{tmdb_id: (story_order, release_order)}`` for every title actually upserted."""
    return {
        call[1][1]['tmdb_id']: (call[1][1]['story_order'], call[1][1]['release_order'])
        for call in repo.calls if call[0] == 'upsert_title'
    }


async def test_discovery_never_overrides_a_curated_title() -> None:
    # Curation always wins: id 1 is hand-written with its own era/story/release/context, and
    # TMDB returning it must not add a second title nor restate any curated column.
    curated = _title(tmdb_id=1, story=100, release=100, context='Where it starts', milestone=True)
    seed = _seed((curated,), discover=(DiscoverSeed(kind='movie', keyword=180547, era='p1'),))

    tmdb = FakeTMDBClient(
        movies={1: {'title': 'One', 'runtime': 100}, 2: {'title': 'Two', 'runtime': 90}},
        discover={('movie', 180547): [[_movie(1, '2008-05-02'), _movie(2, '2008-06-13')]]},
    )
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',))

    assert report.discovered == 1  # only id 2; id 1 was already curated
    assert report.titles_seen == 2
    orders = _upserted_orders(repo)
    assert orders[1] == (100, 100)  # curated ordering untouched
    assert orders[2] == (DISCOVER_ORDER_BASE, DISCOVER_ORDER_BASE)
    curated_row = next(call[1][1] for call in repo.calls if call[0] == 'upsert_title' and call[1][1]['tmdb_id'] == 1)
    assert curated_row['context'] == 'Where it starts'
    assert curated_row['milestone'] is True


async def test_discovery_orders_by_release_date_from_the_base_in_steps() -> None:
    # The later film is returned first; ordering is by release date, not payload order.
    seed = _seed((), discover=(DiscoverSeed(kind='movie', keyword=1),))
    tmdb = FakeTMDBClient(
        movies={1: {'title': 'Later', 'runtime': 100}, 2: {'title': 'Earlier', 'runtime': 90}},
        discover={('movie', 1): [[_movie(1, '2012-05-04'), _movie(2, '2008-05-02')]]},
    )
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',))

    assert report.discovered == 2
    assert _upserted_orders(repo) == {
        2: (DISCOVER_ORDER_BASE, DISCOVER_ORDER_BASE),
        1: (DISCOVER_ORDER_BASE + DISCOVER_ORDER_STEP, DISCOVER_ORDER_BASE + DISCOVER_ORDER_STEP),
    }


async def test_discovery_drops_undated_excluded_and_out_of_window_results() -> None:
    seed = _seed((), discover=(DiscoverSeed(
        kind='movie', keyword=1, exclude=frozenset({3}),
        min_date=datetime.date(2008, 1, 1), max_date=datetime.date(2020, 12, 31),
    ),))
    tmdb = FakeTMDBClient(
        movies={num: {'title': f'M{num}', 'runtime': 100} for num in range(1, 7)},
        discover={('movie', 1): [[
            _movie(1, None),          # undated announcement -- nothing to order by
            _movie(2, ''),            # TMDB's other spelling of "no date"
            _movie(3, '2010-01-01'),  # excluded by id
            _movie(4, '2007-12-31'),  # before min_date
            _movie(5, '2021-01-01'),  # after max_date
            _movie(6, '2015-06-01'),  # the only survivor
        ]]},
    )
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',))

    assert report.discovered == 1
    assert list(_upserted_orders(repo)) == [6]


async def test_discovery_applies_era_and_importance_to_every_declared_path() -> None:
    paths = (PathSeed(slug='complete', name='Complete', default=True), PathSeed(slug='essentials', name='Essentials'))
    seed = _seed((), paths=paths, discover=(
        DiscoverSeed(kind='movie', keyword=1, era='p1', importance='recommended'),
    ))
    tmdb = FakeTMDBClient(
        movies={1: {'title': 'One', 'runtime': 100}},
        discover={('movie', 1): [[_movie(1, '2015-06-01')]]},
    )
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    await ingest.sync(seed, regions=('US',))

    row = next(call[1][1] for call in repo.calls if call[0] == 'upsert_title')
    assert (row['era'], row['era_order']) == ('p1', 100)  # the block's era, resolved to its order
    assert row['context'] is None and row['instruction'] is None and row['milestone'] is False
    assert {level for _, _, level in repo.importance} == {'recommended'}
    assert len(repo.importance) == 2  # one row per declared path


async def test_discovery_expands_a_collections_parts_in_one_request() -> None:
    seed = _seed((), discover=(DiscoverSeed(kind='movie', collection=86311),))
    tmdb = FakeTMDBClient(
        movies={24428: {'title': 'The Avengers', 'runtime': 143}},
        collections={86311: {'id': 86311, 'name': 'The Avengers Collection', 'parts': [_movie(24428, '2012-04-25')]}},
    )
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',))

    assert report.discovered == 1
    assert [call for call in tmdb.calls if call[0] == 'collection'] == [('collection', (86311,))]
    assert not any(call[0] == 'discover' for call in tmdb.calls)  # a collection is not paged


async def test_discovery_pages_until_exhausted_then_stops() -> None:
    seed = _seed((), discover=(DiscoverSeed(kind='tv', company=420),))
    tmdb = FakeTMDBClient(
        tv={num: {'name': f'Show {num}', 'seasons': [], 'episode_run_time': [45]} for num in (1, 2)},
        discover={('tv', 420): [
            [{'id': 1, 'name': 'Show 1', 'first_air_date': '2013-09-24'}],
            [{'id': 2, 'name': 'Show 2', 'first_air_date': '2015-01-06'}],
        ]},
    )
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',))

    assert report.discovered == 2  # both pages consumed
    pages = [call[1][3] for call in tmdb.calls if call[0] == 'discover']
    assert pages == [1, 2]  # and no third request once total_pages is reached
    assert len(pages) <= DISCOVER_PAGE_CAP


async def test_discovery_stops_at_the_page_cap() -> None:
    # A runaway query (here: TMDB claims 500 pages) must cost a bounded crawl, not 500 requests.
    seed = _seed((), discover=(DiscoverSeed(kind='movie', company=420),))
    tmdb = FakeTMDBClient(
        movies={num: {'title': f'M{num}', 'runtime': 100} for num in range(1, 100)},
        discover={('movie', 420): [[_movie(page, f'20{page:02d}-01-01')] for page in range(1, 60)]},
    )
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',))

    assert len([call for call in tmdb.calls if call[0] == 'discover']) == DISCOVER_PAGE_CAP
    assert report.discovered == DISCOVER_PAGE_CAP  # one result per page


async def test_discovery_deduplicates_across_overlapping_blocks() -> None:
    # A keyword and a company query legitimately cover much of the same franchise; the same
    # film must not be seeded twice (and must not consume two order slots).
    seed = _seed((), discover=(
        DiscoverSeed(kind='movie', keyword=1),
        DiscoverSeed(kind='movie', company=2),
    ))
    tmdb = FakeTMDBClient(
        movies={1: {'title': 'One', 'runtime': 100}, 2: {'title': 'Two', 'runtime': 90}},
        discover={
            ('movie', 1): [[_movie(1, '2008-05-02')]],
            ('movie', 2): [[_movie(1, '2008-05-02'), _movie(2, '2010-05-07')]],
        },
    )
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',))

    assert report.discovered == 2
    assert _upserted_orders(repo) == {
        1: (DISCOVER_ORDER_BASE, DISCOVER_ORDER_BASE),
        2: (DISCOVER_ORDER_BASE + DISCOVER_ORDER_STEP, DISCOVER_ORDER_BASE + DISCOVER_ORDER_STEP),
    }


async def test_discovery_failure_keeps_the_curated_titles_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    # A sync must never be all-or-nothing on an optional expansion: the hand-curated titles are
    # the part someone actually authored, and a dead keyword query must not cost them.
    seed = _seed((_title(tmdb_id=1),), discover=(DiscoverSeed(kind='movie', keyword=999),))
    tmdb = FakeTMDBClient(
        movies={1: {'title': 'One', 'runtime': 100}},
        errors={('discover', 999): _FakeTMDBError('tmdb is down')},
    )
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    with caplog.at_level(logging.WARNING, logger='app.services.watchlist.ingest'):
        report = await ingest.sync(seed, regions=('US',))

    assert report.discovered == 0
    assert report.titles_written == 1  # the curated title still synced
    assert list(_upserted_orders(repo)) == [1]
    logged = [record.getMessage() for record in caplog.records]
    assert any('mcu' in message and 'keyword=999' in message and 'tmdb is down' in message for message in logged)


async def test_discovery_failure_skips_the_prune_so_previously_discovered_titles_survive() -> None:
    # Finding 1 regression. Before the fix: a failing discovery block meant `discovered` (and
    # therefore keep_ids) was missing whatever that block previously contributed, so
    # prune_titles deleted those titles -- cascading to every user's watch_progress for them,
    # over a transient TMDB error. Id 2 here stands for a title a *previous, successful* sync
    # of this block discovered and wrote; this run's block is down.
    curated = _title(tmdb_id=1)
    seed = _seed((curated,), discover=(DiscoverSeed(kind='movie', keyword=999),))
    tmdb = FakeTMDBClient(
        movies={1: {'title': 'One', 'runtime': 100}},
        errors={('discover', 999): _FakeTMDBError('tmdb is down')},
    )
    repo = FakeWatchlistRepository(existing_titles=[
        {'tmdb_type': 'movie', 'tmdb_id': 1, 'id': 500},
        {'tmdb_type': 'movie', 'tmdb_id': 2, 'id': 777},  # previously discovered; must survive
    ])
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',))

    assert report.discovered == 0  # the block failed -- nothing contributed this run
    assert report.titles_written == 1  # the curated title still synced
    assert repo.pruned_with is None  # prune_titles was never called -- id 2 was never at risk
    assert report.titles_pruned == 0
    assert any('skipping the title prune' in warning for warning in report.warnings)


async def test_sync_without_discover_makes_no_tmdb_discovery_calls() -> None:
    # The no-[[discover]] path must behave exactly as it did before this feature existed.
    seed = _seed((_title(tmdb_id=1),))
    tmdb = FakeTMDBClient(movies={1: {'title': 'One', 'runtime': 100}})
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',))

    assert not any(call[0] in {'discover', 'collection'} for call in tmdb.calls)
    assert report.discovered == 0


async def test_dry_run_still_expands_discovery_so_nothing_looks_prunable() -> None:
    # Without expanding in a dry run, every discovered title already in the database would be
    # reported as about to be pruned -- the exact opposite of what the run is about to do.
    seed = _seed((), discover=(DiscoverSeed(kind='movie', keyword=1),))
    tmdb = FakeTMDBClient(
        movies={1: {'title': 'One', 'runtime': 100}},
        discover={('movie', 1): [[_movie(1, '2008-05-02')]]},
    )
    repo = FakeWatchlistRepository(existing_titles=[{'tmdb_type': 'movie', 'tmdb_id': 1, 'id': 555}])
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    report = await ingest.sync(seed, regions=('US',), dry_run=True)

    assert report.discovered == 1
    assert report.titles_pruned == 0
    assert repo.pruned_with is None


async def test_sync_prunes_orphaned_people_once_per_run() -> None:
    seed = _seed((_title(tmdb_id=1), _title(tmdb_id=2, story=2, release=2)))
    tmdb = FakeTMDBClient(movies={1: {'title': 'One', 'runtime': 100}, 2: {'title': 'Two', 'runtime': 90}})
    repo = FakeWatchlistRepository()
    ingest = WatchlistIngest(tmdb, repo)  # type: ignore[arg-type]

    await ingest.sync(seed, regions=('US',))

    assert repo.people_pruned == 1
