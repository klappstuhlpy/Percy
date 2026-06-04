-- Revises: V19
-- Creation Date: 2026-06-04 00:00:00.000000+00:00 UTC
-- Reason: autoresponders, server stat counters, economy lottery and voice leveling

-- Trigger-phrase autoresponders (distinct from invoked tags: these fire on matched content).
CREATE TABLE IF NOT EXISTS autoresponders
(
    id         SERIAL    PRIMARY KEY,
    guild_id   BIGINT    NOT NULL,
    trigger    TEXT      NOT NULL,
    response   TEXT      NOT NULL,
    match_type TEXT      NOT NULL DEFAULT 'contains',  -- exact | contains | startswith | regex
    ignore_case BOOLEAN  NOT NULL DEFAULT TRUE,
    enabled    BOOLEAN   NOT NULL DEFAULT TRUE,
    uses       BIGINT    NOT NULL DEFAULT 0,
    created_by BIGINT,
    created_at TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc')
);

-- One trigger phrase per guild (case-insensitive).
CREATE UNIQUE INDEX IF NOT EXISTS autoresponders_guild_trigger_idx
    ON autoresponders (guild_id, lower(trigger));
CREATE INDEX IF NOT EXISTS autoresponders_guild_idx ON autoresponders (guild_id);

-- Self-updating voice channels that display a live server statistic in their name.
CREATE TABLE IF NOT EXISTS guild_stat_counters
(
    id         SERIAL    PRIMARY KEY,
    guild_id   BIGINT    NOT NULL,
    channel_id BIGINT    NOT NULL UNIQUE,
    kind       TEXT      NOT NULL,  -- members | humans | bots | online | boosts | roles | channels
    template   TEXT      NOT NULL DEFAULT '{name}: {count}',
    created_at TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc')
);

CREATE INDEX IF NOT EXISTS guild_stat_counters_guild_idx ON guild_stat_counters (guild_id);

-- A single active lottery per guild, with its pot and weighted ticket entries.
CREATE TABLE IF NOT EXISTS economy_lottery
(
    guild_id    BIGINT    PRIMARY KEY,
    channel_id  BIGINT    NOT NULL,
    ticket_price BIGINT   NOT NULL,
    jackpot     BIGINT    NOT NULL DEFAULT 0,
    ends_at     TIMESTAMP NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc')
);

CREATE TABLE IF NOT EXISTS economy_lottery_entries
(
    guild_id BIGINT NOT NULL REFERENCES economy_lottery (guild_id) ON DELETE CASCADE,
    user_id  BIGINT NOT NULL,
    tickets  BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

-- Opt-in voice-activity XP for the leveling system.
ALTER TABLE level_config ADD COLUMN IF NOT EXISTS voice_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE level_config ADD COLUMN IF NOT EXISTS voice_xp INTEGER NOT NULL DEFAULT 10;
