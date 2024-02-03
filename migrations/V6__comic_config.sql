-- Revises: V5
-- Creation Date: 2023-05-15 08:45:52.476354 UTC
-- Reason: Comics Configuration

CREATE TABLE IF NOT EXISTS comic_config (
    id SERIAL PRIMARY KEY,
    guild_id BIGINT,
    channel_id BIGINT,
    brand TEXT,
    format TEXT,
    day SMALLINT,
    ping BIGINT,
    pin BOOLEAN,
    next_pull TIMESTAMP WITH TIME ZONE
);
