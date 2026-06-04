from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    from collections.abc import Iterable

    import asyncpg

__all__ = ('StarboardRepository',)


class StarboardRepository(BaseRepository):
    """Data access for the ``starboard_config`` and ``starboard_entries`` tables.

    ``starboard_config`` holds one row of per-guild settings (channel, threshold, star
    emoji, self-star toggle, ignore list); ``starboard_entries`` tracks each original
    message that has been mirrored to the starboard, keyed by the *original* message id.
    Methods return raw records/scalars; the ``Starboard`` cog wraps the config row in a
    :class:`~app.cogs.starboard.models.StarboardConfig`.
    """

    # -- config -----------------------------------------------------------

    async def get_config(self, guild_id: int) -> asyncpg.Record | None:
        """Fetches a guild's starboard config row, or ``None`` if never configured."""
        return await self.fetchrow("SELECT * FROM starboard_config WHERE guild_id = $1;", guild_id)

    async def upsert_config(self, guild_id: int, **columns: object) -> asyncpg.Record:
        """Inserts or updates the given config columns for a guild, returning the row.

        Only the columns passed are written; everything else falls back to its default
        (on insert) or keeps its current value (on update).
        """
        keys = list(columns)
        insert_cols = ', '.join(['guild_id', *keys])
        placeholders = ', '.join(f'${i}' for i in range(1, len(keys) + 2))
        updates = ', '.join(f'{key} = EXCLUDED.{key}' for key in keys) or 'guild_id = EXCLUDED.guild_id'
        query = f"""
            INSERT INTO starboard_config ({insert_cols})
            VALUES ({placeholders})
            ON CONFLICT (guild_id) DO UPDATE SET {updates}
            RETURNING *;
        """
        return await self.fetchrow(query, guild_id, *columns.values())

    # -- entries ----------------------------------------------------------

    async def get_entry(self, message_id: int) -> asyncpg.Record | None:
        """Fetches the starboard entry for an original message id."""
        return await self.fetchrow("SELECT * FROM starboard_entries WHERE message_id = $1;", message_id)

    async def get_entry_by_starboard_message(self, starboard_message_id: int) -> asyncpg.Record | None:
        """Fetches the entry whose mirrored post has the given starboard message id."""
        return await self.fetchrow(
            "SELECT * FROM starboard_entries WHERE starboard_message_id = $1;", starboard_message_id)

    async def create_entry(
        self,
        message_id: int,
        guild_id: int,
        channel_id: int,
        author_id: int,
        starboard_message_id: int,
        star_count: int,
    ) -> None:
        """Records a newly mirrored message."""
        query = """
            INSERT INTO starboard_entries
                (message_id, guild_id, channel_id, author_id, starboard_message_id, star_count)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (message_id) DO UPDATE
                SET starboard_message_id = EXCLUDED.starboard_message_id,
                    star_count = EXCLUDED.star_count;
        """
        await self.execute(query, message_id, guild_id, channel_id, author_id, starboard_message_id, star_count)

    async def update_star_count(self, message_id: int, star_count: int) -> None:
        """Updates the cached star count for an entry."""
        await self.execute(
            "UPDATE starboard_entries SET star_count = $2 WHERE message_id = $1;", message_id, star_count)

    async def delete_entry(self, message_id: int) -> None:
        """Removes a starboard entry by original message id."""
        await self.execute("DELETE FROM starboard_entries WHERE message_id = $1;", message_id)

    async def delete_entries(self, message_ids: Iterable[int]) -> None:
        """Removes several starboard entries by original message id."""
        await self.execute(
            "DELETE FROM starboard_entries WHERE message_id = ANY($1::bigint[]);", list(message_ids))
