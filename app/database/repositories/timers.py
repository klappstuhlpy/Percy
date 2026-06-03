from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import asyncpg

__all__ = ('TimersRepository',)


class TimersRepository(BaseRepository):
    """Data access for the ``timers`` table.

    Currently exposes the reminder-specific queries used by the ``Reminder`` cog
    (rows with ``event = 'reminder'``, keyed by the author id stored at
    ``metadata #>> '{args,0}'``). The generic timer scheduling owned by
    ``app.core.timer.TimerManager`` can move here in a later pass.
    """

    async def get_user_reminders(self, user_id: int) -> list[asyncpg.Record]:
        """Fetches a user's running reminders (id, expiry and message), soonest first."""
        query = """
            SELECT id, expires, metadata #>> '{args,2}'
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
