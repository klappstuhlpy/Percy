-- Revises: V11
-- Creation Date: 2023-06-27 18:24:05.605699 UTC
-- Reason: discordstatus_addition

CREATE TABLE IF NOT EXISTS discord_incidents(
    id TEXT NOT NULL PRIMARY KEY,
    name TEXT,
    status TEXT,
    started_at TIMESTAMP,
    data JSONB DEFAULT '{}'::jsonb
)