-- Moderation behaviour switches, the custom "command is disabled" reply and sticky roles.
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS mod_dm_on_action BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS mod_require_reason BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS mod_reinvite_on_unban BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS mod_default_ban_delete_days SMALLINT NOT NULL DEFAULT 0
    CHECK (mod_default_ban_delete_days BETWEEN 0 AND 7);
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS disabled_command_message TEXT;

-- Sticky roles ride the restrictions table: kind 'stickyrole', the role id in channel_id (part of the key).
ALTER TABLE member_restrictions DROP CONSTRAINT IF EXISTS member_restrictions_kind_check;
ALTER TABLE member_restrictions ADD CONSTRAINT member_restrictions_kind_check
    CHECK (kind IN ('voiceban', 'nickfreeze', 'channelmute', 'stickyrole'));
