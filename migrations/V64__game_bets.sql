-- Revises: V63
-- Creation Date: 2026-10-27 00:00:00.000000+00:00 UTC
-- Reason: open roulette / horse-race stakes are debited up front but held only in memory;
-- persisting them lets startup refund whatever a restart orphaned
-- (add '-- migration: no-transaction' below to run outside a transaction)

CREATE TABLE IF NOT EXISTS game_bets (
    id         BIGSERIAL PRIMARY KEY,
    game       TEXT   NOT NULL,
    guild_id   BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    user_id    BIGINT NOT NULL,
    amount     BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS game_bets_table_idx ON game_bets (game, channel_id);
