-- Revises: V15
-- Creation Date: 2026-06-03 00:00:00.000000+00:00 UTC
-- Reason: starboard

CREATE TABLE IF NOT EXISTS starboard_config
(
    guild_id            BIGINT  PRIMARY KEY,
    channel_id          BIGINT,
    threshold           INTEGER NOT NULL DEFAULT 3,
    emoji               TEXT    NOT NULL DEFAULT '⭐',
    self_star           BOOLEAN NOT NULL DEFAULT FALSE,
    enabled             BOOLEAN NOT NULL DEFAULT TRUE,
    ignored_channel_ids BIGINT[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS starboard_entries
(
    message_id           BIGINT    PRIMARY KEY,
    guild_id             BIGINT    NOT NULL,
    channel_id           BIGINT    NOT NULL,
    author_id            BIGINT    NOT NULL,
    starboard_message_id BIGINT,
    star_count           INTEGER   NOT NULL DEFAULT 0,
    created_at           TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc')
);

CREATE INDEX IF NOT EXISTS starboard_entries_guild_id_idx ON starboard_entries (guild_id);
CREATE INDEX IF NOT EXISTS starboard_entries_starboard_message_id_idx ON starboard_entries (starboard_message_id);
