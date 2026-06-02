from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import asyncpg

__all__ = ('IncidentsRepository',)


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
