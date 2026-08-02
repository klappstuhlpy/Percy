"""Tests for :class:`~app.database.repositories.watchlist.WatchlistRepository`.

Mirrors ``tests/test_moderation_repository.py``: exercises the SQL-building/validation
logic against the shared ``mock_db`` fixture (no live Postgres), asserting on the query
text and forwarded parameters rather than real query results.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import pytest

from app.database.repositories import WatchlistRepository

if TYPE_CHECKING:
    from unittest.mock import MagicMock


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
        await repo.set_progress(1, 2, 'deleted', datetime.datetime.now(datetime.UTC))

    mock_db.execute.assert_not_awaited()


async def test_set_progress_upserts_with_last_write_wins(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)
    now = datetime.datetime.now(datetime.UTC)

    await repo.set_progress(1, 2, 'watched', now)

    query, *params = mock_db.execute.await_args.args
    assert 'ON CONFLICT (user_id, title_id) DO UPDATE' in query
    assert 'watch_progress.updated_at < EXCLUDED.updated_at' in query
    assert params == [1, 2, 'watched', now]


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
