"""Tests for :class:`~app.database.repositories.music_plays.MusicPlaysRepository`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories.music_plays import MusicPlaysRepository

if TYPE_CHECKING:
    from unittest.mock import MagicMock


async def test_record_inserts_seven_values_in_order(mock_db: MagicMock) -> None:
    await MusicPlaysRepository(mock_db).record(
        1, 2, title='T', author='A', uri='u', artwork='art', length_ms=3000
    )

    mock_db.execute.assert_awaited_once()
    query, *params = mock_db.execute.await_args.args
    assert 'INSERT INTO music_plays' in query
    assert params == [1, 2, 'T', 'A', 'u', 'art', 3000]


async def test_user_top_tracks_groups_and_passes_args(mock_db: MagicMock) -> None:
    await MusicPlaysRepository(mock_db).user_top_tracks(5, days=7, limit=3)

    query, *params = mock_db.fetch.await_args.args
    assert 'GROUP BY' in query
    assert params == [5, 7, 3]


async def test_guild_history_passes_before_and_limit(mock_db: MagicMock) -> None:
    await MusicPlaysRepository(mock_db).guild_history(9, limit=10)

    query, *params = mock_db.fetch.await_args.args
    assert 'ORDER BY played_at DESC' in query
    assert params == [9, None, 10]


async def test_prune_defaults_to_90_days_and_parses_count(mock_db: MagicMock) -> None:
    mock_db.execute.return_value = 'DELETE 4'

    removed = await MusicPlaysRepository(mock_db).prune()

    assert removed == 4
    assert mock_db.execute.await_args.args[1:] == (90,)
