-- Persisted music player sessions.
--
-- One row per guild with an active (or 24/7) player. Used to:
--   * restore playback after a bot restart / node reconnect, and
--   * back the always-on ("24/7") feature where a player stays connected and
--     keeps a radio stream / looping playlist / autoplay seed running forever.
--
-- Track lists are stored as JSONB arrays of objects: {"uri": ..., "title": ..., "requester_id": ...}

CREATE TABLE IF NOT EXISTS music_sessions (
    guild_id         BIGINT PRIMARY KEY,
    voice_channel_id BIGINT NOT NULL,
    text_channel_id  BIGINT,
    volume           INTEGER NOT NULL DEFAULT 70,
    paused           BOOLEAN NOT NULL DEFAULT FALSE,
    queue_mode       SMALLINT NOT NULL DEFAULT 0,   -- 0 normal, 1 loop (track), 2 loop_all (queue)
    shuffle          BOOLEAN NOT NULL DEFAULT FALSE,
    autoplay         SMALLINT NOT NULL DEFAULT 1,   -- wavelink.AutoPlayMode: 0 enabled, 1 partial, 2 disabled
    always_on        BOOLEAN NOT NULL DEFAULT FALSE,
    always_on_mode   TEXT,                          -- 'radio' | 'playlist' | 'autoplay'
    always_on_source TEXT,                          -- stream URL / playlist query / autoplay seed query
    current_uri      TEXT,
    position         BIGINT NOT NULL DEFAULT 0,     -- playback position of current track, in ms
    tracks           JSONB NOT NULL DEFAULT '[]'::jsonb,
    panel_message_id BIGINT,
    updated_at       TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

-- Fast lookup for "all sessions that should be restored on startup".
CREATE INDEX IF NOT EXISTS music_sessions_always_on_idx ON music_sessions (always_on);
