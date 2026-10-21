"""SQL shape of the notifications repository."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories.notifications import NotificationsRepository

if TYPE_CHECKING:
    from unittest.mock import MagicMock


async def test_pending_list_hides_lapsed_requests(mock_db: MagicMock) -> None:
    await NotificationsRepository(mock_db).list_requests(1, status='pending', limit=5)

    query, *params = mock_db.fetch.await_args.args
    assert "IS DISTINCT FROM 'pending' OR expires_at > $4" in query
    assert params[:3] == [1, 'pending', 5] and len(params) == 4
