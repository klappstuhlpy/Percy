from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    from collections.abc import Callable

    import asyncpg

__all__ = ('NotesRepository',)


class NotesRepository(BaseRepository):
    """Data access for the ``user_notes`` table.

    Notes are per-user snippets, optionally tagged with a topic and tied to a
    reminder timer. Methods return raw records/scalars; the ``UserNotes`` cog
    wraps them in ``Note`` records and attaches timers.
    """

    async def update_note(
            self,
            note_id: int,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> asyncpg.Record:
        """Applies a :class:`~app.database.base.BaseRecord`-style update to a note row."""
        query = f"""
            UPDATE user_notes
            SET {', '.join(map(key, enumerate(values.keys(), start=2)))}
            WHERE id = $1
            RETURNING *;
        """
        return cast('asyncpg.Record', await (connection or self.db).fetchrow(query, note_id, *values.values()))

    async def create_note(self, owner_id: int, content: str, topic: str | None) -> int:
        """Inserts a note and returns its new ID."""
        query = """
            INSERT INTO user_notes (owner_id, content, topic)
            VALUES ($1, $2, $3)
            RETURNING id;
        """
        return await self.fetchval(query, owner_id, content, topic)

    async def get_note(self, note_id: int, owner_id: int | None = None) -> asyncpg.Record | None:
        """Fetches a single note by ID, optionally scoped to an owner."""
        if owner_id:
            return await self.fetchrow(
                "SELECT * FROM user_notes WHERE id = $1 AND owner_id = $2;", note_id, owner_id)
        return await self.fetchrow("SELECT * FROM user_notes WHERE id = $1", note_id)

    async def get_owner_notes(self, owner_id: int, *, sort_by_topic: bool = False) -> list[asyncpg.Record]:
        """Fetches every note owned by a user, optionally ordered by topic."""
        query = "SELECT * FROM user_notes WHERE owner_id = $1"
        query += " ORDER BY topic;" if sort_by_topic else ";"
        return await self.fetch(query, owner_id)

    async def delete_note(self, note_id: int) -> None:
        """Deletes a single note."""
        await self.execute("DELETE FROM user_notes WHERE id = $1;", note_id)

    async def clear_owner_notes(self, owner_id: int) -> None:
        """Deletes every note owned by a user."""
        await self.execute("DELETE FROM user_notes WHERE owner_id = $1;", owner_id)
