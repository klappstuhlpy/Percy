"""Data access for guild notifications and moderator -> admin action requests
(``migrations/V50__notifications.sql``).

Only :meth:`NotificationsRepository.update_request_settings` touches a cached row
(``guild_config``) and so fires the cache signal; everything else is uncached.
"""

from __future__ import annotations

import datetime
import json
from typing import TYPE_CHECKING, Any

from app.database.repositories.base import BaseRepository
from app.services.notifications import DEDUPE_WINDOW, REQUEST_TTL, RETENTION

if TYPE_CHECKING:
    import asyncpg

__all__ = ('NotificationsRepository',)


def _now() -> datetime.datetime:
    # Naive UTC, like every other timestamp in the schema.
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


class NotificationsRepository(BaseRepository):
    """Notification feed, per-user read marks and action requests."""

    async def add(
        self,
        guild_id: int | None,
        *,
        kind: str,
        severity: str = 'info',
        title: str,
        body: str = '',
        link: str | None = None,
        meta: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> int | None:
        """Inserts a notification; ``None`` when the same ``dedupe_key`` already fired within the window."""
        # ponytail: dedupe not race-proof; a unique partial index if duplicates ever matter
        query = """
            INSERT INTO guild_notifications (guild_id, kind, severity, title, body, link, meta, dedupe_key)
            SELECT $1::bigint, $2, $3, $4, $5, $6, $7::jsonb, $8::text
            WHERE $8::text IS NULL OR NOT EXISTS (
                SELECT 1 FROM guild_notifications
                WHERE guild_id IS NOT DISTINCT FROM $1::bigint
                  AND dedupe_key = $8::text
                  AND created_at > $9::timestamp
            )
            RETURNING id
        """
        return await self.fetchval(
            query,
            guild_id,
            kind,
            severity,
            title,
            body,
            link,
            json.dumps(meta or {}),
            dedupe_key,
            _now() - DEDUPE_WINDOW,
        )

    async def list(self, guild_id: int, *, limit: int = 30, before_id: int | None = None) -> list[asyncpg.Record]:
        query = """
            SELECT * FROM guild_notifications
            WHERE (guild_id = $1 OR guild_id IS NULL) AND ($2::bigint IS NULL OR id < $2)
            ORDER BY id DESC
            LIMIT $3
        """
        return await self.fetch(query, guild_id, before_id, limit)

    async def last_read(self, guild_id: int, user_id: int) -> int:
        query = 'SELECT last_read_id FROM notification_reads WHERE guild_id = $1 AND user_id = $2'
        return await self.fetchval(query, guild_id, user_id) or 0

    async def unread_count(self, guild_id: int, user_id: int) -> int:
        query = """
            SELECT COUNT(*) FROM guild_notifications
            WHERE (guild_id = $1 OR guild_id IS NULL)
              AND id > COALESCE((SELECT last_read_id FROM notification_reads WHERE guild_id = $1 AND user_id = $2), 0)
        """
        return await self.fetchval(query, guild_id, user_id) or 0

    async def mark_read(self, guild_id: int, user_id: int, up_to_id: int) -> None:
        query = """
            INSERT INTO notification_reads (guild_id, user_id, last_read_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, user_id)
                DO UPDATE SET last_read_id = GREATEST(notification_reads.last_read_id, EXCLUDED.last_read_id)
        """
        await self.execute(query, guild_id, user_id, up_to_id)

    # -- action requests -------------------------------------------------------------------

    async def create_request(
        self, guild_id: int, requester_id: int, target_id: int, action: str, reason: str, duration: int | None
    ) -> asyncpg.Record:
        query = """
            INSERT INTO action_requests (guild_id, requester_id, target_id, action, reason, duration, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING *
        """
        return await self.fetchrow(
            query, guild_id, requester_id, target_id, action, reason, duration, _now() + REQUEST_TTL
        )

    async def set_request_message(self, request_id: int, channel_id: int, message_id: int) -> None:
        query = 'UPDATE action_requests SET channel_id = $2, message_id = $3 WHERE id = $1'
        await self.execute(query, request_id, channel_id, message_id)

    async def get_request(self, guild_id: int, request_id: int) -> asyncpg.Record | None:
        query = 'SELECT * FROM action_requests WHERE guild_id = $1 AND id = $2'
        return await self.fetchrow(query, guild_id, request_id)

    async def list_requests(
        self, guild_id: int, *, status: str | None = None, limit: int = 50
    ) -> list[asyncpg.Record]:
        query = """
            SELECT * FROM action_requests
            WHERE guild_id = $1 AND ($2::text IS NULL OR status = $2)
            ORDER BY id DESC
            LIMIT $3
        """
        return await self.fetch(query, guild_id, status, limit)

    async def pending_count(self, guild_id: int) -> int:
        query = "SELECT COUNT(*) FROM action_requests WHERE guild_id = $1 AND status = 'pending' AND expires_at > $2"
        return await self.fetchval(query, guild_id, _now()) or 0

    async def claim_request(
        self, guild_id: int, request_id: int, *, status: str, decided_by: int, note: str | None
    ) -> asyncpg.Record | None:
        """Atomically decides a pending, unexpired request; ``None`` when someone else got there first."""
        query = """
            UPDATE action_requests
            SET status = $3, decided_by = $4, decision_note = $5, decided_at = $6
            WHERE id = $2 AND guild_id = $1 AND status = 'pending' AND expires_at > $6
            RETURNING *
        """
        return await self.fetchrow(query, guild_id, request_id, status, decided_by, note, _now())

    async def finish_request(self, request_id: int, *, status: str, result: str | None) -> None:
        """Records the outcome of executing a claimed request (``approved`` or ``failed``)."""
        query = 'UPDATE action_requests SET status = $2, result = $3 WHERE id = $1'
        await self.execute(query, request_id, status, result)

    async def expire_requests(self) -> list[asyncpg.Record]:
        query = """
            UPDATE action_requests SET status = 'expired', decided_at = $1
            WHERE status = 'pending' AND expires_at <= $1
            RETURNING *
        """
        return await self.fetch(query, _now())

    async def prune(self) -> None:
        cutoff = _now() - RETENTION
        await self.execute('DELETE FROM guild_notifications WHERE created_at < $1', cutoff)
        await self.execute("DELETE FROM action_requests WHERE status <> 'pending' AND created_at < $1", cutoff)

    async def update_request_settings(self, guild_id: int, channel_id: int | None, role_ids: list[int]) -> None:
        query = """
            INSERT INTO guild_config (id, request_channel_id, request_role_ids)
            VALUES ($1, $2, $3::bigint[])
            ON CONFLICT (id)
                DO UPDATE SET request_channel_id = EXCLUDED.request_channel_id,
                              request_role_ids   = EXCLUDED.request_role_ids
        """
        await self.execute(query, guild_id, channel_id, list(role_ids))
        self.invalidate_cache('guild_config_changed', guild_id)
