from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    import asyncpg

__all__ = ('ModerationRepository',)


class ModerationRepository(BaseRepository):
    """Data access for moderation features.

    This owns the dedicated ``guild_lockdowns`` table and the moderation-specific
    columns of ``guild_config`` (the mute role and its tracked members). The
    cached guild config is invalidated whenever those columns change so that
    ``Database.get_guild_config`` stays in sync.
    """

    # -- Lockdowns (guild_lockdowns) --------------------------------------

    async def get_lockdown(self, guild_id: int, channel_id: int) -> asyncpg.Record | None:
        """Fetches the lockdown record for a single channel, or ``None`` if not locked."""
        query = "SELECT * FROM guild_lockdowns WHERE guild_id=$1 AND channel_id=$2;"
        return await self.fetchrow(query, guild_id, channel_id)

    async def get_lockdowns(
            self, guild_id: int, *, channel_ids: list[int] | None = None
    ) -> list[asyncpg.Record]:
        """Fetches the stored lockdown overwrites for a guild.

        Returns ``channel_id``, ``allow`` and ``deny`` rows. When ``channel_ids`` is
        given, only those channels are returned; otherwise every locked channel is.
        """
        if channel_ids is None:
            query = "SELECT channel_id, allow, deny FROM guild_lockdowns WHERE guild_id=$1;"
            return await self.fetch(query, guild_id)

        query = """
            SELECT channel_id, allow, deny
            FROM guild_lockdowns
            WHERE guild_id = $1
              AND channel_id = ANY ($2::bigint[]);
        """
        return await self.fetch(query, guild_id, channel_ids)

    async def add_lockdowns(self, records: list[dict[str, Any]]) -> None:
        """Stores the original channel overwrites for a batch of newly locked channels.

        Each record must contain ``guild_id``, ``channel_id``, ``allow`` and ``deny``.
        Existing rows for the same channel are left untouched.
        """
        query = """
            INSERT INTO guild_lockdowns(guild_id, channel_id, allow, deny)
            SELECT d.guild_id, d.channel_id, d.allow, d.deny
            FROM jsonb_to_recordset($1::jsonb)
                     AS d(
                          guild_id BIGINT,
                          channel_id BIGINT,
                          allow BIGINT,
                          deny BIGINT
                    )
            ON CONFLICT (guild_id, channel_id) DO NOTHING;
        """
        await self.execute(query, records)

    async def clear_lockdowns(self, guild_id: int) -> None:
        """Removes every stored lockdown for a guild."""
        query = "DELETE FROM guild_lockdowns WHERE guild_id=$1;"
        await self.execute(query, guild_id)

    async def remove_lockdowns(self, guild_id: int, channel_ids: list[int]) -> None:
        """Removes the stored lockdowns for the given channels of a guild."""
        query = "DELETE FROM guild_lockdowns WHERE guild_id=$1 AND channel_id = ANY($2::bigint[]);"
        await self.execute(query, guild_id, channel_ids)

    # -- Mute role (guild_config) -----------------------------------------

    async def set_mute_role(self, guild_id: int, role_id: int, members: Sequence[int]) -> None:
        """Binds a mute role and replaces the tracked muted members for a guild."""
        query = """
            INSERT INTO guild_config (id, mute_role_id, muted_members)
            VALUES ($1, $2, $3::bigint[])
            ON CONFLICT (id)
                DO UPDATE SET mute_role_id  = EXCLUDED.mute_role_id,
                              muted_members = EXCLUDED.muted_members;
        """
        await self.execute(query, guild_id, role_id, list(members))
        self.db.get_guild_config.invalidate(guild_id)

    async def create_mute_role(self, guild_id: int, role_id: int) -> None:
        """Binds a freshly created mute role for a guild, leaving muted members untouched."""
        query = """
            INSERT INTO guild_config (id, mute_role_id)
            VALUES ($1, $2)
            ON CONFLICT (id)
                DO UPDATE SET mute_role_id = EXCLUDED.mute_role_id;
        """
        await self.execute(query, guild_id, role_id)
        self.db.get_guild_config.invalidate(guild_id)

    async def unbind_mute_role(self, guild_id: int) -> None:
        """Unbinds the mute role and clears the tracked muted members for a guild."""
        query = "UPDATE guild_config SET (mute_role_id, muted_members) = (NULL, '{}'::bigint[]) WHERE id=$1;"
        await self.execute(query, guild_id)
        self.db.get_guild_config.invalidate(guild_id)
