from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import datetime

    import asyncpg

__all__ = ('PlaylistsRepository',)


class PlaylistsRepository(BaseRepository):
    """Data access for the ``playlist`` and ``playlist_lookup`` tables.

    A ``playlist`` row owns many ``playlist_lookup`` rows (its tracks). Methods
    return raw records; the music cog wraps them in ``Playlist`` / ``PlaylistTrack``
    records.
    """

    async def create_playlist(self, user_id: int, name: str, created: datetime.datetime) -> int | None:
        """Creates a playlist for a user and returns its new id."""
        return await self.fetchval(
            "INSERT INTO playlist (user_id, name, created) VALUES ($1, $2, $3) RETURNING id;",
            user_id, name, created)

    async def get_playlist_by_id(self, playlist_id: int) -> asyncpg.Record | None:
        """Fetches a playlist by its id."""
        return await self.fetchrow("SELECT * FROM playlist WHERE id = $1;", playlist_id)

    async def get_playlist_by_name(self, user_id: int, name: str) -> asyncpg.Record | None:
        """Fetches a user's playlist by (case-insensitive) name."""
        return await self.fetchrow(
            "SELECT * FROM playlist WHERE LOWER(name) = $1 AND user_id = $2;", name.lower(), user_id)

    async def get_liked_songs(self, user_id: int) -> asyncpg.Record | None:
        """Fetches a user's static ``Liked Songs`` playlist."""
        return await self.fetchrow(
            "SELECT * FROM playlist WHERE user_id = $1 AND name = 'Liked Songs' LIMIT 1;", user_id)

    async def get_user_playlists(self, user_id: int) -> list[asyncpg.Record]:
        """Fetches every playlist owned by a user."""
        return await self.fetch("SELECT * FROM playlist WHERE user_id = $1;", user_id)

    async def get_playlist_tracks(self, playlist_id: int) -> list[asyncpg.Record]:
        """Fetches every track belonging to a playlist."""
        return await self.fetch("SELECT * FROM playlist_lookup WHERE playlist_id = $1;", playlist_id)

    async def add_track(self, playlist_id: int, name: str, url: str | None) -> asyncpg.Record:
        """Adds a track to a playlist and returns the inserted row."""
        return cast(
            'asyncpg.Record',
            await self.fetchrow(
                "INSERT INTO playlist_lookup (playlist_id, name, url) VALUES ($1, $2, $3) RETURNING *;",
                playlist_id, name, url),
        )

    async def remove_track(self, track_id: int) -> None:
        """Removes a single track by its id."""
        await self.execute("DELETE FROM playlist_lookup WHERE id = $1;", track_id)

    async def clear_tracks(self, playlist_id: int) -> None:
        """Removes every track from a playlist."""
        await self.execute("DELETE FROM playlist_lookup WHERE playlist_id = $1;", playlist_id)

    async def delete_playlist(self, playlist_id: int) -> None:
        """Deletes a playlist together with all of its tracks."""
        await self.execute("DELETE FROM playlist WHERE id = $1;", playlist_id)
        await self.execute("DELETE FROM playlist_lookup WHERE playlist_id = $1;", playlist_id)
