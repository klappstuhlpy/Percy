-- Core schema: commands, lockdowns, polls/giveaways, timezones, comic config, emoji stats, user tracking settings

-- == V1__Initial_migrations ==
CREATE TABLE IF NOT EXISTS guild_config
(
    id                      BIGINT PRIMARY KEY,
    flags                   SMALLINT   NOT NULL DEFAULT 0,
    mention_count           SMALLINT,
    safe_automod_entity_ids BIGINT ARRAY,
    mute_role_id            BIGINT,
    muted_members           BIGINT ARRAY,
    audit_log_channel_id    BIGINT,
    audit_log_flags         JSONB               DEFAULT ('{
      "Server Updates": false,
      "Channel Logs": false,
      "Overwrite Logs": false,
      "Member Logs": false,
      "Member Management": false,
      "Bot Logs": false,
      "Message Logs": false,
      "Integration Logs": false,
      "Stage Logs": false,
      "Role Logs": false,
      "Invite Logs": false,
      "Webhook Logs": false,
      "Emoji Logs": false,
      "Sticker Logs": false,
      "Thread Logs": false,
      "Automod Logs": false
    }'::jsonb),
    audit_log_webhook_url   TEXT,
    prefixes                TEXT ARRAY NOT NULL DEFAULT '{?,>}'::TEXT[],
    use_music_panel         BOOLEAN    NOT NULL DEFAULT TRUE,
    linked_automod_rules    TEXT ARRAY
);

CREATE TABLE IF NOT EXISTS tags
(
    id          SERIAL PRIMARY KEY,
    name        TEXT      NOT NULL,
    content     TEXT      NOT NULL,
    owner_id    BIGINT,
    uses        INTEGER   NOT NULL DEFAULT (0),
    location_id BIGINT    NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'),
    use_embed   BOOLEAN   NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS tags_name_idx ON tags (name);
CREATE INDEX IF NOT EXISTS tags_location_id_idx ON tags (location_id);
CREATE INDEX IF NOT EXISTS tags_name_trgm_idx ON tags USING GIN (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS tags_name_lower_idx ON tags (LOWER(name));
CREATE UNIQUE INDEX IF NOT EXISTS tags_uniq_idx ON tags (LOWER(name), location_id);

CREATE TABLE IF NOT EXISTS tag_lookup
(
    id          SERIAL PRIMARY KEY,
    name        TEXT      NOT NULL,
    location_id BIGINT    NOT NULL,
    owner_id    BIGINT,
    created_at  TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'),
    parent_id   INTEGER REFERENCES tags (id) ON DELETE CASCADE ON UPDATE NO ACTION
);

CREATE INDEX IF NOT EXISTS tag_lookup_name_idx ON tag_lookup (name);
CREATE INDEX IF NOT EXISTS tag_lookup_location_id_idx ON tag_lookup (location_id);
CREATE INDEX IF NOT EXISTS tag_lookup_name_trgm_idx ON tag_lookup USING GIN (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS tag_lookup_name_lower_idx ON tag_lookup (LOWER(name));
CREATE UNIQUE INDEX IF NOT EXISTS tag_lookup_uniq_idx ON tag_lookup (LOWER(name), location_id);

CREATE TABLE IF NOT EXISTS timers
(
    id       SERIAL PRIMARY KEY,
    expires  TIMESTAMP NOT NULL,
    created  TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'),
    event    TEXT      NOT NULL,
    metadata JSONB     NOT NULL DEFAULT ('{}'::jsonb)
);

CREATE INDEX IF NOT EXISTS reminders_expires_idx ON timers (expires);

CREATE TABLE IF NOT EXISTS commands
(
    id          SERIAL PRIMARY KEY,
    guild_id    BIGINT,
    channel_id  BIGINT    NOT NULL,
    author_id   BIGINT    NOT NULL,
    used        TIMESTAMP NOT NULL,
    prefix      TEXT      NOT NULL,
    command     TEXT      NOT NULL,
    failed      BOOLEAN   NOT NULL,
    app_command BOOLEAN   NOT NULL DEFAULT FALSE,
    error       TEXT
);

CREATE INDEX IF NOT EXISTS commands_guild_id_idx ON commands (guild_id);
CREATE INDEX IF NOT EXISTS commands_author_id_idx ON commands (author_id);
CREATE INDEX IF NOT EXISTS commands_used_idx ON commands (used);
CREATE INDEX IF NOT EXISTS commands_command_idx ON commands (command);
CREATE INDEX IF NOT EXISTS commands_failed_idx ON commands (failed);
CREATE INDEX IF NOT EXISTS commands_app_command_idx ON commands (app_command);

CREATE TABLE IF NOT EXISTS plonks
(
    id        SERIAL PRIMARY KEY,
    guild_id  BIGINT        NOT NULL,
    entity_id BIGINT UNIQUE NOT NULL
);

CREATE INDEX IF NOT EXISTS plonks_guild_id_idx ON plonks (guild_id);
CREATE INDEX IF NOT EXISTS plonks_entity_id_idx ON plonks (entity_id);

CREATE TABLE IF NOT EXISTS command_config
(
    id         SERIAL PRIMARY KEY,
    guild_id   BIGINT  NOT NULL,
    channel_id BIGINT  NOT NULL,
    name       TEXT    NOT NULL,
    whitelist  BOOLEAN NOT NULL
);

CREATE INDEX IF NOT EXISTS command_config_guild_id_idx ON command_config (guild_id);

/*
    * Util Functions
 */

CREATE OR REPLACE FUNCTION pg_row_estimate(text) RETURNS bigint AS
$$
DECLARE
    result bigint;
BEGIN
    EXECUTE format('SELECT reltuples::bigint AS estimate FROM pg_class WHERE oid = to_regclass(%L)', $1) INTO result;
    RETURN result;
END;
$$ LANGUAGE plpgsql;

-- == V2__lockdowns ==
CREATE TABLE IF NOT EXISTS guild_lockdowns
(
    guild_id   BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    allow      BIGINT NOT NULL,
    deny       BIGINT NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

-- == V3__poll_giveaway_commands ==
DO
$$
    BEGIN
        IF NOT EXISTS (SELECT 1
                       FROM pg_type
                       WHERE typname = 'poll_entry') THEN
            CREATE TYPE poll_entry AS
            (
                user_id bigint,
                vote    smallint
            );
        END IF;
    END
$$;

CREATE TABLE IF NOT EXISTS polls
(
    id         SERIAL PRIMARY KEY,
    message_id BIGINT                   NOT NULL,
    channel_id BIGINT                   NOT NULL,
    guild_id   BIGINT                   NOT NULL,
    metadata   JSONB                    NOT NULL DEFAULT '{}'::JSONB,
    entries    poll_entry[]             NOT NULL DEFAULT ARRAY []::poll_entry[],
    published  TIMESTAMP WITH TIME ZONE NOT NULL,
    expires    TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE INDEX IF NOT EXISTS polls_message_id_idx ON polls (message_id);
CREATE INDEX IF NOT EXISTS polls_channel_id_idx ON polls (channel_id);
CREATE INDEX IF NOT EXISTS polls_guild_id_idx ON polls (guild_id);

ALTER TABLE guild_config
    ADD COLUMN IF NOT EXISTS poll_channel_id BIGINT;
ALTER TABLE guild_config
    ADD COLUMN IF NOT EXISTS poll_ping_role_id BIGINT;
ALTER TABLE guild_config
    ADD COLUMN IF NOT EXISTS poll_reason_channel_id BIGINT;

CREATE TABLE IF NOT EXISTS giveaways
(
    id         SERIAL PRIMARY KEY,
    author_id  BIGINT   NOT NULL,
    message_id BIGINT   NOT NULL,
    channel_id BIGINT   NOT NULL,
    guild_id   BIGINT   NOT NULL,
    metadata   JSONB             DEFAULT '{}'::JSONB,
    entries    BIGINT[] NOT NULL DEFAULT ARRAY []::BIGINT[]
);

CREATE INDEX IF NOT EXISTS giveaways_message_id_idx ON giveaways (message_id);
CREATE INDEX IF NOT EXISTS giveaways_channel_id_idx ON giveaways (channel_id);
CREATE INDEX IF NOT EXISTS giveaways_guild_id_idx ON giveaways (guild_id);
CREATE INDEX IF NOT EXISTS giveaways_author_id_idx ON giveaways (author_id);

-- == V4__timezones ==
CREATE TABLE IF NOT EXISTS user_settings
(
    id             BIGINT PRIMARY KEY,
    timezone       TEXT,
    track_presence BOOLEAN NOT NULL DEFAULT TRUE
);

ALTER TABLE timers
    ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'UTC';

-- == V5__comic_config ==
CREATE TABLE IF NOT EXISTS comic_config
(
    id         SERIAL PRIMARY KEY,
    guild_id   BIGINT                   NOT NULL,
    channel_id BIGINT                   NOT NULL,
    brand      TEXT                     NOT NULL,
    format     TEXT                     NOT NULL,
    day        SMALLINT,
    ping       BIGINT,
    pin        BOOLEAN DEFAULT FALSE,
    next_pull  TIMESTAMP WITH TIME ZONE NOT NULL
);

-- == V6__emoji_stats ==
CREATE TABLE IF NOT EXISTS emoji_stats
(
    id       BIGSERIAL PRIMARY KEY,
    guild_id BIGINT  NOT NULL,
    emoji_id BIGINT  NOT NULL,
    total    INTEGER NOT NULL DEFAULT (0)
);

CREATE INDEX IF NOT EXISTS emoji_stats_guild_id_idx ON emoji_stats (guild_id);
CREATE INDEX IF NOT EXISTS emoji_stats_emoji_id_idx ON emoji_stats (emoji_id);
CREATE UNIQUE INDEX IF NOT EXISTS emoji_stats_uniq_idx ON emoji_stats (guild_id, emoji_id);

-- == V27__user_data_tracking ==
-- Presence history already has a per-user switch (`track_presence`). Username,
-- nickname and avatar history were collected unconditionally with no way to turn
-- them off. Add a matching `track_history` flag so a user can disable that
-- collection too. Both default to TRUE to preserve existing behaviour (tracking
-- stays on by default); a user can opt out of either or both at any time.

ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS track_history BOOLEAN NOT NULL DEFAULT TRUE;

-- == V47__tracking_opt_in ==
-- V4/V27 defaulted `track_presence` and `track_history` to TRUE, so every member
-- Percy saw was tracked until they opted out. Tracking is now opt-in: new rows
-- start FALSE, and existing rows are reset to FALSE because a TRUE there was the
-- default, not a choice anyone made. Users who want the history features turn
-- them back on from their `settings` panel.

ALTER TABLE user_settings
    ALTER COLUMN track_presence SET DEFAULT FALSE,
    ALTER COLUMN track_history SET DEFAULT FALSE;

UPDATE user_settings SET track_presence = FALSE, track_history = FALSE;
