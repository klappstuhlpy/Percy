-- Revises: V35
-- Creation Date: 2026-07-04 00:00:00.000000+00:00 UTC
-- Reason: outbound_webhooks

-- Per-guild outbound webhook subscriptions. Admins register a URL and a set of
-- events; the dispatcher cog (app/cogs/webhooks.py) POSTs a signed JSON envelope
-- to that URL whenever a matching event fires. `secret` backs the HMAC-SHA256
-- signature sent in the `X-Percy-Signature` header so receivers can verify us.
CREATE TABLE IF NOT EXISTS webhook_subscriptions
(
    id               BIGSERIAL PRIMARY KEY,
    guild_id         BIGINT      NOT NULL,
    url              TEXT        NOT NULL,
    secret           TEXT        NOT NULL,
    events           TEXT[]      NOT NULL DEFAULT '{}',
    label            TEXT,
    enabled          BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMP   NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    last_delivery_at TIMESTAMP,
    last_status      INTEGER,
    failure_count    INTEGER     NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS webhook_subscriptions_guild_idx
    ON webhook_subscriptions (guild_id);

-- A bounded audit log of recent delivery attempts, used by the dashboard to show
-- whether a subscription is healthy. Rows cascade with their subscription and are
-- pruned to the most recent N per subscription by the repository on insert.
CREATE TABLE IF NOT EXISTS webhook_deliveries
(
    id              BIGSERIAL PRIMARY KEY,
    subscription_id BIGINT    NOT NULL REFERENCES webhook_subscriptions (id) ON DELETE CASCADE,
    event           TEXT      NOT NULL,
    success         BOOLEAN   NOT NULL DEFAULT FALSE,
    status_code     INTEGER,
    attempts        INTEGER   NOT NULL DEFAULT 0,
    error           TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS webhook_deliveries_sub_idx
    ON webhook_deliveries (subscription_id, created_at DESC);
