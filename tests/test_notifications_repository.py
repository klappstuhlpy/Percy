from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories import NotificationsRepository

if TYPE_CHECKING:
    from unittest.mock import MagicMock


async def test_add_dedupes_when_no_row(mock_db: MagicMock) -> None:
    repo = NotificationsRepository(mock_db)
    mock_db.fetchval.return_value = None
    assert await repo.add(1, kind='notice', title='t', dedupe_key='k') is None
    query = mock_db.fetchval.await_args.args[0]
    assert 'NOT EXISTS' in query and 'dedupe_key' in query and 'RETURNING id' in query
    mock_db.fetchval.return_value = 7
    assert await repo.add(1, kind='notice', title='t') == 7


async def test_claim_request_is_guarded(mock_db: MagicMock) -> None:
    repo = NotificationsRepository(mock_db)
    mock_db.fetchrow.side_effect = [{'id': 1}, None]
    assert await repo.claim_request(1, 2, status='approved', decided_by=3, note=None) == {'id': 1}
    assert await repo.claim_request(1, 2, status='approved', decided_by=3, note=None) is None
    query = mock_db.fetchrow.await_args.args[0]
    assert "status = 'pending'" in query and 'expires_at >' in query and 'RETURNING *' in query


async def test_mark_read_never_moves_backwards(mock_db: MagicMock) -> None:
    await NotificationsRepository(mock_db).mark_read(1, 2, 9)
    query = mock_db.execute.await_args.args[0]
    assert 'GREATEST' in query
    assert 'LEAST($3::bigint' in query and 'MAX(id)' in query  # cursor clamped to the real max


async def test_update_request_settings_invalidates(mock_db: MagicMock) -> None:
    await NotificationsRepository(mock_db).update_request_settings(5, 10, [1, 2])
    mock_db.signals.fire.assert_called_once()
    assert mock_db.signals.fire.call_args.args[0] == 'guild_config_changed'
