-- Revises: V10
-- Creation Date: 2023-05-28 14:47:43.760632 UTC
-- Reason: Emoji_Migrations

CREATE TABLE IF NOT EXISTS emoji_stats (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT,
    emoji_id BIGINT,
    total INTEGER DEFAULT (0)
);

CREATE INDEX IF NOT EXISTS emoji_stats_guild_id_idx ON emoji_stats (guild_id);
CREATE INDEX IF NOT EXISTS emoji_stats_emoji_id_idx ON emoji_stats (emoji_id);
CREATE UNIQUE INDEX IF NOT EXISTS emoji_stats_uniq_idx ON emoji_stats (guild_id, emoji_id);