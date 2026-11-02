-- Moderation: cases, mod logging, warn policy/points, member restrictions, moderation settings

-- == V17__moderation_cases ==
CREATE TABLE IF NOT EXISTS modlog_config
(
    guild_id   BIGINT PRIMARY KEY,
    channel_id BIGINT
);

CREATE TABLE IF NOT EXISTS mod_cases
(
    id             SERIAL    PRIMARY KEY,
    guild_id       BIGINT    NOT NULL,
    case_index     INTEGER   NOT NULL,
    action         TEXT      NOT NULL,
    target_id      BIGINT    NOT NULL,
    moderator_id   BIGINT,
    reason         TEXT,
    log_message_id BIGINT,
    created_at     TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'),
    UNIQUE (guild_id, case_index)
);

CREATE INDEX IF NOT EXISTS mod_cases_guild_target_idx ON mod_cases (guild_id, target_id);

-- == V21__mod_logging_channels ==
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS mod_log_channel_id BIGINT;
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS message_log_channel_id BIGINT;
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS voice_log_channel_id BIGINT;

-- == V48__case_numbers_are_never_reused ==
-- The highest case number a guild has ever been given. ``MAX(case_index) + 1`` alone handed a
-- deleted newest case's number to the next action, while old modlog posts still pointed at it.
-- Existing cases keep their numbers: 0 here means "not tracked yet", and the allocator takes
-- whichever is higher, this or the live maximum, so the first case after this migration
-- continues from the current history.
ALTER TABLE modlog_config ADD COLUMN IF NOT EXISTS last_case_index INTEGER NOT NULL DEFAULT 0;

-- == V62__warn_policy ==
-- Case expiry / pardon, plus per-guild warn expiry and escalation thresholds.
ALTER TABLE mod_cases ADD COLUMN IF NOT EXISTS expires_at  TIMESTAMP;
ALTER TABLE mod_cases ADD COLUMN IF NOT EXISTS pardoned_at TIMESTAMP;
ALTER TABLE mod_cases ADD COLUMN IF NOT EXISTS pardoned_by BIGINT;

CREATE TABLE IF NOT EXISTS warn_policy
(
    guild_id    BIGINT  PRIMARY KEY,
    expire_days INTEGER CHECK (expire_days IS NULL OR expire_days > 0)
);

CREATE TABLE IF NOT EXISTS warn_thresholds
(
    guild_id         BIGINT  NOT NULL,
    warn_count       INTEGER NOT NULL CHECK (warn_count > 0),
    action           TEXT    NOT NULL,
    duration_seconds INTEGER,
    PRIMARY KEY (guild_id, warn_count)
);

-- == V69__warn_points ==
-- Point-weighted warnings: a warn carries points (every older warn is worth 1), and a guild
-- can define named reason presets worth N points. Thresholds compare the active point total.
ALTER TABLE mod_cases ADD COLUMN IF NOT EXISTS points INTEGER NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS warn_reasons
(
    guild_id    BIGINT  NOT NULL,
    name        TEXT    NOT NULL,
    points      INTEGER NOT NULL CHECK (points BETWEEN 1 AND 100),
    description TEXT,
    PRIMARY KEY (guild_id, name)
);

-- == V70__member_restrictions ==
-- Member restrictions beyond mutes: voice bans, frozen nicknames and per-channel mutes.
-- One row per (member, kind, channel); channel_id is 0 when the kind is not channel-scoped.
-- ``value`` holds the frozen nickname; ``expires_at`` (naive UTC) the optional end.
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS voiceban_role_id BIGINT;

CREATE TABLE IF NOT EXISTS member_restrictions
(
    guild_id   BIGINT      NOT NULL,
    user_id    BIGINT      NOT NULL,
    kind       TEXT        NOT NULL CHECK (kind IN ('voiceban', 'nickfreeze', 'channelmute')),
    channel_id BIGINT      NOT NULL DEFAULT 0,
    value      TEXT,
    expires_at TIMESTAMP,
    PRIMARY KEY (guild_id, user_id, kind, channel_id)
);
CREATE INDEX IF NOT EXISTS member_restrictions_user_idx ON member_restrictions (guild_id, user_id);

-- == V71__moderation_settings ==
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
