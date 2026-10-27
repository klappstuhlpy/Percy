"""SQL-shape checks for the atomic debit and giveaway-entry repository methods."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories import EconomyRepository, GiveawaysRepository

if TYPE_CHECKING:
    from unittest.mock import MagicMock


async def test_debit_cash_is_one_conditional_update(mock_db: MagicMock) -> None:
    mock_db.fetchval.return_value = 250
    repo = EconomyRepository(mock_db)

    assert await repo.debit_cash(1, 2, 750) == 250

    query, *params = mock_db.fetchval.await_args.args
    assert query.startswith('UPDATE economy SET cash = cash - $3')
    assert 'cash >= $3' in query and 'RETURNING cash' in query
    assert params == [1, 2, 750]


async def test_debit_cash_returns_none_when_short(mock_db: MagicMock) -> None:
    mock_db.fetchval.return_value = None
    assert await EconomyRepository(mock_db).debit_cash(1, 2, 10**9) is None


async def test_transfer_cash_skips_credit_when_debit_fails(mock_db: MagicMock) -> None:
    conn = mock_db.connection
    conn.transaction = lambda: mock_db.acquire.return_value  # reuse the async CM mock
    conn.fetchval.return_value = None

    assert await EconomyRepository(mock_db).transfer_cash(1, 3, 2, 1000) is None
    assert not any('cash + $3' in call.args[0] for call in conn.execute.await_args_list)


async def test_add_entry_is_a_guarded_append(mock_db: MagicMock) -> None:
    mock_db.fetchval.return_value = 1
    repo = GiveawaysRepository(mock_db)

    assert await repo.add_entry(7, 42) is True

    query, *params = mock_db.fetchval.await_args.args
    assert 'array_append(entries, $2::bigint)' in query
    assert 'NOT ($2::bigint = ANY(entries))' in query
    assert params == [7, 42]

    mock_db.fetchval.return_value = None
    assert await repo.add_entry(7, 42) is False
