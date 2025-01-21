-- Revises: V6
-- Creation Date: 2023-05-30 20:43:05.959071 UTC
-- Reason: level_commands

CREATE TABLE IF NOT EXISTS levels
(
    guild_id BIGINT  NOT NULL,
    user_id  BIGINT  NOT NULL,
    level    INTEGER NOT NULL DEFAULT 0,
    xp       BIGINT  NOT NULL DEFAULT 0,
    messages BIGINT  NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE level_config
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