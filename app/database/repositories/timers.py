from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any, cast

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    from collections.abc import Callable

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
        return await self.fetchval(query, event, metadata, expires, created, timezone)

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
        await self.execute("DELETE FROM timers WHERE id = $1;", timer_id)

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
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> asyncpg.Record:
        """Applies a :class:`~app.database.base.BaseRecord`-style update to a timer row."""
        query = f"""
            UPDATE timers
            SET {', '.join(map(key, enumerate(values.keys(), start=2)))}
            WHERE id = $1
            RETURNING *;
        """
        return cast(
            'asyncpg.Record',
            await (connection or self.db).fetchrow(query, timer_id, *values.values()),
        )

    # -- Reminder-specific queries (Reminder cog) -------------------------

    async def get_user_reminders(self, user_id: int) -> list[asyncpg.Record]:
        """Fetches a user's running reminders, soonest first.

        Returns ``(id, expires, message, recurrence_label)`` per row; the label is
        ``NULL`` for one-shot reminders.
        """
        query = """
            SELECT id, expires, metadata #>> '{args,2}', metadata #>> '{kwargs,recur_label}'
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
