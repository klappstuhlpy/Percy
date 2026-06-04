-- Revises: V18
-- Creation Date: 2026-06-03 00:00:03.000000+00:00 UTC
-- Reason: self-assignable role menus

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
