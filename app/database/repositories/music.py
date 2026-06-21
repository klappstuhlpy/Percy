from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import asyncpg

__all__ = ('MusicSessionsRepository',)


class MusicSessionsRepository(BaseRepository):
    """Data access for the ``music_sessions`` table.

    Stores a snapshot of each guild's active player so playback can be restored
    after a restart/node reconnect, and backs the always-on ("24/7") feature.
    The music cog (re)hydrates ``Player`` state from these rows.
    """

    async def get_session(self, guild_id: int) -> asyncpg.Record | None:
        """Fetches the persisted session for a guild, if any."""
        return await self.fetchrow("SELECT * FROM music_sessions WHERE guild_id = $1;", guild_id)

    async def get_all_sessions(self) -> list[asyncpg.Record]:
        """Fetches every persisted session (used to restore players on startup)."""
        return await self.fetch("SELECT * FROM music_sessions;")

    async def upsert_session(
        self,
        guild_id: int,
        *,
        voice_channel_id: int,
        text_channel_id: int | None,
        panel_message_id: int | None,
        volume: int,
        paused: bool,
        queue_mode: int,
        shuffle: bool,
        autoplay: int,
        always_on: bool,
        always_on_mode: str | None,
        always_on_source: str | None,
        current_uri: str | None,
        position: int,
        tracks: list[dict[str, Any]],
    ) -> None:
        """Inserts or updates the full session snapshot for a guild."""
        await self.execute(
            """
            INSERT INTO music_sessions (
                guild_id, voice_channel_id, text_channel_id, panel_message_id, volume, paused,
                queue_mode, shuffle, autoplay, always_on, always_on_mode, always_on_source,
                current_uri, position, tracks, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15::jsonb,
                    (now() AT TIME ZONE 'utc'))
            ON CONFLICT (guild_id) DO UPDATE SET
                voice_channel_id = EXCLUDED.voice_channel_id,
                text_channel_id  = EXCLUDED.text_channel_id,
                panel_message_id = EXCLUDED.panel_message_id,
                volume           = EXCLUDED.volume,
                paused           = EXCLUDED.paused,
                queue_mode       = EXCLUDED.queue_mode,
                shuffle          = EXCLUDED.shuffle,
                autoplay         = EXCLUDED.autoplay,
                always_on        = EXCLUDED.always_on,
                always_on_mode   = EXCLUDED.always_on_mode,
                always_on_source = EXCLUDED.always_on_source,
                current_uri      = EXCLUDED.current_uri,
                position         = EXCLUDED.position,
                tracks           = EXCLUDED.tracks,
                updated_at       = EXCLUDED.updated_at;
            """,
            guild_id, voice_channel_id, text_channel_id, panel_message_id, volume, paused,
            queue_mode, shuffle, autoplay, always_on, always_on_mode, always_on_source,
            current_uri, position, json.dumps(tracks),
        )

    async def delete_session(self, guild_id: int) -> None:
        """Removes a guild's persisted session (e.g. on disconnect)."""
        await self.delete_where("music_sessions", ("guild_id",), (guild_id,))
