-- Revises: V11
-- Creation Date: 2024-02-12 19:10:17.139826+00:00 UTC
-- Reason: temp_channel_commands

CREATE TABLE IF NOT EXISTS temp_channels
(
    guild_id   BIGINT             NOT NULL,
    channel_id BIGINT PRIMARY KEY NOT NULL,
    format     TEXT               NOT NULL DEFAULT '⏳ | %username'
);