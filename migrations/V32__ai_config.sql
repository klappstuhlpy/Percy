-- AI-native rewrite: per-guild AI feature flags + per-channel overrides.
--
-- ai_flags is a bitfield mirroring the existing AutoModFlags (guild_config.flags):
-- each bit toggles one AI feature server-wide. Default 0 = all AI features off, so
-- guilds opt in explicitly (the rewrite ships dark). See docs/ai/.
--
-- guild_ai_channel_overrides lets a guild override the server-wide flags per channel:
--   flags_mask   = which feature bits this row controls
--   enabled_mask = the on/off value for those controlled bits
-- Effective flags for a channel = (server & ~flags_mask) | (enabled_mask & flags_mask).

ALTER TABLE guild_config
    ADD COLUMN IF NOT EXISTS ai_flags INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS guild_ai_channel_overrides
(
    guild_id     BIGINT  NOT NULL,
    channel_id   BIGINT  NOT NULL,
    flags_mask   INTEGER NOT NULL DEFAULT 0,
    enabled_mask INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE INDEX IF NOT EXISTS guild_ai_channel_overrides_guild_idx
    ON guild_ai_channel_overrides (guild_id);
