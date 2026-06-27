from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import datetime
    from collections.abc import Sequence

    import asyncpg

__all__ = (
    'CasesRepository',
    'IncidentsRepository',
    'ModerationRepository',
)


# -- Moderation (lockdowns, mute role, alerts, sentinel) -----------------


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
        self.invalidate_cache("guild_config_changed", guild_id)

    async def create_mute_role(self, guild_id: int, role_id: int) -> None:
        """Binds a freshly created mute role for a guild, leaving muted members untouched."""
        query = """
            INSERT INTO guild_config (id, mute_role_id)
            VALUES ($1, $2)
            ON CONFLICT (id)
                DO UPDATE SET mute_role_id = EXCLUDED.mute_role_id;
        """
        await self.execute(query, guild_id, role_id)
        self.invalidate_cache("guild_config_changed", guild_id)

    async def unbind_mute_role(self, guild_id: int) -> None:
        """Unbinds the mute role and clears the tracked muted members for a guild."""
        query = "UPDATE guild_config SET (mute_role_id, muted_members) = (NULL, '{}'::bigint[]) WHERE id=$1;"
        await self.execute(query, guild_id)
        self.invalidate_cache("guild_config_changed", guild_id)

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
        self.invalidate_cache("guild_config_changed", guild_id)

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
        self.invalidate_cache("guild_config_changed", guild_id)

    async def get_audit_log_flags(self, guild_id: int) -> dict[str, Any] | None:
        """Fetches the audit-log event flag mapping for a guild."""
        return await self.fetchval("SELECT audit_log_flags FROM guild_config WHERE id = $1;", guild_id)

    async def set_audit_log_flags(self, guild_id: int, flags: dict[str, Any]) -> None:
        """Replaces the audit-log event flag mapping for a guild."""
        await self.execute("UPDATE guild_config SET audit_log_flags = $2 WHERE id = $1;", guild_id, flags)
        self.invalidate_cache("guild_config_changed", guild_id)

    async def disable_protection(self, guild_id: int, updates: str) -> asyncpg.Record | None:
        """Applies a moderation-disable ``SET`` fragment and returns the *previous* webhook urls.

        ``updates`` is an internally-built ``SET`` clause (no user input). The query returns
        ``audit_log_webhook_url`` and ``alert_webhook_url`` *as they were before* the update
        (captured in a CTE), so the caller can still delete those Discord webhooks even when
        the fragment nulls the url columns. Returns ``None`` if the guild has no config row.
        """
        query = f"""
            WITH old AS (
                SELECT audit_log_webhook_url, alert_webhook_url FROM guild_config WHERE id = $1
            )
            UPDATE guild_config SET {updates} WHERE id = $1
            RETURNING
                (SELECT audit_log_webhook_url FROM old) AS audit_log_webhook_url,
                (SELECT alert_webhook_url FROM old) AS alert_webhook_url;
        """
        record = await self.fetchrow(query, guild_id)
        self.invalidate_cache("guild_config_changed", guild_id)
        return record

    # -- Sentinel & raid/mention protection (guild_config) --------------

    async def setup_sentinel(
            self, guild_id: int, flags_value: int, *, create_sentinel: bool
    ) -> tuple[asyncpg.Record | None, asyncpg.Record | None]:
        """Enables the sentinel flag for a guild, optionally creating its sentinel row.

        Returns the (sentinel, guild_config) records; the sentinel record is
        ``None`` when ``create_sentinel`` is ``False``.
        """
        async with self.acquire(timeout=300.0) as conn, conn.transaction():
            sentinel_record = None
            if create_sentinel:
                sentinel_record = await conn.fetchrow(
                    "INSERT INTO guild_sentinel(id) VALUES ($1) ON CONFLICT DO NOTHING RETURNING *;", guild_id)

            config_record = await conn.fetchrow(
                """
                INSERT INTO guild_config (id, flags)
                VALUES ($1, $2)
                ON CONFLICT (id)
                    DO UPDATE SET flags = guild_config.flags | $2
                RETURNING *;
                """,
                guild_id, flags_value)

        self.invalidate_cache("guild_config_changed", guild_id)
        if create_sentinel:
            # Bust the cached ``None`` from the pre-setup lookup so the next
            # ``get_guild_sentinel`` builds (and caches) the freshly-created record.
            self.invalidate_cache("sentinel_changed", guild_id)
        return sentinel_record, config_record

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
        self.invalidate_cache("guild_config_changed", guild_id)
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
        self.invalidate_cache("guild_config_changed", guild_id)

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
        self.invalidate_cache("guild_config_changed", guild_id)

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
        self.invalidate_cache("guild_config_changed", guild_id)


# -- Cases (mod_cases, modlog_config) --------------------------------------


class CasesRepository(BaseRepository):
    """Data access for the ``mod_cases`` and ``modlog_config`` tables.

    ``mod_cases`` is an append-only moderation log; each row carries a per-guild
    sequential ``case_index`` (the public "Case #N"). ``modlog_config`` holds the
    destination channel for case announcements. Methods return raw records/scalars; the
    ``ModLog`` cog wraps them in :class:`~app.cogs.modlog.models.ModerationCase`.
    """

    # -- cases ------------------------------------------------------------

    async def create_case(
        self,
        guild_id: int,
        action: str,
        target_id: int,
        moderator_id: int | None,
        reason: str | None,
    ) -> asyncpg.Record:
        """Inserts a case with the next per-guild ``case_index`` and returns the row.

        A transaction-scoped advisory lock serializes index allocation per guild so
        concurrent moderation actions can't collide on the ``(guild_id, case_index)``
        uniqueness constraint.
        """
        query = """
            INSERT INTO mod_cases (guild_id, case_index, action, target_id, moderator_id, reason)
            SELECT $1, COALESCE(MAX(case_index), 0) + 1, $2, $3, $4, $5
            FROM mod_cases WHERE guild_id = $1
            RETURNING *;
        """
        async with self.acquire() as con, con.transaction():
            await con.execute('SELECT pg_advisory_xact_lock($1);', guild_id)
            return await con.fetchrow(query, guild_id, action, target_id, moderator_id, reason)

    async def set_log_message(self, case_id: int, log_message_id: int) -> None:
        """Records the id of the announcement message posted for a case."""
        await self.execute('UPDATE mod_cases SET log_message_id = $2 WHERE id = $1;', case_id, log_message_id)

    async def get_case(self, guild_id: int, case_index: int) -> asyncpg.Record | None:
        """Fetches a single case by its public per-guild index."""
        return await self.fetchrow(
            'SELECT * FROM mod_cases WHERE guild_id = $1 AND case_index = $2;', guild_id, case_index)

    async def get_user_cases(self, guild_id: int, target_id: int, *, limit: int = 25) -> list[asyncpg.Record]:
        """Fetches a target's cases for a guild, newest first."""
        query = """
            SELECT * FROM mod_cases
            WHERE guild_id = $1 AND target_id = $2
            ORDER BY case_index DESC
            LIMIT $3;
        """
        return await self.fetch(query, guild_id, target_id, limit)

    async def count_user_cases(self, guild_id: int, target_id: int) -> int:
        """Counts how many cases a target has in a guild."""
        return await self.fetchval(
            'SELECT COUNT(*) FROM mod_cases WHERE guild_id = $1 AND target_id = $2;', guild_id, target_id)

    async def update_reason(self, guild_id: int, case_index: int, reason: str) -> asyncpg.Record | None:
        """Updates a case's reason and returns the updated row (or ``None`` if missing)."""
        query = """
            UPDATE mod_cases SET reason = $3
            WHERE guild_id = $1 AND case_index = $2
            RETURNING *;
        """
        return await self.fetchrow(query, guild_id, case_index, reason)

    async def delete_case(self, guild_id: int, case_index: int) -> asyncpg.Record | None:
        """Deletes a case and returns the deleted row (or ``None`` if missing)."""
        query = """
            DELETE FROM mod_cases
            WHERE guild_id = $1 AND case_index = $2
            RETURNING *;
        """
        return await self.fetchrow(query, guild_id, case_index)

    async def get_cases(
        self,
        guild_id: int,
        *,
        action: str | None = None,
        moderator_id: int | None = None,
        target_id: int | None = None,
        after: datetime.datetime | None = None,
        before: datetime.datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[asyncpg.Record]:
        """Fetches cases for a guild with optional filters, newest first."""
        args: list[Any] = [guild_id]
        clauses = ['guild_id = $1']

        if action is not None:
            args.append(action)
            clauses.append(f'action = ${len(args)}')
        if moderator_id is not None:
            args.append(moderator_id)
            clauses.append(f'moderator_id = ${len(args)}')
        if target_id is not None:
            args.append(target_id)
            clauses.append(f'target_id = ${len(args)}')
        if after is not None:
            args.append(after)
            clauses.append(f'created_at >= ${len(args)}')
        if before is not None:
            args.append(before)
            clauses.append(f'created_at <= ${len(args)}')

        where = ' AND '.join(clauses)
        args.extend([limit, offset])
        query = f"""
            SELECT * FROM mod_cases
            WHERE {where}
            ORDER BY case_index DESC
            LIMIT ${len(args) - 1} OFFSET ${len(args)};
        """
        return await self.fetch(query, *args)

    async def count_cases(
        self,
        guild_id: int,
        *,
        action: str | None = None,
        moderator_id: int | None = None,
        target_id: int | None = None,
        after: datetime.datetime | None = None,
        before: datetime.datetime | None = None,
    ) -> int:
        """Counts cases for a guild matching the given filters."""
        args: list[Any] = [guild_id]
        clauses = ['guild_id = $1']

        if action is not None:
            args.append(action)
            clauses.append(f'action = ${len(args)}')
        if moderator_id is not None:
            args.append(moderator_id)
            clauses.append(f'moderator_id = ${len(args)}')
        if target_id is not None:
            args.append(target_id)
            clauses.append(f'target_id = ${len(args)}')
        if after is not None:
            args.append(after)
            clauses.append(f'created_at >= ${len(args)}')
        if before is not None:
            args.append(before)
            clauses.append(f'created_at <= ${len(args)}')

        where = ' AND '.join(clauses)
        query = f"SELECT COUNT(*) FROM mod_cases WHERE {where};"
        return await self.fetchval(query, *args)

    async def get_recent_cases(self, guild_id: int, *, since: datetime.datetime) -> list[asyncpg.Record]:
        """Fetches cases created since a timestamp, oldest first (for event streaming)."""
        query = """
            SELECT * FROM mod_cases
            WHERE guild_id = $1 AND created_at >= $2
            ORDER BY created_at ASC;
        """
        return await self.fetch(query, guild_id, since)

    # -- modlog config ----------------------------------------------------

    async def get_modlog_channel(self, guild_id: int) -> int | None:
        """Fetches the configured modlog channel id for a guild, if any."""
        return await self.fetchval('SELECT channel_id FROM modlog_config WHERE guild_id = $1;', guild_id)

    async def set_modlog_channel(self, guild_id: int, channel_id: int | None) -> None:
        """Sets (or clears, with ``None``) the modlog channel for a guild."""
        query = """
            INSERT INTO modlog_config (guild_id, channel_id)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id;
        """
        await self.execute(query, guild_id, channel_id)


# -- Incidents (discord_incidents) -----------------------------------------


class IncidentsRepository(BaseRepository):
    """Data access for the ``discord_incidents`` table.

    Each row links a guild (and its chosen channel/message) to the Discord Status
    incident it is currently tracking. Methods return raw records; the
    ``DiscordStatus`` cog wraps them in ``IncidentItem`` and owns the feed logic.
    """

    # -- reads ------------------------------------------------------------

    async def get_all_subscribers(self) -> list[asyncpg.Record]:
        """Fetches every guild subscribed to the status feed."""
        return await self.fetch("SELECT * FROM discord_incidents;")

    async def get_subscriber(self, guild_id: int) -> asyncpg.Record | None:
        """Fetches a guild's subscription row, or ``None`` if it is not subscribed."""
        return await self.fetchrow("SELECT * FROM discord_incidents WHERE guild_id = $1;", guild_id)

    async def incident_exists(self, incident_id: str, guild_id: int) -> bool:
        """Returns whether a row exists for the given incident and guild."""
        record = await self.fetchrow(
            "SELECT 1 FROM discord_incidents WHERE id = $1 AND guild_id = $2;", incident_id, guild_id)
        return record is not None

    # -- incident tracking ------------------------------------------------

    async def set_incident_id(self, incident_id: str, guild_id: int) -> asyncpg.Record:
        """Assigns an incident ID to a guild's subscription and returns the updated row."""
        return await self.fetchrow(
            "UPDATE discord_incidents SET id = $1 WHERE guild_id = $2 RETURNING *;", incident_id, guild_id)

    async def set_status(self, incident_id: str, guild_id: int, status: str) -> None:
        """Updates the tracked status of a guild's current incident."""
        await self.execute(
            "UPDATE discord_incidents SET status = $3 WHERE id = $1 AND guild_id = $2;",
            incident_id, guild_id, status)

    async def replace_incident(self, new_id: str, old_id: str, status: str, guild_id: int) -> None:
        """Swaps a guild's tracked incident for a newer one with its status."""
        await self.execute(
            "UPDATE discord_incidents SET id = $1, status = $3 WHERE id = $2 AND guild_id = $4;",
            new_id, status, old_id, guild_id)

    async def set_message_id(self, message_id: int, incident_id: str, guild_id: int) -> None:
        """Records the message used to display a guild's incident."""
        await self.execute(
            "UPDATE discord_incidents SET message_id = $1 WHERE id = $2 AND guild_id = $3;",
            message_id, incident_id, guild_id)

    async def create_incident(
            self, incident_id: str, status: str, guild_id: int, channel_id: int
    ) -> asyncpg.Record:
        """Inserts a new tracked incident for a guild and returns the row."""
        return await self.fetchrow(
            "INSERT INTO discord_incidents (id, status, guild_id, channel_id) VALUES ($1, $2, $3, $4) RETURNING *;",
            incident_id, status, guild_id, channel_id)

    async def update_incident_status(
            self, incident_id: str, status: str, guild_id: int
    ) -> asyncpg.Record:
        """Updates a tracked incident's status and returns the updated row."""
        return await self.fetchrow(
            "UPDATE discord_incidents SET status = $2 WHERE id = $1 AND guild_id = $3 RETURNING *;",
            incident_id, status, guild_id)

    # -- subscription management ------------------------------------------

    async def create_subscription(
            self, guild_id: int, channel_id: int, *, connection: asyncpg.Connection | None = None
    ) -> None:
        """Inserts a new subscription for a guild.

        Raises :class:`asyncpg.UniqueViolationError` if the guild is already
        subscribed; the caller is expected to handle that case.
        """
        query = "INSERT INTO discord_incidents (guild_id, channel_id) VALUES ($1, $2) RETURNING *;"
        await (connection or self.db).execute(query, guild_id, channel_id)

    async def update_channel(self, guild_id: int, channel_id: int) -> None:
        """Changes the channel a guild's subscription posts to."""
        await self.execute(
            "UPDATE discord_incidents SET channel_id = $2 WHERE guild_id = $1;", guild_id, channel_id)

    async def unsubscribe(self, guild_id: int) -> None:
        """Removes a guild's subscription."""
        await self.execute("DELETE FROM discord_incidents WHERE guild_id = $1;", guild_id)
