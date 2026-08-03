"""Tests for the watchlist seed format and TMDB ingest service.

A fake TMDB client (canned payloads / queued errors) and a fake watchlist repository
(records every call) stand in for the real network/DB layers, mirroring the pattern used for
:class:`~app.services.ai.AIService` in ``tests/test_ai_service.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import asyncpg
import pytest

from app.clients.base import HTTPClientError
from app.services.watchlist import (
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
        errors: dict[tuple[str, int], BaseException] | None = None,
    ) -> None:
        self._movies = movies or {}
        self._tv = tv or {}
        self._seasons = seasons or {}
        self._providers = providers or {}
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

    async def watch_providers(self, kind: str, item_id: int) -> Any:
        self.calls.append(('watch_providers', (kind, item_id)))
        return self._providers.get((kind, item_id), {'results': {}})


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


def _title(**overrides: Any) -> TitleSeed:
    base: dict[str, Any] = {'tmdb_type': 'movie', 'tmdb_id': 1, 'era': 'p1', 'story': 1, 'release': 1}
    base.update(overrides)
    return TitleSeed(**base)


def _seed(titles: tuple[TitleSeed, ...], paths: tuple[PathSeed, ...] = ()) -> UniverseSeed:
    return UniverseSeed(
        slug='mcu', name='MCU', short_name='MCU', paths=paths,
        eras=(EraSeed(key='p1', name='Phase 1', order=100),), titles=titles,
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


def test_load_seed_valid(tmp_path: Path) -> None:
    path = _write_seed(tmp_path, _VALID_SEED)
    seed = load_seed(path)
    assert seed.slug == 'mcu'
    assert len(seed.paths) == 1
    assert len(seed.eras) == 1
    assert len(seed.titles) == 1
    assert seed.titles[0].importance == {'complete': 'essential'}


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
