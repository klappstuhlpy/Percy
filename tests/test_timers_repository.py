"""Tests for :class:`~app.database.repositories.timers.TimersRepository`.

These confirm the repository forwards the right SQL and arguments to the shared
pool helpers. The member-scoped lookups back the moderation duplicate-checks and
unmute timer cancellation (``tempmute``/``tempban`` store their target at
``args[2]`` and the guild at ``args[0]``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories import TimersRepository

if TYPE_CHECKING:
    from unittest.mock import MagicMock


def make_repo(mock_db: MagicMock) -> TimersRepository:
    return TimersRepository(mock_db)


async def test_fetch_member_timer_matches_guild_and_target(mock_db: MagicMock) -> None:
    row = object()
    mock_db.fetchrow.return_value = row
    repo = make_repo(mock_db)

    result = await repo.fetch_member_timer("tempmute", 100, 200)

    assert result is row
    query, *params = mock_db.fetchrow.await_args.args
    assert "FROM timers" in query
    assert "metadata #>> '{args,0}'" in query  # guild id
    assert "metadata #>> '{args,2}'" in query  # target id
    # The jsonb text-extraction operator (#>>) returns text, so ids are passed as strings.
    assert params == ["tempmute", "100", "200"]


async def test_delete_member_timer_returns_id_and_passes_args(mock_db: MagicMock) -> None:
    mock_db.fetchval.return_value = 42
    repo = make_repo(mock_db)

    result = await repo.delete_member_timer("tempban", 7, 9)

    assert result == 42
    query, *params = mock_db.fetchval.await_args.args
    assert "DELETE FROM timers" in query
    assert "RETURNING id" in query
    assert params == ["tempban", "7", "9"]


async def test_delete_member_timer_returns_none_when_absent(mock_db: MagicMock) -> None:
    mock_db.fetchval.return_value = None
    repo = make_repo(mock_db)

    assert await repo.delete_member_timer("tempmute", 1, 2) is None
