-- Leveling: config, voice XP, XP history, rank cards

-- == V7__level_migration ==
CREATE TABLE IF NOT EXISTS levels
(
    guild_id BIGINT  NOT NULL,
    user_id  BIGINT  NOT NULL,
    level    INTEGER NOT NULL DEFAULT 0,
    xp       BIGINT  NOT NULL DEFAULT 0,
    messages BIGINT  NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS level_config
(
    id                        BIGINT           NOT NULL PRIMARY KEY,
    enabled                   BOOLEAN          NOT NULL DEFAULT FALSE,
    role_stack                BOOLEAN          NOT NULL DEFAULT TRUE,
    base                      INTEGER          NOT NULL DEFAULT 100,
    factor                    DOUBLE PRECISION NOT NULL DEFAULT 1.3,
    min_gain                  INTEGER          NOT NULL DEFAULT 8,
    max_gain                  INTEGER          NOT NULL DEFAULT 15,
    cooldown_rate             INTEGER          NOT NULL DEFAULT 1,
    cooldown_per              INTEGER          NOT NULL DEFAULT 40,
    level_up_channel          BIGINT           NOT NULL DEFAULT 1, -- 0 = don't send, 1 = send to source channel, 2 = DM, else ID of channel
    level_up_message          TEXT             NOT NULL DEFAULT '*Congratulations {user.mention}!* You leveled up to level **{level}**! <:oneup:1113286994378899516>',
    special_level_up_messages JSONB            NOT NULL DEFAULT '{}'::JSONB,
    blacklisted_roles         BIGINT ARRAY     NOT NULL DEFAULT ARRAY []::BIGINT[],
    blacklisted_channels      BIGINT ARRAY              DEFAULT ARRAY []::BIGINT[],
    blacklisted_users         BIGINT ARRAY              DEFAULT ARRAY []::BIGINT[],
    level_roles               JSONB            NOT NULL DEFAULT '{}'::JSONB,
    multiplier_roles          JSONB            NOT NULL DEFAULT '{}'::JSONB,
    multiplier_channels       JSONB            NOT NULL DEFAULT '{}'::JSONB,
    delete_after_leave        BOOLEAN          NOT NULL DEFAULT FALSE
);

-- == V20__community_economy_voice ==
-- Opt-in voice-activity XP for the leveling system.
ALTER TABLE level_config ADD COLUMN IF NOT EXISTS voice_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE level_config ADD COLUMN IF NOT EXISTS voice_xp INTEGER NOT NULL DEFAULT 10;

-- == V22__xp_history ==
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

-- == V49__rank_card_styles ==
-- A member's rank card style, global across servers: a *partial* JSON object of preset keys
-- (theme/accent/bar/font, validated in app/rendering/card_style.py). Absent keys follow the
-- server default, then the built-in default.
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS rank_card JSONB NOT NULL DEFAULT '{}'::JSONB;

-- A server's default card style (same partial shape) and whether members may override it.
-- A locked server renders every card in its default and hides the member editor.
ALTER TABLE level_config ADD COLUMN IF NOT EXISTS card_style JSONB NOT NULL DEFAULT '{}'::JSONB;
ALTER TABLE level_config ADD COLUMN IF NOT EXISTS card_locked BOOLEAN NOT NULL DEFAULT FALSE;
