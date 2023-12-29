-- Revises: V8
-- Creation Date: 2023-05-30 20:43:05.959071 UTC
-- Reason: level_migration

CREATE TABLE IF NOT EXISTS levels (
    PRIMARY KEY (user_id, guild_id),
    user_id BIGINT NOT NULL,
    guild_id BIGINT NOT NULL,
    messages INT DEFAULT 0,
    experience INT DEFAULT 0,
    voice_minutes DOUBLE PRECISION DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS levels_guild_id_user_id_idx ON levels (user_id, guild_id);