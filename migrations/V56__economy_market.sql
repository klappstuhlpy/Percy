-- Revises: V55
-- Creation Date: 2026-10-24 00:00:00.000000+00:00 UTC
-- Reason: player market — members list items for sale, items are escrowed until sold or cancelled
-- (add '-- migration: no-transaction' below to run outside a transaction)

CREATE TABLE IF NOT EXISTS economy_market
(
    id         BIGSERIAL PRIMARY KEY,
    guild_id   BIGINT    NOT NULL,
    seller_id  BIGINT    NOT NULL,
    item_id    BIGINT    NOT NULL REFERENCES economy_items (id) ON DELETE CASCADE,
    quantity   INTEGER   NOT NULL CHECK (quantity > 0),
    price_each BIGINT    NOT NULL CHECK (price_each > 0),
    created_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS economy_market_guild_created_idx ON economy_market (guild_id, created_at DESC);
