-- Revises: V52
-- Creation Date: 2026-10-24 00:00:00.000000+00:00 UTC
-- Reason: passive mode — members who opt out of robbing and heists, both ways
-- (add '-- migration: no-transaction' below to run outside a transaction)

-- One row per member who ever toggled; changed_at drives the toggle cooldown
-- (PASSIVE_COOLDOWN in app/services/economy/core.py), so it is kept when passive is turned off.
CREATE TABLE IF NOT EXISTS economy_passive
(
    user_id    BIGINT    NOT NULL,
    guild_id   BIGINT    NOT NULL,
    enabled    BOOLEAN   NOT NULL,
    changed_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    PRIMARY KEY (user_id, guild_id)
);
