from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import asyncpg

__all__ = ('CasesRepository',)


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
