-- Revises: V49
-- Creation Date: 2026-10-21 18:34:56.294540+00:00 UTC
-- Reason: notifications
-- (add '-- migration: no-transaction' below to run outside a transaction)



CREATE TABLE IF NOT EXISTS guild_notifications (
    id         BIGSERIAL PRIMARY KEY,
    guild_id   BIGINT,                       -- NULL = global notice from the bot owner
    kind       TEXT NOT NULL,
    severity   TEXT NOT NULL DEFAULT 'info',
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    link       TEXT,
    meta       JSONB NOT NULL DEFAULT '{}'::jsonb,
    dedupe_key TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);
CREATE INDEX IF NOT EXISTS guild_notifications_guild_idx ON guild_notifications (guild_id, id DESC);

CREATE TABLE IF NOT EXISTS notification_reads (
    guild_id     BIGINT NOT NULL,
    user_id      BIGINT NOT NULL,
    last_read_id BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS action_requests (
    id            BIGSERIAL PRIMARY KEY,
    guild_id      BIGINT NOT NULL,
    requester_id  BIGINT NOT NULL,
    target_id     BIGINT NOT NULL,
    action        TEXT NOT NULL,
    reason        TEXT NOT NULL,
    duration      INTEGER,                   -- seconds, timeout/mute only
    status        TEXT NOT NULL DEFAULT 'pending',
    decided_by    BIGINT,
    decision_note TEXT,
    result        TEXT,
    channel_id    BIGINT,                    -- the Discord approval message, if one was posted
    message_id    BIGINT,
    created_at    TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    decided_at    TIMESTAMP,
    expires_at    TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS action_requests_pending_idx ON action_requests (guild_id, status, id DESC);

ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS request_channel_id BIGINT;
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS request_role_ids BIGINT[] NOT NULL DEFAULT '{}';
