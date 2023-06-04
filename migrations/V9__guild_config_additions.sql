-- Revises: V8
-- Creation Date: 2023-05-06 07:52:22.959865 UTC
-- Reason: Guild Config Additions

CREATE TABLE IF NOT EXISTS guild_config (
    id BIGINT PRIMARY KEY,
    poll_channel BIGINT,
    poll_ping_role BIGINT,
    poll_reason_channel BIGINT,
    help_forum_category BIGINT,
    forum_low_quality_check BOOLEAN
);

