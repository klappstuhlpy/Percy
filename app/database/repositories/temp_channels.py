from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    from collections.abc import Callable

    import asyncpg

__all__ = ('TempChannelsRepository',)


class TempChannelsRepository(BaseRepository):
    """Data access for the ``temp_channels`` table.

    Each row marks a voice channel as a hub that spawns temporary voice channels,
    together with the naming ``format`` to apply. Methods return raw records; the
    ``TempChannels`` cog wraps them in ``TempChannel`` records.
    """

    async def update_channel(
            self,
            guild_id: int,
            channel_id: int,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> asyncpg.Record:
        """Applies a :class:`~app.database.base.BaseRecord`-style update to a temp-channel row."""
        query = f"""
            UPDATE temp_channels
            SET {', '.join(map(key, enumerate(values.keys(), start=3)))}
            WHERE guild_id = $1 AND channel_id = $2
            RETURNING *;
        """
        return cast(
            'asyncpg.Record',
            await (connection or self.db).fetchrow(query, guild_id, channel_id, *values.values()),
        )

    async def get_guild_channels(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches every temp-channel hub configured in a guild."""
        return await self.fetch("SELECT * FROM temp_channels WHERE guild_id = $1;", guild_id)

    async def get_channel(self, guild_id: int, channel_id: int) -> asyncpg.Record | None:
        """Fetches a single temp-channel hub by guild and channel."""
        return await self.fetchrow(
            "SELECT * FROM temp_channels WHERE guild_id = $1 AND channel_id = $2;", guild_id, channel_id)

    async def create_channel(self, guild_id: int, channel_id: int, fmt: str) -> None:
        """Registers a voice channel as a temp-channel hub with the given name format."""
        await self.execute(
            "INSERT INTO temp_channels (guild_id, channel_id, format) VALUES ($1, $2, $3);",
            guild_id, channel_id, fmt)

    async def delete_channel(self, guild_id: int, channel_id: int) -> None:
        """Removes a single temp-channel hub."""
        await self.execute(
            "DELETE FROM temp_channels WHERE guild_id = $1 AND channel_id = $2;", guild_id, channel_id)

    async def delete_guild_channels(self, guild_id: int) -> None:
        """Removes every temp-channel hub in a guild."""
        await self.execute("DELETE FROM temp_channels WHERE guild_id = $1;", guild_id)
