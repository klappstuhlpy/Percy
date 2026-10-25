from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    from datetime import datetime

    import asyncpg

__all__ = ('MusicPlaysRepository',)


class MusicPlaysRepository(BaseRepository):
    """Data access for the ``music_plays`` table (one row per track start).

    Timestamps are naive UTC. ``user_id`` is NULL for autoplay recommendations.
    """

    async def record(
        self,
        guild_id: int,
        user_id: int | None,
        *,
        title: str,
        author: str | None,
        uri: str | None,
        artwork: str | None,
        length_ms: int | None,
    ) -> None:
        """Appends one play."""
        await self.execute(
            "INSERT INTO music_plays (guild_id, user_id, title, author, uri, artwork, length_ms) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7);",
            guild_id, user_id, title, author, uri, artwork, length_ms,
        )

    async def guild_history(
        self, guild_id: int, *, limit: int = 50, before: datetime | None = None
    ) -> list[asyncpg.Record]:
        """Plays in a guild, newest first; ``before`` pages backwards."""
        return await self.fetch(
            "SELECT * FROM music_plays WHERE guild_id = $1 AND ($2::timestamp IS NULL OR played_at < $2) "
            "ORDER BY played_at DESC LIMIT $3;",
            guild_id, before, limit,
        )

    async def user_recent(self, user_id: int, *, limit: int = 20) -> list[asyncpg.Record]:
        """A user's most recent plays across guilds, newest first."""
        return await self.fetch(
            "SELECT * FROM music_plays WHERE user_id = $1 ORDER BY played_at DESC LIMIT $2;",
            user_id, limit,
        )

    async def user_top_tracks(self, user_id: int, *, days: int = 30, limit: int = 10) -> list[asyncpg.Record]:
        """A user's most played tracks (grouped by ``uri``) within the last ``days``."""
        return await self.fetch(
            """
            SELECT (array_agg(title ORDER BY played_at DESC))[1]     AS title,
                   (array_agg(author ORDER BY played_at DESC))[1]    AS author,
                   uri,
                   (array_agg(artwork ORDER BY played_at DESC))[1]   AS artwork,
                   (array_agg(length_ms ORDER BY played_at DESC))[1] AS length_ms,
                   count(*) AS plays
            FROM music_plays
            WHERE user_id = $1 AND uri IS NOT NULL
              AND played_at > (now() AT TIME ZONE 'utc') - make_interval(days => $2)
            GROUP BY uri
            ORDER BY plays DESC, max(played_at) DESC
            LIMIT $3;
            """,
            user_id, days, limit,
        )

    async def prune(self, *, older_than_days: int = 90) -> int:
        """Deletes plays older than ``older_than_days``; returns the number removed."""
        status = await self.execute(
            "DELETE FROM music_plays WHERE played_at < (now() AT TIME ZONE 'utc') - make_interval(days => $1);",
            older_than_days,
        )
        return int(status.split()[-1]) if isinstance(status, str) else 0
