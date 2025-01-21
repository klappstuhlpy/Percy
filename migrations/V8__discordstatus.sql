-- Revises: V7
-- Creation Date: 2023-06-27 18:24:05.605699 UTC
-- Reason: discordstatus

CREATE TABLE IF NOT EXISTS discord_incidents
(
    id         TEXT,
    name       TEXT,
    status     TEXT,
    started_at TIMESTAMP,
    data       JSONB DEFAULT '{}'::jsonb,
    guild_id   BIGINT PRIMARY KEY NOT NULL,
    channel_id BIGINT             NOT NULL,
    message_id BIGINT
);