-- Revises: V30
-- Creation Date: 2026-06-20 00:00:00.000000+00:00 UTC
-- Reason: bot-list vote rewards (global, renewable XP boost)

-- A single active vote reward per user, granted by voting on a bot list (top.gg /
-- discordbotlist.com). The boost is global (applies in every guild the user shares
-- with the bot) and renewable: each vote resets ``expires_at`` to now + the reward
-- window. ``multiplier`` is the XP factor (e.g. 1.10 for +10%).
CREATE TABLE IF NOT EXISTS vote_rewards
(
    user_id      BIGINT           PRIMARY KEY,
    multiplier   DOUBLE PRECISION NOT NULL DEFAULT 1.10,
    expires_at   TIMESTAMP        NOT NULL,
    last_source  TEXT             NOT NULL,  -- top.gg | discordbotlist.com
    last_voted_at TIMESTAMP       NOT NULL DEFAULT (now() at time zone 'utc'),
    total_votes  INTEGER          NOT NULL DEFAULT 0
);
