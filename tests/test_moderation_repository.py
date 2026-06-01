"""Tests for :class:`~app.database.repositories.moderation.ModerationRepository`.

These confirm the repository forwards the right SQL and arguments to the shared
pool helpers, and that mutating the mute role busts the cached guild config.
They establish the pattern for testing the repository layer in isolation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories import ModerationRepository

if TYPE_CHECKING:
    from unittest.mock import MagicMock


def make_repo(mock_db: MagicMock) -> ModerationRepository:
    return ModerationRepository(mock_db)


async def test_clear_lockdowns_deletes_for_guild(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    await repo.clear_lockdowns(123)

    mock_db.execute.assert_awaited_once()
    query, *params = mock_db.execute.await_args.args
    assert 'DELETE FROM guild_lockdowns' in query
    assert params == [123]


async def test_remove_lockdowns_passes_channel_ids(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    await repo.remove_lockdowns(1, [10, 20])

    query, *params = mock_db.execute.await_args.args
    assert 'guild_lockdowns' in query and 'ANY' in query
    assert params == [1, [10, 20]]


async def test_get_lockdowns_without_channels_fetches_all(mock_db: MagicMock) -> None:
    rows = [(10, 1, 2)]
    mock_db.fetch.return_value = rows
    repo = make_repo(mock_db)

    result = await repo.get_lockdowns(7)

    assert result is rows
    query, *params = mock_db.fetch.await_args.args
    assert 'SELECT channel_id, allow, deny' in query
    assert 'ANY' not in query  # the unfiltered branch
    assert params == [7]


async def test_get_lockdowns_with_channels_filters(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    await repo.get_lockdowns(7, channel_ids=[10, 20])

    query, *params = mock_db.fetch.await_args.args
    assert 'ANY ($2::bigint[])' in query
    assert params == [7, [10, 20]]


async def test_add_lockdowns_inserts_records(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)
    records = [{'guild_id': 1, 'channel_id': 2, 'allow': 0, 'deny': 0}]

    await repo.add_lockdowns(records)

    query, *params = mock_db.execute.await_args.args
    assert 'INSERT INTO guild_lockdowns' in query
    assert params == [records]


async def test_get_lockdown_returns_record(mock_db: MagicMock) -> None:
    sentinel = object()
    mock_db.fetchrow.return_value = sentinel
    repo = make_repo(mock_db)

    result = await repo.get_lockdown(1, 2)

    assert result is sentinel
    query, *params = mock_db.fetchrow.await_args.args
    assert 'SELECT * FROM guild_lockdowns' in query
    assert params == [1, 2]


async def test_set_mute_role_writes_and_invalidates(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    await repo.set_mute_role(1, 99, members=[3, 4])

    query, *params = mock_db.execute.await_args.args
    assert 'INSERT INTO guild_config' in query
    assert 'muted_members' in query
    assert params == [1, 99, [3, 4]]
    # mutating the mute role must bust the cached guild config
    mock_db.get_guild_config.invalidate.assert_called_once_with(1)


async def test_unbind_mute_role_clears_and_invalidates(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    await repo.unbind_mute_role(42)

    query, *params = mock_db.execute.await_args.args
    assert 'UPDATE guild_config' in query
    assert params == [42]
    mock_db.get_guild_config.invalidate.assert_called_once_with(42)
