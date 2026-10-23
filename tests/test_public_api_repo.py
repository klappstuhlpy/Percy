"""SQL-shape checks for the repository methods behind the public API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories import UsersRepository

if TYPE_CHECKING:
    from unittest.mock import MagicMock


async def test_adjust_balance_is_one_conditional_update(mock_db: MagicMock) -> None:
    repo = UsersRepository(mock_db)

    await repo.adjust_balance(1, 2, cash=-5, bank=7)

    query, *params = mock_db.fetchrow.await_args.args
    assert query.startswith('UPDATE economy SET cash = cash + $3, bank = bank + $4')
    assert 'cash + $3 >= 0 AND bank + $4 >= 0' in query
    assert params == [1, 2, -5, 7]
