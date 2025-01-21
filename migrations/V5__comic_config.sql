-- Revises: V4
-- Creation Date: 2023-05-15 08:45:52.476354 UTC
-- Reason: Comics Configuration

CREATE TABLE IF NOT EXISTS comic_config
(
    id         SERIAL PRIMARY KEY,
    guild_id   BIGINT                   NOT NULL,
    channel_id BIGINT                   NOT NULL,
    brand      TEXT                     NOT NULL,
    format     TEXT                     NOT NULL,
    day        SMALLINT,
    ping       BIGINT,
    pin        BOOLEAN DEFAULT FALSE,
    next_pull  TIMESTAMP WITH TIME ZONE NOT NULL
);
