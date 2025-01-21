-- Revises: V13
-- Creation Date: 2024-03-25 17:24:35.721873+00:00 UTC
-- Reason: user notes migration

CREATE TABLE IF NOT EXISTS user_notes
(
    id         SERIAL PRIMARY KEY,
    topic      TEXT,
    content    TEXT      NOT NULL,
    owner_id   BIGINT    NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc')
);