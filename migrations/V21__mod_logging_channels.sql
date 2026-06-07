-- Revises: V20
-- Creation Date: 2026-06-07 00:00:00.000000+00:00 UTC
-- Reason: mod_logging_channels

ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS mod_log_channel_id BIGINT;
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS message_log_channel_id BIGINT;
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS voice_log_channel_id BIGINT;
