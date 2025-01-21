-- Revises: V8
-- Creation Date: 2024-01-09 21:24:21.852223+00:00 UTC
-- Reason: casino_economy_migration

CREATE TABLE IF NOT EXISTS economy
(
    id       SERIAL PRIMARY KEY,
    user_id  BIGINT NOT NULL,
    guild_id BIGINT NOT NULL,
    cash     BIGINT NOT NULL DEFAULT 0,
    bank     BIGINT NOT NULL DEFAULT 0
)

