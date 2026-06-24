from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any, cast

from app.database.repositories.base import BaseRepository
from app.utils.timetools import ensure_utc

if TYPE_CHECKING:
    import asyncpg

__all__ = ('TimersRepository',)


class TimersRepository(BaseRepository):
    """Data access for the ``timers`` table.

    Backs both the generic scheduler (``app.core.timer.TimerManager``) and the
    reminder-specific queries used by the ``Reminder`` cog (rows with
    ``event = 'reminder'``, keyed by the author id stored at
    ``metadata #>> '{args,0}'``). Methods return raw records and scalars; the
    scheduler/cog wrap them in ``Timer`` records.
    """

    # -- Generic scheduling (TimerManager) --------------------------------

    @staticmethod
    def _kwargs_filter(kwargs: dict[str, Any]) -> str:
        """Builds the ``metadata`` filter clause for a kwargs lookup (params start at $2)."""
        return ' AND '.join(
            f"metadata #>> ARRAY['kwargs', '{key}'] = ${i}"
            for i, key in enumerate(kwargs.keys(), start=2)
        )

    async def create_timer(
            self,
            event: str,
            metadata: dict[str, Any],
            expires: datetime.datetime,
            created: datetime.datetime,
            timezone: str,
    ) -> int:
        """Inserts a new timer and returns its generated id."""
        query = """
            INSERT INTO timers (event, metadata, expires, created, timezone)
            VALUES ($1, $2::jsonb, $3, $4, $5)
            RETURNING id;
        """
        return await self.fetchval(
            query, event, metadata,
            ensure_utc(expires).replace(tzinfo=None),
            ensure_utc(created).replace(tzinfo=None),
            timezone,
        )

    async def fetch_by_kwargs(self, event: str, kwargs: dict[str, Any]) -> asyncpg.Record | None:
        """Fetches the first timer for an event matching all of the given metadata kwargs."""
        query = f"SELECT * FROM timers WHERE event = $1 AND {self._kwargs_filter(kwargs)} LIMIT 1;"
        return await self.fetchrow(query, event, *map(str, kwargs.values()))

    async def delete_by_kwargs(self, event: str, kwargs: dict[str, Any]) -> int | None:
        """Deletes the timer for an event matching the given metadata kwargs, returning its id."""
        query = f"DELETE FROM timers WHERE event = $1 AND {self._kwargs_filter(kwargs)} RETURNING id;"
        return await self.fetchval(query, event, *map(str, kwargs.values()))

    async def delete_timer(self, timer_id: int) -> None:
        """Deletes a single timer by its id."""
        await self.delete_where("timers", ("id",), (timer_id,))

    async def fetch_member_timer(self, event: str, guild_id: int, member_id: int) -> asyncpg.Record | None:
        """Fetches an active ``event`` timer targeting ``member_id`` in ``guild_id``.

        Used by member-scoped moderation timers (``tempmute``/``tempban``) whose
        positional ``args`` are ``[guild_id, mod_id, member_id, ...]``; this matches the
        guild at ``args[0]`` and the target at ``args[2]``. Only persisted timers are
        searched (sub-minute timers live in memory and are not stored).
        """
        query = """
            SELECT * FROM timers
            WHERE event = $1
              AND metadata #>> '{args,0}' = $2
              AND metadata #>> '{args,2}' = $3
            LIMIT 1;
        """
        return await self.fetchrow(query, event, str(guild_id), str(member_id))

    async def delete_member_timer(self, event: str, guild_id: int, member_id: int) -> int | None:
        """Deletes the active ``event`` timer targeting ``member_id`` in ``guild_id``, returning its id."""
        query = """
            DELETE FROM timers
            WHERE event = $1
              AND metadata #>> '{args,0}' = $2
              AND metadata #>> '{args,2}' = $3
            RETURNING id;
        """
        return await self.fetchval(query, event, str(guild_id), str(member_id))

    async def get_next_due(
            self, days: int = 7, *, connection: asyncpg.Connection | None = None
    ) -> asyncpg.Record | None:
        """Fetches the soonest-expiring timer due within ``days``, or ``None`` if none is ready."""
        query = """
            SELECT *
            FROM timers
            WHERE (expires AT TIME ZONE timezone) < (CURRENT_TIMESTAMP + $1::interval)
            ORDER BY expires
            LIMIT 1;
        """
        return await (connection or self.db).fetchrow(query, datetime.timedelta(days=days))

    async def update_timer(
            self,
            timer_id: int,
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> asyncpg.Record:
        """Updates a timer row and returns the full updated record."""
        return cast(
            'asyncpg.Record',
            await self.update_returning("timers", ("id",), (timer_id,), values, connection=connection),
        )

    # -- Reminder-specific queries (Reminder cog) -------------------------

    async def get_user_reminders(self, user_id: int) -> list[asyncpg.Record]:
        """Fetches a user's running reminders, soonest first.

        Returns ``(id, expires, message, recurrence_label, shared_count)`` per row; the
        label is ``NULL`` for one-shot reminders and ``shared_count`` is the number of
        friends the reminder is also delivered to (``0`` when not shared).
        """
        query = """
            SELECT id, expires, metadata #>> '{args,2}', metadata #>> '{kwargs,recur_label}',
                   COALESCE(jsonb_array_length(metadata #> '{kwargs,shared}'), 0)
            FROM timers
            WHERE event = 'reminder'
              AND metadata #>> '{args,0}' = $1
            ORDER BY expires;
        """
        return await self.fetch(query, str(user_id))

    async def delete_reminder(self, reminder_id: int, user_id: int) -> str:
        """Deletes a single reminder owned by a user, returning the command status tag."""
        query = """
            DELETE FROM timers WHERE id=$1
            AND event = 'reminder'
            AND metadata #>> '{args,0}' = $2;
        """
        return await self.execute(query, reminder_id, str(user_id))

    async def count_user_reminders(self, user_id: int) -> int:
        """Counts how many reminders a user currently has running."""
        query = """
            SELECT COUNT(*) FROM timers
            WHERE event = 'reminder'
            AND metadata #>> '{args,0}' = $1;
        """
        return await self.fetchval(query, str(user_id))

    async def delete_user_reminders(self, user_id: int) -> None:
        """Deletes every reminder owned by a user."""
        await self.execute(
            "DELETE FROM timers WHERE event = 'reminder' AND metadata #>> '{args,0}' = $1;", str(user_id))
