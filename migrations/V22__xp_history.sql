-- Revises: V21
-- Creation Date: 2026-06-10 00:00:00.000000+00:00 UTC
-- Reason: xp_history

-- Daily per-guild snapshot of cumulative leveling XP, used to render the
-- dashboard XP time-series chart. One row per (guild, day); the daily snapshot
-- task upserts today's row. `total_xp` is the summed *total* XP across members
-- (resolved with the guild's level spec, not the per-level `xp` column).
CREATE TABLE IF NOT EXISTS xp_history
(
    guild_id BIGINT  NOT NULL,
    day      DATE    NOT NULL,
    total_xp BIGINT  NOT NULL DEFAULT 0,
    gainers  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, day)
);

CREATE INDEX IF NOT EXISTS xp_history_guild_day_idx ON xp_history (guild_id, day);
