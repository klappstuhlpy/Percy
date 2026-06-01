from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import asyncpg

__all__ = ('GuildsRepository',)


class GuildsRepository(BaseRepository):
    """Data access for the ``guild_config`` and ``guild_gatekeeper`` tables.

    The methods return raw records and scalars; mapping them onto the
    :class:`~app.database.base.GuildConfig` / :class:`~app.database.base.Gatekeeper`
    domain objects (and caching the result) is left to :class:`~app.database.base.Database`.
    """

    # -- guild_config -----------------------------------------------------

    async def get_config_record(self, guild_id: int) -> asyncpg.Record:
        """Fetches the config row for a guild, inserting a default row if absent."""
        async with self.acquire(timeout=300.0) as con:
            record = await con.fetchrow("SELECT * FROM guild_config WHERE id=$1;", guild_id)
            if record is not None:
                return record
            return await con.fetchrow("INSERT INTO guild_config (id) VALUES ($1) RETURNING *;", guild_id)

    async def delete_config(self, guild_id: int) -> None:
        """Deletes the config row for a guild (e.g. when the bot leaves it)."""
        await self.execute("DELETE FROM guild_config WHERE id = $1;", guild_id)

    # -- guild_gatekeeper -------------------------------------------------

    async def get_gatekeeper_record(self, guild_id: int) -> asyncpg.Record | None:
        """Fetches the gatekeeper row for a guild, or ``None`` if none is configured."""
        return await self.fetchrow("SELECT * FROM guild_gatekeeper WHERE id=$1;", guild_id)

    async def get_gatekeeper_members(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches all gatekeeper member rows for a guild."""
        return await self.fetch("SELECT * FROM guild_gatekeeper_members WHERE guild_id=$1;", guild_id)
