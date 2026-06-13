from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    from collections.abc import Iterable

    import asyncpg

__all__ = ('GuildsRepository',)


class GuildsRepository(BaseRepository):
    """Data access for the per-guild configuration tables.

    Covers ``guild_config`` and ``guild_gatekeeper`` as well as the ignore list
    (``plonks``) and per-channel command toggles (``command_config``). The methods
    return raw records and scalars; mapping them onto the
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

    async def upsert_gatekeeper(self, guild_id: int, fields: dict[str, Any]) -> None:
        """Ensures a gatekeeper row exists for a guild and updates the given columns.

        ``fields`` keys must be ``guild_gatekeeper`` column names (callers pass a
        validated allow-list). Invalidates the cached gatekeeper record afterwards.
        """
        async with self.acquire() as con, con.transaction():
            await con.execute(
                "INSERT INTO guild_gatekeeper (id) VALUES ($1) ON CONFLICT (id) DO NOTHING;", guild_id)
            if fields:
                set_clause = ", ".join(f'"{col}" = ${i}' for i, col in enumerate(fields, start=2))
                await con.execute(
                    f"UPDATE guild_gatekeeper SET {set_clause} WHERE id = $1;", guild_id, *fields.values())
        self.invalidate_cache("gatekeeper_changed", guild_id)

    # -- plonks (ignore list) --------------------------------------------

    async def is_plonked(self, guild_id: int, entity_ids: Iterable[int]) -> bool:
        """Returns whether any of ``entity_ids`` is on the guild's ignore list."""
        return await self.fetchval(
            "SELECT 1 FROM plonks WHERE guild_id=$1 AND entity_id = ANY($2::bigint[]);",
            guild_id, list(entity_ids)) is not None

    async def get_plonks(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches every ignored entity id for a guild."""
        return await self.fetch("SELECT entity_id FROM plonks WHERE guild_id=$1;", guild_id)

    async def add_plonk(self, guild_id: int, entity_id: int) -> None:
        """Adds a single entity to the guild's ignore list (no-op if already present)."""
        await self.execute(
            "INSERT INTO plonks (guild_id, entity_id) VALUES ($1, $2) ON CONFLICT DO NOTHING;",
            guild_id, entity_id)

    async def bulk_add_plonks(self, guild_id: int, entity_ids: Iterable[int]) -> None:
        """Adds many entities to the guild's ignore list, skipping ones already present."""
        async with self.acquire() as con, con.transaction():
            records = await con.fetch("SELECT entity_id FROM plonks WHERE guild_id=$1;", guild_id)
            current = {r[0] for r in records}
            to_insert = [(guild_id, entity_id) for entity_id in entity_ids if entity_id not in current]
            await con.copy_records_to_table('plonks', columns=['guild_id', 'entity_id'], records=to_insert)

    async def remove_plonks(self, guild_id: int, entity_ids: Iterable[int]) -> None:
        """Removes the given entities from the guild's ignore list."""
        await self.execute(
            "DELETE FROM plonks WHERE guild_id=$1 AND entity_id = ANY($2::bigint[]);",
            guild_id, list(entity_ids))

    async def clear_plonks(self, guild_id: int) -> None:
        """Removes every ignored entity for a guild."""
        await self.execute("DELETE FROM plonks WHERE guild_id=$1;", guild_id)

    # -- command_config (per-channel command toggles) --------------------

    async def get_command_config(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches all per-channel command toggles for a guild."""
        return await self.fetch(
            "SELECT name, channel_id, whitelist FROM command_config WHERE guild_id=$1;", guild_id)

    async def set_command_config(
            self, guild_id: int, channel_id: int | None, name: str, *, whitelist: bool
    ) -> None:
        """Replaces a command toggle for a (guild, channel, command) triple.

        Raises :exc:`asyncpg.UniqueViolationError` if an identical toggle already exists.
        """
        if channel_id is None:
            subcheck = 'channel_id IS NULL'
            args = (guild_id, name)
        else:
            subcheck = 'channel_id=$3'
            args = (guild_id, name, channel_id)

        async with self.acquire() as con, con.transaction():
            await con.execute(
                f"DELETE FROM command_config WHERE guild_id=$1 AND name=$2 AND {subcheck};", *args)
            await con.execute(
                "INSERT INTO command_config (guild_id, channel_id, name, whitelist) VALUES ($1, $2, $3, $4);",
                guild_id, channel_id, name, whitelist)

    async def clear_command_config(self, guild_id: int, name: str) -> None:
        """Removes every toggle (guild-wide and per-channel) for a command in a guild."""
        await self.execute(
            "DELETE FROM command_config WHERE guild_id=$1 AND name=$2;", guild_id, name)

    async def clear_command_config_channel(self, guild_id: int, name: str, channel_id: int) -> None:
        """Removes the toggle for a command in one specific channel."""
        await self.execute(
            "DELETE FROM command_config WHERE guild_id=$1 AND name=$2 AND channel_id=$3;",
            guild_id, name, channel_id)
