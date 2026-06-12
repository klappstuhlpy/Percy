-- Revises: V25
-- Creation Date: 2026-06-12 00:00:00.000000+00:00 UTC
-- Reason: index economy balances by guild for fast per-user lookups and leaderboards

-- The `economy` table only had its `id` primary key, so every balance access —
-- both the per-user lookup (`WHERE user_id = ? AND guild_id = ?`, run by every
-- economy command) and the dashboard leaderboard (`WHERE guild_id = ? ORDER BY
-- cash + bank`) — full-scanned the table across all guilds. This composite index
-- turns those into index scans. Its columns are immutable per row, so it adds no
-- write overhead on the frequent cash/bank UPDATEs (only on INSERT/DELETE).
CREATE INDEX IF NOT EXISTS economy_guild_user_idx ON economy (guild_id, user_id);
