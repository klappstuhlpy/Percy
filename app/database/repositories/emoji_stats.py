from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    from collections.abc import Sequence

    import asyncpg

__all__ = ('EmojiStatsRepository',)


class EmojiStatsRepository(BaseRepository):
    """Data access for the ``emoji_stats`` table.

    Tracks how often each custom emoji is used per guild. Methods return raw
    records/scalars; the ``Emoji`` cog owns the batching and presentation.
    """

    async def bulk_insert(self, rows: list[dict[str, Any]]) -> None:
        """Upserts a batch of ``(guild, emoji, added)`` usage counts."""
        query = """
            INSERT INTO emoji_stats (guild_id, emoji_id, total)
            SELECT x.guild, x.emoji, x.added
            FROM jsonb_to_recordset($1::jsonb)
                     AS x(
                          guild BIGINT,
                          emoji BIGINT,
                          added INT
                    )
            ON CONFLICT (guild_id, emoji_id) DO UPDATE
                SET total = emoji_stats.total + excluded.total;
        """
        await self.execute(query, rows)

    async def get_random_emoji_id(self, *, connection: asyncpg.Connection | None = None) -> int | None:
        """Returns a random emoji ID from the table, or ``None`` if it is empty."""
        query = """
            SELECT emoji_id
            FROM emoji_stats
            OFFSET FLOOR(RANDOM() * (SELECT COUNT(*) FROM emoji_stats))
            LIMIT 1;
        """
        return await (connection or self.db).fetchval(query)

    async def get_emoji_record(
            self, emoji_id: int, *, connection: asyncpg.Connection | None = None
    ) -> asyncpg.Record | None:
        """Fetches a single ``emoji_stats`` row for an emoji."""
        return await (connection or self.db).fetchrow(
            "SELECT * FROM emoji_stats WHERE emoji_id=$1 LIMIT 1;", emoji_id)

    async def get_guild_summary(self, guild_id: int) -> asyncpg.Record | None:
        """Returns the total uses (``Count``) and distinct emoji (``Emoji``) for a guild."""
        query = """
            SELECT
               COALESCE(SUM(total), 0) AS "Count",
               COUNT(*) AS "Emoji"
            FROM emoji_stats
            WHERE guild_id=$1
            GROUP BY guild_id;
        """
        return await self.fetchrow(query, guild_id)

    async def get_top_guild_emojis(self, guild_id: int, *, limit: int = 10) -> list[asyncpg.Record]:
        """Returns a guild's most-used emoji as ``(emoji_id, total)`` rows."""
        query = """
            SELECT emoji_id, total
            FROM emoji_stats
            WHERE guild_id=$1
            ORDER BY total DESC
            LIMIT $2;
        """
        return await self.fetch(query, guild_id, limit)

    async def get_emoji_guild_breakdown(self, emoji_id: int) -> list[asyncpg.Record]:
        """Returns per-guild usage counts for a single emoji as ``(guild_id, count)`` rows."""
        query = """
            SELECT guild_id, SUM(total) AS "count"
            FROM emoji_stats
            WHERE emoji_id=$1
            GROUP BY guild_id;
        """
        return await self.fetch(query, emoji_id)

    async def get_guild_emoji_stats(
            self, guild_id: int, emoji_ids: Sequence[int]
    ) -> list[asyncpg.Record]:
        """Returns usage for a specific set of a guild's emoji, ordered by total."""
        query = """
            SELECT emoji_id, total
            FROM emoji_stats
            WHERE guild_id=$1 AND emoji_id = ANY($2::bigint[])
            ORDER BY total DESC;
        """
        return await self.fetch(query, guild_id, emoji_ids)

    async def get_global_top_emojis(self, *, limit: int = 10) -> list[asyncpg.Record]:
        """Returns the most-used emoji across all guilds as ``(emoji_id, count)`` rows."""
        query = """
            SELECT emoji_id, SUM(total) AS "count"
            FROM emoji_stats
            GROUP BY emoji_id
            ORDER BY "count" DESC
            LIMIT $1;
        """
        return await self.fetch(query, limit)
