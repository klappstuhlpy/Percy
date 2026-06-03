from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    from collections.abc import Sequence

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

    async def bulk_update_muted_members(self, records: list[dict[str, Any]]) -> None:
        """Replaces the tracked muted-member arrays for a batch of guilds.

        Each record must contain ``guild_id`` and ``result_array`` (the new member ids).
        Cache invalidation for the affected guilds is handled by the caller.
        """
        query = """
            UPDATE guild_config
            SET muted_members = x.result_array
            FROM jsonb_to_recordset($1::jsonb) AS x(guild_id BIGINT, result_array BIGINT[])
            WHERE guild_config.id = x.guild_id;
        """
        await self.execute(query, records)

    # -- Alerts & audit log (guild_config) --------------------------------

    async def enable_alerts(self, guild_id: int, flags_value: int, channel_id: int, webhook_url: str) -> None:
        """Enables alert messages for a guild and stores the alert webhook."""
        query = """
            INSERT INTO guild_config (id, flags, alert_channel_id, alert_webhook_url)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (id)
                DO UPDATE SET flags             = guild_config.flags | EXCLUDED.flags,
                              alert_channel_id  = EXCLUDED.alert_channel_id,
                              alert_webhook_url = EXCLUDED.alert_webhook_url;
        """
        await self.execute(query, guild_id, flags_value, channel_id, webhook_url)
        self.db.get_guild_config.invalidate(guild_id)

    async def get_audit_log_webhook_url(self, guild_id: int) -> str | None:
        """Fetches the stored audit-log webhook url for a guild, if any."""
        return await self.fetchval("SELECT audit_log_webhook_url FROM guild_config WHERE id = $1;", guild_id)

    async def enable_audit_log(self, guild_id: int, flags_value: int, channel_id: int, webhook_url: str) -> None:
        """Enables the audit log for a guild and stores the audit-log webhook."""
        query = """
            INSERT INTO guild_config (id, flags, audit_log_channel_id, audit_log_webhook_url)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (id)
                DO UPDATE SET flags                 = guild_config.flags | $2,
                              audit_log_channel_id  = $3,
                              audit_log_webhook_url = $4;
        """
        await self.execute(query, guild_id, flags_value, channel_id, webhook_url)
        self.db.get_guild_config.invalidate(guild_id)

    async def get_audit_log_flags(self, guild_id: int) -> dict[str, Any] | None:
        """Fetches the audit-log event flag mapping for a guild."""
        return await self.fetchval("SELECT audit_log_flags FROM guild_config WHERE id = $1;", guild_id)

    async def set_audit_log_flags(self, guild_id: int, flags: dict[str, Any]) -> None:
        """Replaces the audit-log event flag mapping for a guild."""
        await self.execute("UPDATE guild_config SET audit_log_flags = $2 WHERE id = $1;", guild_id, flags)
        self.db.get_guild_config.invalidate(guild_id)

    async def disable_protection(self, guild_id: int, updates: str) -> asyncpg.Record:
        """Applies a moderation-disable ``SET`` fragment and returns the freed webhook urls.

        ``updates`` is an internally-built ``SET`` clause (no user input); the query
        returns ``audit_log_webhook_url`` and ``alert_webhook_url`` so the caller can
        clean up the corresponding webhooks.
        """
        query = f"UPDATE guild_config SET {updates} WHERE id=$1 RETURNING audit_log_webhook_url, alert_webhook_url;"
        record = await self.fetchrow(query, guild_id)
        self.db.get_guild_config.invalidate(guild_id)
        return cast('asyncpg.Record', record)

    # -- Gatekeeper & raid/mention protection (guild_config) --------------

    async def setup_gatekeeper(
            self, guild_id: int, flags_value: int, *, create_gatekeeper: bool
    ) -> tuple[asyncpg.Record | None, asyncpg.Record | None]:
        """Enables the gatekeeper flag for a guild, optionally creating its gatekeeper row.

        Returns the (gatekeeper, guild_config) records; the gatekeeper record is
        ``None`` when ``create_gatekeeper`` is ``False``.
        """
        async with self.acquire(timeout=300.0) as conn, conn.transaction():
            gatekeeper_record = None
            if create_gatekeeper:
                gatekeeper_record = await conn.fetchrow(
                    "INSERT INTO guild_gatekeeper(id) VALUES ($1) ON CONFLICT DO NOTHING RETURNING *;", guild_id)

            config_record = await conn.fetchrow(
                """
                INSERT INTO guild_config (id, flags)
                VALUES ($1, $2)
                ON CONFLICT (id)
                    DO UPDATE SET flags = guild_config.flags | $2
                RETURNING *;
                """,
                guild_id, flags_value)

        self.db.get_guild_config.invalidate(guild_id)
        return gatekeeper_record, config_record

    async def toggle_raid_protection(self, guild_id: int, flag: int, enabled: bool | None) -> bool:
        """Toggles raid protection for a guild and returns its resulting state."""
        query = """
            INSERT INTO guild_config (id, flags)
            VALUES ($1, $2)
            ON CONFLICT (id)
                DO UPDATE SET flags = CASE COALESCE($3, NOT (guild_config.flags & $2 = $2))
                                          WHEN TRUE THEN guild_config.flags | $2
                                          WHEN FALSE THEN guild_config.flags & ~$2
                END
            RETURNING COALESCE($3, (flags & $2 = $2));
        """
        result = await self.fetchval(query, guild_id, flag, enabled)
        self.db.get_guild_config.invalidate(guild_id)
        return result

    async def set_mention_count(self, guild_id: int, count: int) -> None:
        """Sets the mention-spam threshold for a guild."""
        query = """
            INSERT INTO guild_config (id, mention_count, safe_automod_entity_ids)
            VALUES ($1, $2, '{}')
            ON CONFLICT (id)
                DO UPDATE SET mention_count = $2;
        """
        await self.execute(query, guild_id, count)
        self.db.get_guild_config.invalidate(guild_id)

    # -- Moderation ignore list (guild_config.safe_automod_entity_ids) ----

    async def add_safe_entities(self, guild_id: int, entity_ids: list[int]) -> None:
        """Adds entities to the moderation ignore list for a guild."""
        query = """
            UPDATE guild_config
            SET safe_automod_entity_ids =
                    ARRAY(SELECT DISTINCT * FROM unnest(COALESCE(safe_automod_entity_ids, '{}') || $2::bigint[]))
            WHERE id = $1;
        """
        await self.execute(query, guild_id, entity_ids)
        self.db.get_guild_config.invalidate(guild_id)

    async def remove_safe_entities(self, guild_id: int, entity_ids: list[int]) -> None:
        """Removes entities from the moderation ignore list for a guild."""
        query = """
            UPDATE guild_config
            SET safe_automod_entity_ids =
                    ARRAY(SELECT element
                          FROM unnest(safe_automod_entity_ids) AS element
                          WHERE NOT (element = ANY ($2::bigint[])))
            WHERE id = $1;
        """
        await self.execute(query, guild_id, entity_ids)
        self.db.get_guild_config.invalidate(guild_id)
