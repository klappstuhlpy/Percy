-- Outbound webhooks and guild templates

-- == V36__outbound_webhooks ==
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

-- == V37__guild_templates ==
-- Shareable server-setup templates. A template is a portable snapshot of a
-- guild's *content* config (autoresponders, tags, comic feeds, temp-channel
-- formats and portable guild-config scalars) produced by the backup export.
-- Publishing one lets another server import the same setup in one click.
-- `data` is the backup blob (see app/services/backup.py `build_backup`).
CREATE TABLE IF NOT EXISTS guild_templates
(
    id              BIGSERIAL PRIMARY KEY,
    slug            TEXT      NOT NULL UNIQUE,
    name            TEXT      NOT NULL,
    description     TEXT,
    author_guild_id BIGINT,
    author_id       BIGINT,
    data            JSONB     NOT NULL,
    public          BOOLEAN   NOT NULL DEFAULT FALSE,
    downloads       INTEGER   NOT NULL DEFAULT 0,
    created_at      TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS guild_templates_public_idx
    ON guild_templates (public, created_at DESC);

CREATE INDEX IF NOT EXISTS guild_templates_author_guild_idx
    ON guild_templates (author_guild_id);
