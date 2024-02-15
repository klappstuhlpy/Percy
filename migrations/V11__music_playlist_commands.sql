-- Revises: V10
-- Creation Date: 2024-02-12 18:17:50.257503+00:00 UTC
-- Reason: music_playlist_commands

ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS music_panel_channel_id TEXT;
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS music_panel_message_id BIGINT;

CREATE TABLE IF NOT EXISTS playlist (
    id SERIAL PRIMARY KEY,
    name TEXT,
    user_id BIGINT,
    created TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS playlist_lookup (
    id SERIAL PRIMARY KEY,
    playlist_id INTEGER REFERENCES playlist (id) ON DELETE CASCADE ON UPDATE NO ACTION,
    name TEXT,
    url TEXT
);