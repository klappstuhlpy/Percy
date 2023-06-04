-- Revises: V7
-- Creation Date: 2023-04-02 08:54:00.148543 UTC
-- Reason: starboard_addition

CREATE TABLE IF NOT EXISTS starboard (
    id BIGINT PRIMARY KEY,
    channel_id BIGINT,
    threshold INTEGER DEFAULT (1) NOT NULL,
    locked BOOLEAN DEFAULT FALSE,
    max_age INTERVAL DEFAULT ('7 days'::interval) NOT NULL
);

CREATE TABLE IF NOT EXISTS starboard_entries (
    id SERIAL PRIMARY KEY,
    bot_message_id BIGINT,
    message_id BIGINT UNIQUE NOT NULL,
    channel_id BIGINT,
    author_id BIGINT,
    guild_id BIGINT REFERENCES starboard (id) ON DELETE CASCADE ON UPDATE NO ACTION NOT NULL
);

CREATE INDEX IF NOT EXISTS starboard_entries_bot_message_id_idx ON starboard_entries (bot_message_id);
CREATE INDEX IF NOT EXISTS starboard_entries_message_id_idx ON starboard_entries (message_id);
CREATE INDEX IF NOT EXISTS starboard_entries_guild_id_idx ON starboard_entries (guild_id);

CREATE TABLE IF NOT EXISTS starrers (
    id SERIAL PRIMARY KEY,
    author_id BIGINT NOT NULL,
    entry_id INTEGER REFERENCES starboard_entries (id) ON DELETE CASCADE ON UPDATE NO ACTION NOT NULL
);

CREATE INDEX IF NOT EXISTS starrers_entry_id_idx ON starrers (entry_id);
CREATE UNIQUE INDEX IF NOT EXISTS starrers_uniq_idx ON starrers (author_id, entry_id);