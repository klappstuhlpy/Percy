-- Revises: V3
-- Creation Date: 2023-03-28 13:20:17.941295 UTC
-- Reason: poll_giveaway_commands

CREATE TABLE IF NOT EXISTS polls (
    id SERIAL PRIMARY KEY,
    message_id BIGINT,
    channel_id BIGINT,
    guild_id   BIGINT,
    extra      jsonb DEFAULT '{}'::jsonb,
    users      BIGINT[]
);

CREATE INDEX IF NOT EXISTS polls_message_id_idx ON polls(message_id);
CREATE INDEX IF NOT EXISTS polls_channel_id_idx ON polls(channel_id);
CREATE INDEX IF NOT EXISTS polls_guild_id_idx ON polls(guild_id);

CREATE TABLE IF NOT EXISTS giveaways (
    id SERIAL PRIMARY KEY,
    author_id    BIGINT,
    message_id   BIGINT,
    channel_id   BIGINT,
    guild_id     BIGINT,
    prize        TEXT,
    description  TEXT,
    winner_count SMALLINT,
    entries      BIGINT[]
);

CREATE INDEX IF NOT EXISTS giveaways_message_id_idx ON giveaways(message_id);
CREATE INDEX IF NOT EXISTS giveaways_channel_id_idx ON giveaways(channel_id);
CREATE INDEX IF NOT EXISTS giveaways_guild_id_idx ON giveaways(guild_id);
CREATE INDEX IF NOT EXISTS giveaways_author_id_idx ON giveaways(author_id);