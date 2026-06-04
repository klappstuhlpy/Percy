from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import asyncpg

__all__ = ('AutoRespondersRepository',)


class AutoRespondersRepository(BaseRepository):
    """Data access for the ``autoresponders`` table.

    Autoresponders fire a canned response when a message matches a trigger phrase
    (unlike tags, which are explicitly invoked). Methods return raw records/scalars;
    matching logic lives in the cog's engine so it stays Discord-free and testable.
    """

    async def get_all(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches every autoresponder for a guild, oldest first."""
        return await self.fetch(
            'SELECT * FROM autoresponders WHERE guild_id = $1 ORDER BY id;', guild_id)

    async def get_enabled(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches only the enabled autoresponders for a guild (the on-message hot path)."""
        return await self.fetch(
            'SELECT * FROM autoresponders WHERE guild_id = $1 AND enabled ORDER BY id;', guild_id)

    async def get(self, guild_id: int, trigger: str) -> asyncpg.Record | None:
        """Fetches a single autoresponder by case-insensitive trigger."""
        return await self.fetchrow(
            'SELECT * FROM autoresponders WHERE guild_id = $1 AND lower(trigger) = lower($2);',
            guild_id, trigger,
        )

    async def create(
        self,
        guild_id: int,
        trigger: str,
        response: str,
        *,
        match_type: str,
        ignore_case: bool,
        created_by: int,
    ) -> asyncpg.Record | None:
        """Inserts an autoresponder, returning the row (or ``None`` if the trigger exists)."""
        query = """
            INSERT INTO autoresponders (guild_id, trigger, response, match_type, ignore_case, created_by)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT DO NOTHING
            RETURNING *;
        """
        return await self.fetchrow(query, guild_id, trigger, response, match_type, ignore_case, created_by)

    async def delete(self, guild_id: int, trigger: str) -> asyncpg.Record | None:
        """Deletes an autoresponder by trigger, returning the deleted row (or ``None``)."""
        query = """
            DELETE FROM autoresponders
            WHERE guild_id = $1 AND lower(trigger) = lower($2)
            RETURNING *;
        """
        return await self.fetchrow(query, guild_id, trigger)

    async def set_enabled(self, guild_id: int, trigger: str, enabled: bool) -> asyncpg.Record | None:
        """Toggles an autoresponder on or off, returning the updated row (or ``None``)."""
        query = """
            UPDATE autoresponders
            SET enabled = $3
            WHERE guild_id = $1 AND lower(trigger) = lower($2)
            RETURNING *;
        """
        return await self.fetchrow(query, guild_id, trigger, enabled)

    async def increment_uses(self, responder_id: int) -> None:
        """Bumps the usage counter for a fired autoresponder."""
        await self.execute('UPDATE autoresponders SET uses = uses + 1 WHERE id = $1;', responder_id)
