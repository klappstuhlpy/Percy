from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import datetime

    import asyncpg

__all__ = (
    'AniListRepository',
    'PlaylistsRepository',
    'UsersRepository',
)


# -- Users (settings, balance, personal data) ------------------------------


class UsersRepository(BaseRepository):
    """Data access for the ``user_settings`` and ``economy`` tables.

    The methods return raw records and scalars; mapping them onto the
    :class:`~app.database.base.UserConfig` / :class:`~app.database.base.Balance`
    domain objects (and caching the result) is left to :class:`~app.database.base.Database`.
    """

    # -- user_settings ----------------------------------------------------

    async def get_settings_record(self, user_id: int) -> asyncpg.Record:
        """Fetches the settings row for a user, inserting a default row if absent."""
        record = await self.fetchrow("SELECT * FROM user_settings WHERE id = $1;", user_id)
        if record is None:
            record = await self.fetchrow("INSERT INTO user_settings (id) VALUES ($1) RETURNING *;", user_id)
        return record

    async def get_timezone(self, user_id: int) -> str:
        """Fetches the stored timezone for a user."""
        return await self.fetchval("SELECT timezone FROM user_settings WHERE id = $1;", user_id, column='timezone')

    async def set_timezone(self, user_id: int, timezone: str) -> None:
        """Stores (or replaces) a user's timezone."""
        query = """
            INSERT INTO user_settings (id, timezone)
            VALUES ($1, $2)
                ON CONFLICT (id) DO UPDATE SET timezone = $2;
        """
        await self.execute(query, user_id, timezone)
        self.invalidate_cache("user_config_changed", user_id)

    async def clear_timezone(self, user_id: int) -> None:
        """Clears a user's stored timezone."""
        await self.execute("UPDATE user_settings SET timezone = NULL WHERE id=$1;", user_id)
        self.invalidate_cache("user_config_changed", user_id)

    async def delete_personal_data(self, user_id: int) -> None:
        """Removes a user's tracked history (presence, avatar and item) in one transaction."""
        async with self.acquire(timeout=300.0) as conn, conn.transaction():
            await conn.execute(
                """
                DELETE FROM presence_history WHERE uuid = $1;
                DELETE FROM avatar_history WHERE uuid = $1;
                DELETE FROM item_history WHERE uuid = $1;
                """,
                user_id,
            )

    async def export_personal_data(self, user_id: int) -> dict[str, object]:
        """Collects a user's stored personal data for a data-access (export) request.

        Mirrors :meth:`delete_personal_data`: returns the settings row plus the
        presence, name/nickname and avatar history. Avatar image bytes are omitted
        (only the format and timestamp are exported) to keep the payload portable.
        """
        settings = await self.fetchrow("SELECT * FROM user_settings WHERE id = $1;", user_id)
        presence = await self.fetch(
            "SELECT status, status_before, changed_at FROM presence_history WHERE uuid = $1 ORDER BY changed_at;",
            user_id,
        )
        items = await self.fetch(
            "SELECT item_type, item_value, changed_at FROM item_history WHERE uuid = $1 ORDER BY changed_at;",
            user_id,
        )
        avatars = await self.fetch(
            "SELECT format, changed_at FROM avatar_history WHERE uuid = $1 ORDER BY changed_at;",
            user_id,
        )
        return {
            'settings': dict(settings) if settings is not None else None,
            'presence_history': [dict(row) for row in presence],
            'name_history': [dict(row) for row in items],
            'avatar_history': [dict(row) for row in avatars],
        }

    # -- economy ----------------------------------------------------------

    async def get_balance_record(self, user_id: int, guild_id: int) -> asyncpg.Record:
        """Fetches a user's balance row for a guild, inserting an empty one if absent."""
        record = await self.fetchrow(
            "SELECT * FROM economy WHERE user_id = $1 AND guild_id = $2;", user_id, guild_id)
        if not record:
            record = await self.fetchrow(
                "INSERT INTO economy (user_id, guild_id, cash, bank) VALUES ($1, $2, 0, 0) RETURNING *;",
                user_id, guild_id)
        return record

    async def get_guild_balance_records(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches every balance row for a guild."""
        return await self.fetch("SELECT * FROM economy WHERE guild_id = $1;", guild_id)

    async def get_top_balance_records(self, guild_id: int, limit: int) -> list[asyncpg.Record]:
        """Fetches the richest members of a guild (by cash + bank), excluding empty wallets.

        A single ordered query - the leaderboard must not loop per-member balance
        lookups (that was both slow and only sampled an arbitrary subset).
        """
        return await self.fetch(
            "SELECT user_id, cash, bank, (cash + bank) AS total FROM economy "
            "WHERE guild_id = $1 AND (cash + bank) > 0 ORDER BY total DESC LIMIT $2;",
            guild_id, limit)

    async def add_cash(self, user_id: int, guild_id: int, amount: int) -> None:
        """Adds (or, with a negative ``amount``, removes) cash from a user's balance."""
        await self.execute(
            "UPDATE economy SET cash = cash + $1 WHERE user_id = $2 AND guild_id = $3;",
            amount, user_id, guild_id)



# -- Playlists -------------------------------------------------------------


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


# -- AniList ---------------------------------------------------------------


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
