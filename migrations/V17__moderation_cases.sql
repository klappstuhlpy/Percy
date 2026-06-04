-- Revises: V16
-- Creation Date: 2026-06-03 00:00:01.000000+00:00 UTC
-- Reason: moderation cases

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
