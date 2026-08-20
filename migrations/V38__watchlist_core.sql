-- Revises: V37
-- Creation Date: 2026-08-02 00:00:00.000000+00:00 UTC
-- Reason: watchlist_core

-- Universe watchlist: TMDB-backed watch-order tracker for a set of franchises
-- ("universes", e.g. MCU). A universe holds titles synced from TMDB, optional
-- curated viewing paths through those titles, per-path importance tags, and
-- per-region streaming provider availability.
CREATE TABLE IF NOT EXISTS watch_universes
(
    slug         TEXT    PRIMARY KEY,
    name         TEXT    NOT NULL,
    short_name   TEXT    NOT NULL,
    tagline      TEXT,
    accent       INTEGER NOT NULL DEFAULT 15082537,
    logo_url     TEXT,
    backdrop_url TEXT,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    published    BOOLEAN NOT NULL DEFAULT FALSE,
    synced_at    TIMESTAMP
);

CREATE TABLE IF NOT EXISTS watch_titles
(
    id            BIGSERIAL PRIMARY KEY,
    universe      TEXT      NOT NULL REFERENCES watch_universes (slug) ON DELETE CASCADE,
    tmdb_type     TEXT      NOT NULL CHECK (tmdb_type IN ('movie', 'tv')),
    tmdb_id       INTEGER   NOT NULL,
    slug          TEXT      NOT NULL,
    title         TEXT      NOT NULL,
    kind          TEXT      NOT NULL DEFAULT 'movie' CHECK (kind IN ('movie', 'tv', 'special')),
    runtime       INTEGER   NOT NULL DEFAULT 0,
    episodes      INTEGER,
    release_date  DATE,
    poster_path   TEXT,
    backdrop_path TEXT,
    overview      TEXT,
    tmdb_rating   NUMERIC(3, 1),
    tmdb_votes    INTEGER,
    era           TEXT,
    era_order     INTEGER   NOT NULL DEFAULT 0,
    story_order   INTEGER   NOT NULL DEFAULT 0,
    release_order INTEGER   NOT NULL DEFAULT 0,
    milestone     BOOLEAN   NOT NULL DEFAULT FALSE,
    instruction   TEXT,
    context       TEXT,
    spoiler       TEXT,
    credits_of    BIGINT    REFERENCES watch_titles (id) ON DELETE SET NULL,
    sub_universe  TEXT,
    UNIQUE (universe, tmdb_type, tmdb_id),
    UNIQUE (universe, slug)
);
CREATE INDEX IF NOT EXISTS watch_titles_story_idx   ON watch_titles (universe, story_order);
CREATE INDEX IF NOT EXISTS watch_titles_release_idx ON watch_titles (universe, release_order);
CREATE INDEX IF NOT EXISTS watch_titles_trgm_idx    ON watch_titles USING GIN (title gin_trgm_ops);

CREATE TABLE IF NOT EXISTS watch_paths
(
    id          BIGSERIAL PRIMARY KEY,
    universe    TEXT      NOT NULL REFERENCES watch_universes (slug) ON DELETE CASCADE,
    slug        TEXT      NOT NULL,
    name        TEXT      NOT NULL,
    description TEXT,
    is_default  BOOLEAN   NOT NULL DEFAULT FALSE,
    sort_order  INTEGER   NOT NULL DEFAULT 0,
    UNIQUE (universe, slug)
);

CREATE TABLE IF NOT EXISTS watch_title_importance
(
    title_id   BIGINT NOT NULL REFERENCES watch_titles (id) ON DELETE CASCADE,
    path_id    BIGINT NOT NULL REFERENCES watch_paths (id) ON DELETE CASCADE,
    importance TEXT   NOT NULL CHECK (importance IN ('essential', 'recommended', 'optional')),
    PRIMARY KEY (title_id, path_id)
);

CREATE TABLE IF NOT EXISTS watch_providers
(
    title_id      BIGINT  NOT NULL REFERENCES watch_titles (id) ON DELETE CASCADE,
    region        CHAR(2) NOT NULL,
    provider_id   INTEGER NOT NULL,
    provider_name TEXT    NOT NULL,
    logo_path     TEXT,
    offer         TEXT    NOT NULL DEFAULT 'flatrate' CHECK (offer IN ('flatrate', 'rent', 'buy')),
    PRIMARY KEY (title_id, region, provider_id, offer)
);
