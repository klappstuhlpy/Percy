-- Revises: V1
-- Creation Date: 2023-03-28 13:19:24.941121 UTC
-- Reason: lockdowns

CREATE TABLE IF NOT EXISTS guild_lockdowns
(
    guild_id   BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    allow      BIGINT NOT NULL,
    deny       BIGINT NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);