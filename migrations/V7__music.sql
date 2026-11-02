-- Music: playlists, sessions, DJ mode, play history

-- == V11__music_playlist_commands ==
ALTER TABLE guild_config
    ADD COLUMN IF NOT EXISTS music_panel_channel_id BIGINT;
ALTER TABLE guild_config
    ADD COLUMN IF NOT EXISTS music_panel_message_id BIGINT;

CREATE TABLE IF NOT EXISTS playlist
(
    id      SERIAL PRIMARY KEY,
    name    TEXT                     NOT NULL,
    user_id BIGINT                   NOT NULL,
    created TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_lookup
(
    id          SERIAL PRIMARY KEY,
    playlist_id INTEGER NOT NULL REFERENCES playlist (id) ON DELETE CASCADE ON UPDATE NO ACTION,
    name        TEXT    NOT NULL,
    url         TEXT    NOT NULL
);

-- == V29__music_sessions ==
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

-- == V30__music_dj_mode ==
-- Configurable DJ access mode for the music player panel and commands.
--
-- 0 = everyone (default, current behaviour: anyone in the voice channel can interact)
-- 1 = dj_only (only DJ role holders or manage_guild permission can control playback)
-- 2 = hybrid  (everyone can use basic controls; destructive actions require DJ)

ALTER TABLE guild_config
    ADD COLUMN IF NOT EXISTS music_dj_mode SMALLINT NOT NULL DEFAULT 0;

-- == V60__music_plays ==
CREATE TABLE IF NOT EXISTS music_plays (
    id        BIGSERIAL PRIMARY KEY,
    guild_id  BIGINT NOT NULL,
    user_id   BIGINT,
    title     TEXT NOT NULL,
    author    TEXT,
    uri       TEXT,
    artwork   TEXT,
    length_ms BIGINT,
    played_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS music_plays_guild_played_idx ON music_plays (guild_id, played_at DESC);
CREATE INDEX IF NOT EXISTS music_plays_user_played_idx ON music_plays (user_id, played_at DESC);

-- == V61__playlist_customization ==
-- artist and cover so the dashboard can show them instead of "Liked song"

ALTER TABLE playlist ADD COLUMN IF NOT EXISTS thumbnail TEXT;

ALTER TABLE playlist_lookup ADD COLUMN IF NOT EXISTS author TEXT;
ALTER TABLE playlist_lookup ADD COLUMN IF NOT EXISTS artwork TEXT;
