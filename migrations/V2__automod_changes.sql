-- Revises: V1
-- Creation Date: 2023-03-28 13:17:29.679740 UTC
-- Reason: automod_changes

ALTER TABLE guild_config ALTER COLUMN flags SET DEFAULT 0;
ALTER TABLE guild_config ADD COLUMN audit_log_webhook_url TEXT;

-- Previous versions of raid_mod = 2 implied raid_mode = 1
-- Due to this now being interpreted as bit flags this will need to be 3 (1 | 2)
UPDATE guild_config SET flags = 3 WHERE flags = 2;

-- Change the flags to be not null now that there are no null values
ALTER TABLE guild_config ALTER COLUMN flags SET NOT NULL;