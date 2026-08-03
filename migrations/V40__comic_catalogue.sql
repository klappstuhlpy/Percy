-- Revises: V39
-- Creation Date: 2026-08-03 12:00:00.000000+00:00 UTC
-- Reason: comic_catalogue

-- The comic archive. Until now the comic cog held every issue in a memory-only cache
-- (refreshed every 6 h) and persisted nothing but the per-guild feed *schedule*
-- (V5__comic_config), so a restart lost the catalogue and the website had nothing to read.
-- These tables are the archive: the cache stays (the Discord embeds render from it), the
-- database becomes what survives.
--
-- ``first_seen`` is what makes "new this week" honest across a restart -- it is set once, on
-- the first upsert of a release, and never touched again.
CREATE TABLE IF NOT EXISTS comic_releases
(
    id           SERIAL PRIMARY KEY,
    brand        TEXT           NOT NULL,
    external_id  TEXT           NOT NULL,
    slug         TEXT           NOT NULL,
    title        TEXT           NOT NULL,
    series_slug  TEXT,
    series_name  TEXT,
    issue_number TEXT,
    release_date DATE,
    cover_url    TEXT,
    description  TEXT,
    price        NUMERIC(10, 2),
    page_count   INTEGER,
    format       TEXT,
    publisher    TEXT,
    url          TEXT,
    first_seen   TIMESTAMP      NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    updated_at   TIMESTAMP      NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    UNIQUE (brand, external_id),
    UNIQUE (brand, slug)
);

CREATE INDEX IF NOT EXISTS comic_releases_brand_date_idx ON comic_releases (brand, release_date DESC);
CREATE INDEX IF NOT EXISTS comic_releases_series_idx ON comic_releases (series_slug);
CREATE INDEX IF NOT EXISTS comic_releases_first_seen_idx ON comic_releases (first_seen DESC);
CREATE INDEX IF NOT EXISTS comic_releases_title_trgm_idx ON comic_releases USING GIN (title gin_trgm_ops);

-- Credits, in the ``{role: [names]}`` shape locg-api gives us, flattened one row per pair.
-- A creator is a subject a user can subscribe to (see the releases plan), hence the
-- lower(name) index: the fan-out lookup is by name, case-insensitively.
CREATE TABLE IF NOT EXISTS comic_creators
(
    release_id INTEGER NOT NULL REFERENCES comic_releases (id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    role       TEXT    NOT NULL,
    PRIMARY KEY (release_id, name, role)
);

CREATE INDEX IF NOT EXISTS comic_creators_name_idx ON comic_creators (lower(name));

-- ``kind`` is locg-api's character ``type`` (Main, Supporting, Villain, ...) and is display
-- data only -- subscriptions match on the name.
CREATE TABLE IF NOT EXISTS comic_characters
(
    release_id INTEGER NOT NULL REFERENCES comic_releases (id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    kind       TEXT,
    PRIMARY KEY (release_id, name)
);

CREATE INDEX IF NOT EXISTS comic_characters_name_idx ON comic_characters (lower(name));
