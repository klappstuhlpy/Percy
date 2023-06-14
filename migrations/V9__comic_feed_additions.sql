-- Revises: V9
-- Creation Date: 2023-05-15 08:45:52.476354 UTC
-- Reason: Marvel Comics Configuration Addition

CREATE TABLE IF NOT EXISTS feed_config (
    id SERIAL PRIMARY KEY,
    guild_id BIGINT,
    channel_id BIGINT,
    brand TEXT,
    format TEXT,
    day SMALLINT,
    ping BIGINT,
    pin BOOLEAN
);
