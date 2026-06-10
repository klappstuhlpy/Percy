from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import datetime

__all__ = ('AniListRepository',)


class AniListRepository(BaseRepository):
    """Persistent storage for AniList OAuth tokens."""

    async def get_token(self, user_id: int) -> tuple[str, datetime.datetime] | None:
        row = await self.fetchrow(
            'SELECT access_token, expires_at FROM anilist_users WHERE user_id = $1',
            user_id,
        )
        if row is None:
            return None
        return row['access_token'], row['expires_at']

    async def upsert_token(self, user_id: int, access_token: str, expires_at: datetime.datetime) -> None:
        await self.execute(
            '''INSERT INTO anilist_users (user_id, access_token, expires_at)
               VALUES ($1, $2, $3)
               ON CONFLICT (user_id) DO UPDATE
               SET access_token = EXCLUDED.access_token,
                   expires_at = EXCLUDED.expires_at''',
            user_id, access_token, expires_at,
        )

    async def delete_token(self, user_id: int) -> bool:
        result = await self.execute(
            'DELETE FROM anilist_users WHERE user_id = $1', user_id,
        )
        return result == 'DELETE 1'
