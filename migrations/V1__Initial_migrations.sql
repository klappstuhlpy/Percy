-- Revises: V0
-- Creation Date: 2023-03-28 13:14:25.528617 UTC
-- Reason: Initial migrations

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
