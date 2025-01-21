-- Revises: V10
-- Creation Date: 2024-02-12 18:17:50.257503+00:00 UTC
-- Reason: music_playlist_commands

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