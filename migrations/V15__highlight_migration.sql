-- Revises: V14
-- Creation Date: 2024-03-27 14:51:20.913610+00:00 UTC
-- Reason: highlight_migration

CREATE TABLE IF NOT EXISTS highlights
(
    id          SERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    location_id BIGINT NOT NULL,
    blocked     BIGINT[],
    lookup      TEXT[]
);