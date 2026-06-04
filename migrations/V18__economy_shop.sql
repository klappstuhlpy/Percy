-- Revises: V17
-- Creation Date: 2026-06-03 00:00:02.000000+00:00 UTC
-- Reason: economy shop, inventory and daily rewards

CREATE TABLE IF NOT EXISTS economy_items
(
    id          SERIAL    PRIMARY KEY,
    guild_id    BIGINT    NOT NULL,
    name        TEXT      NOT NULL,
    description TEXT,
    price       BIGINT    NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc')
);

-- Case-insensitive uniqueness of item names within a guild.
CREATE UNIQUE INDEX IF NOT EXISTS economy_items_guild_name_idx ON economy_items (guild_id, lower(name));

CREATE TABLE IF NOT EXISTS economy_inventory
(
    user_id  BIGINT  NOT NULL,
    guild_id BIGINT  NOT NULL,
    item_id  INTEGER NOT NULL REFERENCES economy_items (id) ON DELETE CASCADE,
    quantity INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, guild_id, item_id)
);

CREATE TABLE IF NOT EXISTS economy_dailies
(
    user_id    BIGINT    NOT NULL,
    guild_id   BIGINT    NOT NULL,
    last_claim TIMESTAMP NOT NULL,
    streak     INTEGER   NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, guild_id)
);
