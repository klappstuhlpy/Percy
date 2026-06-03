from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any, cast

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    from collections.abc import Callable

    import asyncpg

__all__ = ('ComicsRepository',)


class ComicsRepository(BaseRepository):
    """Data access for the ``comic_config`` table.

    Each row is a per-guild, per-brand comic feed subscription. Methods return raw
    records; the comic cog wraps them in ``ComicFeed`` records.
    """

    async def get_config(self, guild_id: int, brand: str) -> asyncpg.Record | None:
        """Fetches a guild's feed configuration for a single brand."""
        return await self.fetchrow(
            "SELECT * FROM comic_config WHERE guild_id = $1 AND brand = $2;", guild_id, brand)

    async def get_next_scheduled(
            self, days: int = 7, *, connection: asyncpg.Connection | None = None
    ) -> asyncpg.Record | None:
        """Fetches the earliest feed due within ``days``, or ``None`` if none is ready."""
        query = """
            SELECT *
            FROM comic_config
            WHERE (next_pull AT TIME ZONE 'UTC') < (CURRENT_TIMESTAMP + $1::interval)
            ORDER BY next_pull
            LIMIT 1;
        """
        return await (connection or self.db).fetchrow(query, datetime.timedelta(days=days))

    async def create_config(self, config: dict[str, Any]) -> None:
        """Inserts a new comic feed configuration.

        ``config`` must provide the columns in order: ``guild_id``, ``channel_id``,
        ``brand``, ``format``, ``day``, ``ping``, ``pin``, ``next_pull``.
        """
        query = """
            INSERT INTO comic_config (guild_id, channel_id, brand, format, day, ping, pin, next_pull)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8);
        """
        await self.execute(query, *config.values())

    async def update_config(
            self,
            config_id: int,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> asyncpg.Record:
        """Applies a :class:`~app.database.base.BaseRecord`-style update to a feed row."""
        query = f"""
            UPDATE comic_config
            SET {', '.join(map(key, enumerate(values.keys(), start=2)))}
            WHERE id = $1
            RETURNING *;
        """
        return cast(
            'asyncpg.Record',
            await (connection or self.db).fetchrow(query, config_id, *values.values()),
        )

    async def set_next_pull(self, next_pull: datetime.datetime, guild_id: int, brand: str) -> None:
        """Updates the scheduled next-pull time for a guild's brand feed."""
        await self.execute(
            "UPDATE comic_config SET next_pull = $1 WHERE guild_id = $2 AND brand = $3;",
            next_pull, guild_id, brand)

    async def delete_config(self, guild_id: int, brand: str) -> None:
        """Removes a guild's feed configuration for a single brand."""
        await self.execute(
            "DELETE FROM comic_config WHERE guild_id = $1 AND brand = $2;", guild_id, brand)
