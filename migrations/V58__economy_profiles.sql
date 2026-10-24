-- Revises: V57
-- Creation Date: 2026-10-24 00:00:00.000000+00:00 UTC
-- Reason: profile card — the achievement title a member has equipped
-- (add '-- migration: no-transaction' below to run outside a transaction)

CREATE TABLE IF NOT EXISTS economy_profiles
(
    user_id  BIGINT NOT NULL,
    guild_id BIGINT NOT NULL,
    title    TEXT,
    PRIMARY KEY (user_id, guild_id)
);
