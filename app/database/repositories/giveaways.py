from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    from collections.abc import Iterable

    import asyncpg

__all__ = ('GiveawaysRepository',)


class GiveawaysRepository(BaseRepository):
    """Data access for the ``giveaways`` table.

    Each row stores a giveaway's location (guild/channel/message), its author,
    the set of entrant IDs, and a JSONB ``metadata`` blob holding the prize,
    schedule and winner count. Methods return raw records/scalars; the
    ``Giveaways`` cog wraps them in ``Giveaway`` records.
    """

    async def get_giveaway(self, giveaway_id: int) -> asyncpg.Record | None:
        """Fetches a giveaway by ID."""
        return await self.fetchrow("SELECT * FROM giveaways WHERE id = $1 LIMIT 1;", giveaway_id)

    async def get_guild_giveaway(self, guild_id: int, giveaway_id: int) -> asyncpg.Record | None:
        """Fetches a giveaway by ID, scoped to a guild."""
        return await self.fetchrow(
            "SELECT * FROM giveaways WHERE guild_id = $1 AND id = $2 LIMIT 1;", guild_id, giveaway_id)

    async def get_guild_giveaways(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches every giveaway in a guild."""
        return await self.fetch("SELECT * FROM giveaways WHERE guild_id = $1;", guild_id)

    async def create_giveaway(
            self, channel_id: int, message_id: int, guild_id: int, author_id: int, metadata: dict[str, Any]
    ) -> int:
        """Inserts a new giveaway and returns its ID."""
        query = """
            INSERT INTO giveaways (channel_id, message_id, guild_id, author_id, metadata)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            RETURNING id;
        """
        return await self.fetchval(query, channel_id, message_id, guild_id, author_id, metadata)

    async def set_entries(self, giveaway_id: int, entries: Iterable[int]) -> None:
        """Replaces the entrant set of a giveaway."""
        await self.execute("UPDATE giveaways SET entries = $1 WHERE id = $2;", entries, giveaway_id)

    async def delete_giveaway(self, giveaway_id: int) -> None:
        """Deletes a giveaway."""
        await self.execute("DELETE FROM giveaways WHERE id = $1;", giveaway_id)
