-- Revises: V10
-- Creation Date: 2024-01-09 21:24:21.852223+00:00 UTC
-- Reason: casino_economy_migration

CREATE TABLE IF NOT EXISTS economy (
    user_id BIGINT PRIMARY KEY,
    guild_id BIGINT,
    cash BIGINT DEFAULT 0,
    bank BIGINT DEFAULT 0
)

