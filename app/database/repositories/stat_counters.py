from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import asyncpg

__all__ = ('StatCountersRepository',)


class StatCountersRepository(BaseRepository):
    """Data access for the ``guild_stat_counters`` table.

    A stat counter binds a voice channel to a live server statistic (member count,
    boosts, …); a periodic loop in the cog renames the channel from its ``template``.
    Methods return raw records/scalars.
    """

    async def get_all(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches every stat counter configured for a guild."""
        return await self.fetch(
            'SELECT * FROM guild_stat_counters WHERE guild_id = $1 ORDER BY id;', guild_id)

    async def get_every(self) -> list[asyncpg.Record]:
        """Fetches every stat counter across all guilds (for the refresh loop)."""
        return await self.fetch('SELECT * FROM guild_stat_counters ORDER BY guild_id;')

    async def get_by_channel(self, channel_id: int) -> asyncpg.Record | None:
        """Fetches the counter bound to a channel, or ``None``."""
        return await self.fetchrow(
            'SELECT * FROM guild_stat_counters WHERE channel_id = $1;', channel_id)

    async def create(
        self, guild_id: int, channel_id: int, kind: str, template: str
    ) -> asyncpg.Record | None:
        """Binds a channel to a statistic, returning the row (or ``None`` if already bound)."""
        query = """
            INSERT INTO guild_stat_counters (guild_id, channel_id, kind, template)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (channel_id) DO NOTHING
            RETURNING *;
        """
        return await self.fetchrow(query, guild_id, channel_id, kind, template)

    async def delete_by_channel(self, channel_id: int) -> asyncpg.Record | None:
        """Removes the counter bound to a channel, returning the deleted row (or ``None``)."""
        return await self.fetchrow(
            'DELETE FROM guild_stat_counters WHERE channel_id = $1 RETURNING *;', channel_id)
