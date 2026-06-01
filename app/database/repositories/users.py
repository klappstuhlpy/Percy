from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import asyncpg

__all__ = ('UsersRepository',)


class UsersRepository(BaseRepository):
    """Data access for the ``user_settings`` and ``economy`` tables.

    The methods return raw records and scalars; mapping them onto the
    :class:`~app.database.base.UserConfig` / :class:`~app.database.base.Balance`
    domain objects (and caching the result) is left to :class:`~app.database.base.Database`.
    """

    # -- user_settings ----------------------------------------------------

    async def get_settings_record(self, user_id: int) -> asyncpg.Record:
        """Fetches the settings row for a user, inserting a default row if absent."""
        record = await self.fetchrow("SELECT * FROM user_settings WHERE id = $1;", user_id)
        if record is None:
            record = await self.fetchrow("INSERT INTO user_settings (id) VALUES ($1) RETURNING *;", user_id)
        return record

    async def get_timezone(self, user_id: int) -> str:
        """Fetches the stored timezone for a user."""
        return await self.fetchval("SELECT timezone FROM user_settings WHERE id = $1;", user_id, column='timezone')

    # -- economy ----------------------------------------------------------

    async def get_balance_record(self, user_id: int, guild_id: int) -> asyncpg.Record:
        """Fetches a user's balance row for a guild, inserting an empty one if absent."""
        record = await self.fetchrow(
            "SELECT * FROM economy WHERE user_id = $1 AND guild_id = $2;", user_id, guild_id)
        if not record:
            record = await self.fetchrow(
                "INSERT INTO economy (user_id, guild_id, cash, bank) VALUES ($1, $2, 0, 0) RETURNING *;",
                user_id, guild_id)
        return record

    async def get_guild_balance_records(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches every balance row for a guild."""
        return await self.fetch("SELECT * FROM economy WHERE guild_id = $1;", guild_id)
