"""Data access for voice bans, frozen nicknames and channel mutes (``migrations/V11``)."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import asyncpg

__all__ = ('RestrictionsRepository',)


def _naive(value: datetime.datetime | None) -> datetime.datetime | None:
    # Naive UTC, like every other timestamp in the schema.
    return value.astimezone(datetime.UTC).replace(tzinfo=None) if value and value.tzinfo else value


class RestrictionsRepository(BaseRepository):
    """One row per (member, kind, channel)."""

    async def upsert(
        self,
        guild_id: int,
        user_id: int,
        kind: str,
        *,
        channel_id: int = 0,
        value: str | None = None,
        expires_at: datetime.datetime | None = None,
    ) -> None:
        query = """
            INSERT INTO member_restrictions (guild_id, user_id, kind, channel_id, value, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (guild_id, user_id, kind, channel_id)
                DO UPDATE SET value = EXCLUDED.value, expires_at = EXCLUDED.expires_at;
        """
        await self.execute(query, guild_id, user_id, kind, channel_id, value, _naive(expires_at))

    async def get(self, guild_id: int, user_id: int, kind: str, channel_id: int = 0) -> asyncpg.Record | None:
        query = """
            SELECT * FROM member_restrictions
            WHERE guild_id = $1 AND user_id = $2 AND kind = $3 AND channel_id = $4;
        """
        return await self.fetchrow(query, guild_id, user_id, kind, channel_id)

    async def for_member(self, guild_id: int, user_id: int) -> list[asyncpg.Record]:
        query = "SELECT * FROM member_restrictions WHERE guild_id = $1 AND user_id = $2;"
        return await self.fetch(query, guild_id, user_id)

    async def remove(self, guild_id: int, user_id: int, kind: str, channel_id: int = 0) -> bool:
        """Deletes one restriction, returning whether it existed."""
        query = """
            DELETE FROM member_restrictions
            WHERE guild_id = $1 AND user_id = $2 AND kind = $3 AND channel_id = $4
            RETURNING 1;
        """
        return await self.fetchval(query, guild_id, user_id, kind, channel_id) is not None

    async def list_kind(self, guild_id: int, kind: str) -> list[asyncpg.Record]:
        """Every restriction of one kind in the guild."""
        query = "SELECT * FROM member_restrictions WHERE guild_id = $1 AND kind = $2 ORDER BY user_id, channel_id;"
        return await self.fetch(query, guild_id, kind)

    async def remove_scope(self, guild_id: int, kind: str, channel_id: int) -> None:
        """Deletes ``kind`` restrictions of every member for one scope (a deleted role's sticky rows)."""
        query = "DELETE FROM member_restrictions WHERE guild_id = $1 AND kind = $2 AND channel_id = $3;"
        await self.execute(query, guild_id, kind, channel_id)
