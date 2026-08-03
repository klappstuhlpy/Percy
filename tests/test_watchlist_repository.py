"""Tests for :class:`~app.database.repositories.watchlist.WatchlistRepository`.

Mirrors ``tests/test_moderation_repository.py``: exercises the SQL-building/validation
logic against the shared ``mock_db`` fixture (no live Postgres), asserting on the query
text and forwarded parameters rather than real query results.
"""

from __future__ import annotations

import datetime
import re
from typing import TYPE_CHECKING

import pytest

from app.database.repositories import WatchlistRepository
from app.services.watchlist.ingest import movie_row
from app.services.watchlist.seeds import TitleSeed

if TYPE_CHECKING:
    from unittest.mock import MagicMock

_PLACEHOLDER_RE = re.compile(r'\$\d+')


def make_repo(mock_db: MagicMock) -> WatchlistRepository:
    return WatchlistRepository(mock_db)


async def test_list_titles_rejects_bad_sort(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    with pytest.raises(ValueError):
        await repo.list_titles('mcu', sort='bogus')

    mock_db.fetch.assert_not_awaited()


async def test_list_titles_story_sort_orders_by_story_order(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    await repo.list_titles('mcu', sort='story')

    query, *params = mock_db.fetch.await_args.args
    assert 'ORDER BY t.story_order, t.id' in query
    assert params == ['mcu', None]


async def test_list_titles_release_sort_orders_by_release_order(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    await repo.list_titles('mcu', path_slug='chronological', sort='release')

    query, *params = mock_db.fetch.await_args.args
    assert 'ORDER BY t.release_order, t.id' in query
    assert params == ['mcu', 'chronological']


async def test_set_progress_rejects_bad_status(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    with pytest.raises(ValueError):
        await repo.set_progress(1, 2, 'deleted', datetime.datetime.now(datetime.UTC).replace(tzinfo=None))

    mock_db.execute.assert_not_awaited()


async def test_set_progress_upserts_with_last_write_wins(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)

    await repo.set_progress(1, 2, 'watched', now)

    query, *params = mock_db.execute.await_args.args
    assert 'ON CONFLICT (user_id, title_id) DO UPDATE' in query
    assert 'watch_progress.updated_at < EXCLUDED.updated_at' in query
    assert params == [1, 2, 'watched', now]


async def test_set_progress_bulk_rejects_bad_status(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)

    with pytest.raises(ValueError):
        await repo.set_progress_bulk(1, [(2, 'deleted', now)])

    mock_db.acquire.assert_not_called()


async def test_upsert_prefs_rejects_unknown_column(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    with pytest.raises(ValueError):
        await repo.upsert_prefs(1, {'region': 'US', 'admin': True})

    mock_db.fetchrow.assert_not_awaited()


async def test_upsert_prefs_writes_whitelisted_columns(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    await repo.upsert_prefs(1, {'region': 'DE', 'show_spoiler': True})

    query, *params = mock_db.fetchrow.await_args.args
    assert 'INSERT INTO watch_prefs' in query
    assert '"region"' in query and '"show_spoiler"' in query
    assert params == [1, 'DE', True]


async def test_prune_titles_with_empty_keep_ids_is_a_noop(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    result = await repo.prune_titles('mcu', [])

    assert result == 0
    mock_db.fetch.assert_not_awaited()


async def test_prune_titles_deletes_and_returns_count(mock_db: MagicMock) -> None:
    mock_db.fetch.return_value = [{'id': 1}, {'id': 2}]
    repo = make_repo(mock_db)

    result = await repo.prune_titles('mcu', [10, 20])

    assert result == 2
    query, *params = mock_db.fetch.await_args.args
    assert 'DELETE FROM watch_titles' in query
    assert params == ['mcu', [10, 20]]


async def test_set_importance_rejects_bad_value(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    with pytest.raises(ValueError):
        await repo.set_importance(1, 2, 'super-essential')

    mock_db.execute.assert_not_awaited()


async def test_prune_importance_deletes_rows_not_kept(mock_db: MagicMock) -> None:
    mock_db.fetch.return_value = [{'path_id': 5}]
    repo = make_repo(mock_db)

    result = await repo.prune_importance(1, [2, 3])

    assert result == 1
    query, *params = mock_db.fetch.await_args.args
    assert 'DELETE FROM watch_title_importance' in query
    assert 'path_id != ALL($2::bigint[])' in query
    assert params == [1, [2, 3]]


async def test_prune_importance_with_empty_keep_list_deletes_everything(mock_db: MagicMock) -> None:
    # Unlike prune_titles, an empty keep list here is NOT a "refuse to wipe" signal -- a seed
    # with no importance entries for a title means it belongs to no path, so every existing
    # importance row for it must go. The query still runs (no early-return guard).
    mock_db.fetch.return_value = [{'path_id': 5}, {'path_id': 6}]
    repo = make_repo(mock_db)

    result = await repo.prune_importance(1, [])

    assert result == 2
    _query, *params = mock_db.fetch.await_args.args
    assert params == [1, []]


async def test_upsert_title_placeholder_count_matches_argument_count(mock_db: MagicMock) -> None:
    # I5: guards against a placeholder/argument count drift in the 23-column INSERT (e.g. a
    # forgotten $N or a forgotten positional arg would silently misalign every column after it).
    seed_title = TitleSeed(tmdb_type='movie', tmdb_id=42, era='phase1', story=7, release=9)
    row = movie_row(seed_title, {'title': 'Test Movie', 'runtime': 120}, universe='mcu', era_order=3)
    repo = make_repo(mock_db)

    await repo.upsert_title('mcu', row)

    query, *params = mock_db.fetchval.await_args.args
    placeholders = set(_PLACEHOLDER_RE.findall(query))
    assert len(placeholders) == len(params)


async def test_upsert_title_insert_columns_match_row_builder_output(mock_db: MagicMock) -> None:
    # I5: asserts the INSERT column list and movie_row()'s output agree by construction, so
    # renaming a key in either place (e.g. 'story_order' -> 'story') fails loudly here instead
    # of silently writing the wrong column from a stale $N.
    seed_title = TitleSeed(tmdb_type='movie', tmdb_id=42, era='phase1', story=7, release=9)
    row = movie_row(seed_title, {'title': 'Test Movie', 'runtime': 120}, universe='mcu', era_order=3)
    repo = make_repo(mock_db)

    await repo.upsert_title('mcu', row)

    query, *_params = mock_db.fetchval.await_args.args
    columns_text = query.split('INSERT INTO watch_titles (', 1)[1].split(')', 1)[0]
    columns = {c.strip() for c in columns_text.split(',')}
    assert columns == set(row) | {'universe'}
