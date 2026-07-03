-- Revises: V9
-- Creation Date: 2024-02-08 18:19:37.448137+00:00 UTC
-- Reason: sentinel

ALTER TABLE guild_config
    ADD COLUMN IF NOT EXISTS alert_webhook_url TEXT;
ALTER TABLE guild_config
    ADD COLUMN IF NOT EXISTS alert_channel_id BIGINT;

CREATE TABLE IF NOT EXISTS guild_sentinel
(
    id              BIGINT PRIMARY KEY,
    started_at      TIMESTAMP WITH TIME ZONE CHECK (
        started_at IS NULL OR
        (channel_id IS NOT NULL AND role_id IS NOT NULL AND message_id IS NOT NULL)),
    channel_id      BIGINT,
    role_id         BIGINT,
    message_id      BIGINT,
    bypass_action   TEXT NOT NULL DEFAULT 'ban',
    rate            TEXT,
    starter_role_id BIGINT
);


DO
$$
    BEGIN
        IF NOT EXISTS (SELECT 1
                       FROM pg_type
                       WHERE typname = 'sentinel_role_state') THEN
            CREATE TYPE sentinel_role_state AS ENUM ('added', 'pending_add', 'pending_remove');
        END IF;
    EXCEPTION
        WHEN duplicate_object THEN NULL;
    END
$$;

CREATE TABLE IF NOT EXISTS guild_sentinel_members
(
    guild_id BIGINT              NOT NULL,
    user_id  BIGINT              NOT NULL,
    state    sentinel_role_state NOT NULL DEFAULT 'pending_add',
    PRIMARY KEY (guild_id, user_id)
);