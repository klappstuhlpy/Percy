from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    from collections.abc import Callable

    import asyncpg

__all__ = ('LevelingRepository',)


class LevelingRepository(BaseRepository):
    """Data access for the ``level_config`` and ``levels`` tables.

    The methods return raw records and scalars; building the
    ``GuildLevelConfig`` / ``LevelConfig`` domain objects (and caching the guild
    config) is left to the ``Leveling`` cog, which owns the ``cog`` reference each
    record needs.
    """

    # -- level_config (per-guild settings) --------------------------------

    async def get_guild_config_record(self, guild_id: int) -> asyncpg.Record | None:
        """Fetches the leveling config row for a guild, or ``None`` if unconfigured."""
        return await self.fetchrow("SELECT * FROM level_config WHERE id = $1 LIMIT 1;", guild_id)

    async def create_guild_config(self, guild_id: int, enabled: bool) -> asyncpg.Record:
        """Inserts a new leveling config row for a guild and returns it."""
        query = "INSERT INTO level_config (id, enabled) VALUES ($1, $2) RETURNING *;"
        return await self.fetchrow(query, guild_id, enabled)

    async def update_guild_config(
            self,
            guild_id: int,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> asyncpg.Record:
        """Applies a :class:`~app.database.base.BaseRecord`-style update to a guild's config row."""
        query = f"""
            UPDATE level_config
            SET {', '.join(map(key, enumerate(values.keys(), start=2)))}
            WHERE id = $1
            RETURNING *;
        """
        return await (connection or self.db).fetchrow(query, guild_id, *values.values())

    # -- levels (per-member XP rows) --------------------------------------

    async def get_or_create_user_level(self, user_id: int, guild_id: int) -> asyncpg.Record:
        """Fetches a member's level row, inserting a default one if absent."""
        record = await self.fetchrow("SELECT * FROM levels WHERE user_id = $1 AND guild_id = $2;", user_id, guild_id)
        if not record:
            record = await self.fetchrow(
                "INSERT INTO levels (user_id, guild_id) VALUES ($1, $2) RETURNING *;", user_id, guild_id)
        return record

    async def get_user_levels(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches every member level row for a guild."""
        return await self.fetch("SELECT * FROM levels WHERE guild_id = $1;", guild_id)

    async def get_leaderboard(self, guild_id: int, *, limit: int = 10) -> list[asyncpg.Record]:
        """Fetches the top members of a guild ordered by message count."""
        query = """
            SELECT user_id, level, xp, messages
            FROM levels
            WHERE guild_id = $1 AND messages > 0
            ORDER BY messages DESC
            LIMIT $2;
        """
        return await self.fetch(query, guild_id, limit)

    async def get_rank(
            self, user_id: int, guild_id: int, *, connection: asyncpg.Connection | None = None
    ) -> int:
        """Returns a member's XP rank within their guild, or ``0`` if they have none."""
        query = """
            SELECT rank
            FROM (SELECT user_id, guild_id, row_number() OVER (ORDER BY xp DESC) AS rank
                  FROM levels
                  WHERE guild_id = $2) AS rank
            WHERE user_id = $1
              AND guild_id = $2
            LIMIT 1;
        """
        record = await (connection or self.db).fetchval(query, user_id, guild_id)
        return int(record) if record is not None else 0

    async def update_user_level(
            self,
            user_id: int,
            guild_id: int,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> asyncpg.Record:
        """Applies a :class:`~app.database.base.BaseRecord`-style update to a member's level row."""
        query = f"""
            UPDATE levels
            SET {', '.join(map(key, enumerate(values.keys(), start=3)))}
            WHERE user_id = $1 AND guild_id = $2
            RETURNING *;
        """
        return await (connection or self.db).fetchrow(query, user_id, guild_id, *values.values())

    async def delete_member(self, user_id: int, guild_id: int) -> None:
        """Deletes a member's level row for a guild."""
        await self.execute("DELETE FROM levels WHERE user_id = $1 AND guild_id = $2;", user_id, guild_id)
