-- Revises: V36
-- Creation Date: 2026-07-04 00:00:01.000000+00:00 UTC
-- Reason: guild_templates

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
