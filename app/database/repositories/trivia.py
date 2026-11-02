"""Data access for custom trivia lists and scores (``migrations/V74``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import asyncpg

    from app.services.trivia.lists import CustomQuestion

__all__ = ('TriviaRepository',)


class TriviaRepository(BaseRepository):
    async def create_list(
        self, guild_id: int, name: str, author_id: int, questions: list[CustomQuestion]
    ) -> bool:
        """Store a list atomically; ``False`` if the name is taken."""
        async with self.acquire() as conn, conn.transaction():
            list_id = await conn.fetchval(
                'INSERT INTO trivia_lists (guild_id, name, author_id) VALUES ($1, $2, $3) '
                'ON CONFLICT (guild_id, name) DO NOTHING RETURNING id;',
                guild_id, name, author_id,
            )
            if list_id is None:
                return False
            await conn.executemany(
                'INSERT INTO trivia_questions (list_id, question, answers) VALUES ($1, $2, $3);',
                [(list_id, q.question, list(q.answers)) for q in questions],
            )
        return True

    async def delete_list(self, guild_id: int, name: str) -> bool:
        result = await self.execute('DELETE FROM trivia_lists WHERE guild_id = $1 AND name = $2;', guild_id, name)
        return result.endswith(' 1')

    async def get_lists(self, guild_id: int) -> list[asyncpg.Record]:
        return await self.fetch(
            'SELECT l.name, l.author_id, (SELECT count(*) FROM trivia_questions q WHERE q.list_id = l.id) AS questions '
            'FROM trivia_lists l WHERE l.guild_id = $1 ORDER BY l.name;',
            guild_id,
        )

    async def get_questions(self, guild_id: int, name: str) -> list[asyncpg.Record] | None:
        """The list's questions, or ``None`` if there is no such list."""
        list_id = await self.fetchval('SELECT id FROM trivia_lists WHERE guild_id = $1 AND name = $2;', guild_id, name)
        if list_id is None:
            return None
        return await self.fetch('SELECT question, answers FROM trivia_questions WHERE list_id = $1 ORDER BY id;', list_id)

    async def record_correct(self, guild_id: int, user_id: int) -> None:
        await self.execute(
            'INSERT INTO trivia_scores (guild_id, user_id, correct) VALUES ($1, $2, 1) '
            'ON CONFLICT (guild_id, user_id) DO UPDATE SET correct = trivia_scores.correct + 1;',
            guild_id, user_id,
        )

    async def top_scores(self, guild_id: int, limit: int = 10) -> list[asyncpg.Record]:
        return await self.fetch(
            'SELECT user_id, correct FROM trivia_scores WHERE guild_id = $1 '
            'ORDER BY correct DESC, user_id LIMIT $2;',
            guild_id, limit,
        )
