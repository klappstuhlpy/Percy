"""Tests for :class:`~app.database.repositories.users.VotesRepository`.

These confirm the repository forwards the right SQL and arguments for recording
a bot-list vote and reading back the resulting renewable XP boost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories import VotesRepository

if TYPE_CHECKING:
    from unittest.mock import MagicMock


def make_repo(mock_db: MagicMock) -> VotesRepository:
    return VotesRepository(mock_db)


async def test_record_vote_upserts_with_renewed_expiry(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    await repo.record_vote(42, 'top.gg', multiplier=1.10, duration_hours=12)

    mock_db.fetchval.assert_awaited_once()
    query, *params = mock_db.fetchval.await_args.args
    assert 'INSERT INTO vote_rewards' in query
    assert 'ON CONFLICT (user_id) DO UPDATE' in query
    # Renew (not stack): expiry is reset to now + interval, and the vote count bumps.
    assert 'total_votes = vote_rewards.total_votes + 1' in query
    assert 'make_interval(hours => $4)' in query
    assert params == [42, 1.10, 'top.gg', 12]


async def test_record_vote_defaults(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    await repo.record_vote(7, 'discordbotlist.com')

    params = list(mock_db.fetchval.await_args.args[1:])
    assert params == [7, 1.10, 'discordbotlist.com', 12]


async def test_get_active_multiplier_returns_value(mock_db: MagicMock) -> None:
    mock_db.fetchval.return_value = 1.10
    repo = make_repo(mock_db)

    result = await repo.get_active_multiplier(42)

    assert result == 1.10
    query, *params = mock_db.fetchval.await_args.args
    assert 'FROM vote_rewards' in query
    assert "expires_at > (now() at time zone 'utc')" in query
    assert params == [42]


async def test_get_active_multiplier_defaults_to_one(mock_db: MagicMock) -> None:
    mock_db.fetchval.return_value = None
    repo = make_repo(mock_db)

    assert await repo.get_active_multiplier(42) == 1.0


async def test_get_status_fetches_row(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    await repo.get_status(42)

    query, *params = mock_db.fetchrow.await_args.args
    assert 'SELECT * FROM vote_rewards WHERE user_id = $1' in query
    assert params == [42]
