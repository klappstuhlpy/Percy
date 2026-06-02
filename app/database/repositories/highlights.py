from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    from collections.abc import Callable

    import asyncpg

__all__ = ('HighlightsRepository',)


class HighlightsRepository(BaseRepository):
    """Data access for the ``highlights`` table.

    Each row is a user's highlight configuration within a guild: the trigger
    ``lookup`` set and the ``blocked`` entity set. Methods return raw records;
    the ``Highlights`` cog wraps them in ``HighlightConfig`` records.
    """

    async def update_config(
            self,
            config_id: int,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> asyncpg.Record:
        """Applies a :class:`~app.database.base.BaseRecord`-style update to a highlight row."""
        query = f"""
            UPDATE highlights
            SET {', '.join(map(key, enumerate(values.keys(), start=2)))}
            WHERE id = $1
            RETURNING *;
        """
        return cast('asyncpg.Record', await (connection or self.db).fetchrow(query, config_id, *values.values()))

    async def get_guild_configs(self, location_id: int) -> list[asyncpg.Record]:
        """Fetches every highlight configuration in a guild."""
        return await self.fetch("SELECT * FROM highlights WHERE location_id = $1;", location_id)

    async def get_config(self, location_id: int, user_id: int) -> asyncpg.Record | None:
        """Fetches a user's highlight configuration in a guild, if it exists."""
        return await self.fetchrow(
            "SELECT * FROM highlights WHERE location_id = $1 AND user_id = $2;", location_id, user_id)

    async def create_config(self, user_id: int, location_id: int) -> asyncpg.Record:
        """Inserts a blank highlight configuration for a user in a guild and returns it."""
        return await self.fetchrow(
            "INSERT INTO highlights (user_id, location_id) VALUES ($1, $2) RETURNING *;", user_id, location_id)

    async def delete_config(self, config_id: int) -> None:
        """Deletes a highlight configuration."""
        await self.execute("DELETE FROM highlights WHERE id = $1;", config_id)

    async def get_import_locations(self, user_id: int, exclude_location_id: int) -> list[asyncpg.Record]:
        """Fetches the guild IDs where a user has highlights, excluding the current guild."""
        query = """
            SELECT location_id
            FROM highlights
            WHERE user_id = $1
            AND location_id != $2
            AND lookup IS NOT NULL;
        """
        return await self.fetch(query, user_id, exclude_location_id)
