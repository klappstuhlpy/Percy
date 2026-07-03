-- Revises: V33
-- Creation Date: 2026-07-02 00:00:00.000000+00:00 UTC
-- Reason: economy expansion (jobs, pets, quests, achievements, prestige, guild settings)

-- Per-guild economy tuning; a missing row means "all defaults".
CREATE TABLE IF NOT EXISTS economy_settings
(
    guild_id          BIGINT PRIMARY KEY,
    payout_multiplier DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    rob_enabled       BOOLEAN          NOT NULL DEFAULT TRUE,
    daily_base        INTEGER          NOT NULL DEFAULT 250,
    max_bet           BIGINT -- NULL = uncapped
);

-- Weekly/monthly claim timestamps live next to the daily bookkeeping. A member
-- may claim weekly/monthly before ever claiming daily, so last_claim becomes nullable.
ALTER TABLE economy_dailies ADD COLUMN IF NOT EXISTS last_weekly TIMESTAMP;
ALTER TABLE economy_dailies ADD COLUMN IF NOT EXISTS last_monthly TIMESTAMP;
ALTER TABLE economy_dailies ALTER COLUMN last_claim DROP NOT NULL;

-- A member's current job on the static ladder (job ids live in code).
CREATE TABLE IF NOT EXISTS economy_jobs
(
    user_id  BIGINT    NOT NULL,
    guild_id BIGINT    NOT NULL,
    job_id   TEXT      NOT NULL,
    shifts   INTEGER   NOT NULL DEFAULT 0, -- lifetime shifts worked (never resets on job change)
    hired_at TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'),
    PRIMARY KEY (user_id, guild_id)
);

-- Permanent prestige levels; each level grants a payout multiplier bonus.
CREATE TABLE IF NOT EXISTS economy_prestige
(
    user_id       BIGINT  NOT NULL,
    guild_id      BIGINT  NOT NULL,
    level         INTEGER NOT NULL DEFAULT 0,
    last_prestige TIMESTAMP,
    PRIMARY KEY (user_id, guild_id)
);

-- Earned achievement badges (achievement ids live in code).
CREATE TABLE IF NOT EXISTS economy_achievements
(
    user_id     BIGINT    NOT NULL,
    guild_id    BIGINT    NOT NULL,
    achievement TEXT      NOT NULL,
    earned_at   TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'),
    PRIMARY KEY (user_id, guild_id, achievement)
);

-- One pet per member per guild (species ids live in code).
CREATE TABLE IF NOT EXISTS economy_pets
(
    user_id    BIGINT    NOT NULL,
    guild_id   BIGINT    NOT NULL,
    species    TEXT      NOT NULL,
    name       TEXT      NOT NULL,
    adopted_at TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'),
    last_fed   TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'),
    last_claim TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'),
    PRIMARY KEY (user_id, guild_id)
);

-- Daily quest board: rows are generated lazily per member per day.
CREATE TABLE IF NOT EXISTS economy_quests
(
    user_id   BIGINT  NOT NULL,
    guild_id  BIGINT  NOT NULL,
    day       DATE    NOT NULL,
    quest     TEXT    NOT NULL, -- pool key, e.g. 'fish_n'
    kind      TEXT    NOT NULL, -- progress hook, e.g. 'fish'
    goal      INTEGER NOT NULL,
    progress  INTEGER NOT NULL DEFAULT 0,
    reward    INTEGER NOT NULL,
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (user_id, guild_id, day, quest)
);

-- Quest completions are counted for achievements; keep lookups cheap.
CREATE INDEX IF NOT EXISTS economy_quests_completed_idx
    ON economy_quests (user_id, guild_id) WHERE completed;
