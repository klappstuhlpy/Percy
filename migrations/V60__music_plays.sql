-- Revises: V59
-- Creation Date: 2026-10-25 00:00:00.000000+00:00 UTC
-- Reason: persisted play history — recently played per server and per-user top tracks
-- (add '-- migration: no-transaction' below to run outside a transaction)

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
