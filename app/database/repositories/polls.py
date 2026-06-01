from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import datetime
    from collections.abc import Callable

    import asyncpg

__all__ = ('PollsRepository',)


class PollsRepository(BaseRepository):
    """Data access for the ``polls`` table.

    ``poll_entry`` is a Postgres composite type stored in the ``entries`` array
    column of ``polls`` rather than a standalone table, so it is handled here too.
    The methods return raw records/scalars; building :class:`Poll` objects is left
    to the ``Polls`` cog, which owns the ``cog`` reference each record needs.
    """

    # Whitelisted ``ORDER BY`` fragments, keyed by the cog's sort flag values.
    _SORT_CLAUSES: ClassVar[dict[str, str]] = {
        'id': 'id',
        'new': "metadata #>> ARRAY['kwargs', 'published'] DESC",
        'old': "metadata #>> ARRAY['kwargs', 'published'] ASC",
        'most votes': "metadata #>> ARRAY['kwargs', 'votes'] DESC",
        'least votes': "metadata #>> ARRAY['kwargs', 'votes'] ASC",
    }

    async def create(
            self,
            poll_id: int,
            channel_id: int,
            message_id: int,
            guild_id: int,
            published: datetime.datetime,
            expires: datetime.datetime,
            metadata: dict[str, Any],
    ) -> int:
        """Inserts a new poll and returns its generated ``id``."""
        query = """
            INSERT INTO polls (id, channel_id, message_id, guild_id, published, expires, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            RETURNING id;
        """
        return await self.fetchval(
            query, poll_id, channel_id, message_id, guild_id, published, expires, metadata)

    async def get(self, poll_id: int, guild_id: int) -> asyncpg.Record | None:
        """Fetches a single poll scoped to a guild."""
        query = "SELECT * FROM polls WHERE id = $1 AND guild_id = $2 LIMIT 1;"
        return await self.fetchrow(query, poll_id, guild_id)

    async def get_by_id(self, poll_id: int) -> asyncpg.Record | None:
        """Fetches a single poll by its ID, regardless of guild."""
        query = "SELECT * FROM polls WHERE id = $1 LIMIT 1;"
        return await self.fetchrow(query, poll_id)

    async def get_for_guild(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches every poll belonging to a guild."""
        query = "SELECT * FROM polls WHERE guild_id = $1;"
        return await self.fetch(query, guild_id)

    async def get_all_ids(self) -> list[asyncpg.Record]:
        """Fetches the IDs of every poll (used to generate a unique new ID)."""
        return await self.fetch("SELECT id FROM polls;")

    async def search_for_guild(
            self, guild_id: int, *, sort: str | None = None, active: bool = False
    ) -> list[asyncpg.Record]:
        """Fetches a guild's polls, optionally filtered to running polls and sorted.

        ``sort`` is matched against a whitelist of allowed ``ORDER BY`` fragments,
        falling back to sorting by ``id`` for unknown values.
        """
        sort_clause = self._SORT_CLAUSES.get(sort or 'id', 'id')
        running = "AND metadata #>> ARRAY['kwargs', 'running'] = true" if active else ''
        query = f"SELECT * FROM polls WHERE guild_id = $1 {running} ORDER BY {sort_clause};"
        return await self.fetch(query, guild_id)

    async def update(
            self,
            poll_id: int,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> asyncpg.Record:
        """Applies a :class:`~app.database.base.BaseRecord`-style update to a poll row."""
        query = f"""
            UPDATE polls
            SET {', '.join(map(key, enumerate(values.keys(), start=2)))}
            WHERE id = $1
            RETURNING *;
        """
        return await (connection or self.db).fetchrow(query, poll_id, *values.values())

    async def delete(self, poll_id: int) -> None:
        """Deletes a poll row."""
        await self.execute("DELETE FROM polls WHERE id = $1;", poll_id)
