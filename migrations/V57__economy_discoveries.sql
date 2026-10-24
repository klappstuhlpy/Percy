-- Revises: V56
-- Creation Date: 2026-10-24 00:00:00.000000+00:00 UTC
-- Reason: collection book — which catalogue drops a member has ever found
-- (add '-- migration: no-transaction' below to run outside a transaction)

-- Insert-if-absent on the primary key; first_found is when the member first got that drop.
CREATE TABLE IF NOT EXISTS economy_discoveries
(
    user_id     BIGINT    NOT NULL,
    guild_id    BIGINT    NOT NULL,
    builtin_key TEXT      NOT NULL,
    first_found TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    PRIMARY KEY (user_id, guild_id, builtin_key)
);
