"""Tests for :class:`~app.database.repositories.game_stats.GameStatsRepository`.

These confirm the repository forwards the right SQL and win/loss/push flags to the
shared pool helpers, and that recording is fail-safe (a database error during
telemetry must never bubble up into a game's payout flow).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories import GameStatsRepository

if TYPE_CHECKING:
    from unittest.mock import MagicMock


def make_repo(mock_db: MagicMock) -> GameStatsRepository:
    return GameStatsRepository(mock_db)


async def test_record_win_sets_win_flags(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    await repo.record_result(1, 2, 'poker', 'win', wagered=100, profit=250)

    mock_db.execute.assert_awaited_once()
    query, *params = mock_db.execute.await_args.args
    assert 'INSERT INTO game_stats' in query
    # guild, user, game, won, lost, tied, wagered, profit
    assert params == [1, 2, 'poker', 1, 0, 0, 100, 250]


async def test_record_loss_sets_loss_flags(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    await repo.record_result(1, 2, 'slots', 'loss', wagered=50, profit=-50)

    _, *params = mock_db.execute.await_args.args
    assert params == [1, 2, 'slots', 0, 1, 0, 50, -50]


async def test_record_push_sets_tied_flag(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    await repo.record_result(1, 2, 'blackjack', 'push')

    _, *params = mock_db.execute.await_args.args
    assert params == [1, 2, 'blackjack', 0, 0, 1, 0, 0]


async def test_record_swallows_database_errors(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)
    mock_db.execute.side_effect = RuntimeError("pool exploded")

    # Must not raise — telemetry failures cannot break gameplay.
    await repo.record_result(1, 2, 'tower', 'win')


async def test_leaderboard_scopes_to_game(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    await repo.get_leaderboard(99, game='roulette', metric='won', limit=5)

    query, *params = mock_db.fetch.await_args.args
    assert 'AND game = $2' in query
    assert 'ORDER BY won DESC' in query
    assert params == [99, 'roulette', 1]


async def test_leaderboard_winrate_uses_min_played(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    await repo.get_leaderboard(99, metric='winrate', min_played=10)

    query, *params = mock_db.fetch.await_args.args
    assert 'AND game' not in query  # no game scope
    assert 'winrate DESC' in query
    assert params == [99, 10]
