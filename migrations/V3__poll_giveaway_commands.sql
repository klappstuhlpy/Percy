-- Revises: V2
-- Creation Date: 2023-03-28 13:20:17.941295 UTC
-- Reason: poll_giveaway_commands

CREATE TYPE poll_entry AS
(
    user_id bigint,
    vote    smallint
);

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