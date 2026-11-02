-- Server tools: sentinel, temp channels, role menus, autoresponders, stat counters, tag transfers, Discord status

-- == V10__sentinel ==
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

-- == V12__temp_channel_commands ==
CREATE TABLE IF NOT EXISTS temp_channels
(
    guild_id   BIGINT             NOT NULL,
    channel_id BIGINT PRIMARY KEY NOT NULL,
    format     TEXT               NOT NULL DEFAULT '⏳ | %username'
);

-- == V19__role_menus ==
CREATE TABLE IF NOT EXISTS role_menus
(
    id           SERIAL    PRIMARY KEY,
    guild_id     BIGINT    NOT NULL,
    channel_id   BIGINT    NOT NULL,
    message_id   BIGINT,
    title        TEXT      NOT NULL,
    description  TEXT,
    unique_roles BOOLEAN   NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc')
);

CREATE INDEX IF NOT EXISTS role_menus_guild_id_idx ON role_menus (guild_id);

CREATE TABLE IF NOT EXISTS role_menu_entries
(
    menu_id  INTEGER NOT NULL REFERENCES role_menus (id) ON DELETE CASCADE,
    role_id  BIGINT  NOT NULL,
    emoji    TEXT,
    label    TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (menu_id, role_id)
);

-- == V20__community_economy_voice ==
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


-- == V63__giveaway_requirements ==
-- Giveaway requirements (required role / account age / join age) live in giveaways.metadata
-- ('kwargs'), so they need no columns. This migration only adds the pending tag-transfer
-- bookkeeping: who sent it and when the request lapses (24h, checked on access).
CREATE TABLE IF NOT EXISTS tag_transfers
(
    tag_id     INTEGER   PRIMARY KEY REFERENCES tags (id) ON DELETE CASCADE,
    from_id    BIGINT    NOT NULL,
    expires_at TIMESTAMP NOT NULL
);

-- == V8__discordstatus ==
CREATE TABLE IF NOT EXISTS discord_incidents
(
    id         TEXT,
    name       TEXT,
    status     TEXT,
    started_at TIMESTAMP,
    data       JSONB DEFAULT '{}'::jsonb,
    guild_id   BIGINT PRIMARY KEY NOT NULL,
    channel_id BIGINT             NOT NULL,
    message_id BIGINT
);

-- == V65__dstatus_incidents ==
CREATE TABLE IF NOT EXISTS discord_incident_posts
(
    guild_id    BIGINT NOT NULL REFERENCES discord_incidents (guild_id) ON DELETE CASCADE,
    incident_id TEXT   NOT NULL,
    message_id  BIGINT NOT NULL,
    status      TEXT,
    PRIMARY KEY (guild_id, incident_id)
);

-- Backfill from the old one-row-per-guild tracking.
INSERT INTO discord_incident_posts (guild_id, incident_id, message_id, status)
SELECT guild_id, id, message_id, status
FROM discord_incidents
WHERE id IS NOT NULL AND message_id IS NOT NULL
ON CONFLICT DO NOTHING;
