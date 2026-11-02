-- Watchlist catalogue: universes, titles, progress, people, trailers, seasons, provider links

-- == V38__watchlist_core ==
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

-- == V39__watchlist_progress ==
-- Per-user watchlist state: which titles a user has watched or skipped
-- (watch_progress) and their viewing preferences (watch_prefs) -- region,
-- preferred path, sort mode, spoiler/credits visibility, and a weekly-hours
-- budget used to estimate completion time.
CREATE TABLE IF NOT EXISTS watch_progress
(
    user_id    BIGINT    NOT NULL,
    title_id   BIGINT    NOT NULL REFERENCES watch_titles (id) ON DELETE CASCADE,
    status     TEXT      NOT NULL CHECK (status IN ('watched', 'skipped')),
    updated_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    PRIMARY KEY (user_id, title_id)
);

CREATE TABLE IF NOT EXISTS watch_prefs
(
    user_id      BIGINT    PRIMARY KEY,
    region       CHAR(2)   NOT NULL DEFAULT 'US',
    path_slug    TEXT,
    sort_mode    TEXT      NOT NULL DEFAULT 'story' CHECK (sort_mode IN ('story', 'release')),
    show_spoiler BOOLEAN   NOT NULL DEFAULT FALSE,
    with_credits BOOLEAN   NOT NULL DEFAULT TRUE,
    hide_sub     BOOLEAN   NOT NULL DEFAULT FALSE,
    weekly_hours JSONB,
    target_date  DATE,
    updated_at   TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

-- == V41__people ==
-- Cast and crew for the watchlist catalogue, mirroring V40's ``comic_creators``: a person is
-- a subject a user can subscribe to, so "follow Pedro Pascal" and "follow Jonathan Hickman"
-- end up the same mechanism with the role stored on the credit rather than in the key.
--
-- Sourced from TMDB ``{movie,tv}/{id}/credits`` during ``main.py watchlist sync``, for every
-- seeded title *including unreleased ones* -- an actor subscription that only knows about
-- films already out is worthless.
CREATE TABLE IF NOT EXISTS watch_people
(
    tmdb_person_id INTEGER   PRIMARY KEY,
    name           TEXT      NOT NULL,
    slug           TEXT      NOT NULL,
    profile_path   TEXT,
    updated_at     TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    UNIQUE (slug)
);

CREATE INDEX IF NOT EXISTS watch_people_name_trgm_idx ON watch_people USING GIN (name gin_trgm_ops);

-- ``billing_order`` is TMDB's cast ``order`` (crew rows have none), which lets a detail page
-- show the top billing without a second sort. ``role`` is normalised to four values rather
-- than TMDB's free-text ``job``, so the subscription layer has a closed set to match on.
CREATE TABLE IF NOT EXISTS watch_credits
(
    title_id       BIGINT  NOT NULL REFERENCES watch_titles (id) ON DELETE CASCADE,
    person_id      INTEGER NOT NULL REFERENCES watch_people (tmdb_person_id) ON DELETE CASCADE,
    role           TEXT    NOT NULL CHECK (role IN ('cast', 'director', 'writer', 'creator')),
    character_name TEXT,
    billing_order  INTEGER,
    PRIMARY KEY (title_id, person_id, role)
);

-- "Everything this person is in" -- the person page and the subscription fan-out both read
-- by person, never by title alone.
CREATE INDEX IF NOT EXISTS watch_credits_person_idx ON watch_credits (person_id);

-- == V44__trailers ==
-- Three columns on ``watch_titles`` rather than a ``watch_videos`` table, because the product
-- is one click-to-load trailer per title (the plan's §6.2 item 7), not a video library. A
-- table would buy an ordered many-to-many we would never read more than one row of, and would
-- cost the detail page a second query and the ingest a delete-then-insert pass; a column
-- degrades to "no trailer" by staying NULL, which is the common case for an unreleased title
-- and must never read as an error.
--
-- What is kept is exactly what renders: ``trailer_site`` + ``trailer_key`` build both the
-- watch URL and the embed URL (``https://www.youtube.com/{watch?v=,embed/}<key>``), and
-- ``trailer_name`` is TMDB's video title ("Official Trailer", "Teaser Trailer #2") -- the only
-- honest label for the button and its accessible name. TMDB's ``type``, ``official``,
-- ``published_at`` and ``iso_639_1`` are *selection* inputs, not render inputs: they decide
-- which of a title's dozen videos wins in
-- :func:`app.services.watchlist.trailers.select_trailer`, and storing the losers' tie-breakers
-- alongside the winner would only invite a second, divergent selection at read time.
--
-- Filled by ``main.py watchlist sync`` from ``{movie,tv}/{id}/videos``. Written through
-- ``set_trailer`` rather than ``upsert_title`` for the same reason ``release_date`` has its own
-- setter (V38): a TMDB outage must leave the stored trailer alone, and a column folded into the
-- upsert would be overwritten with NULL on every failed fetch.
ALTER TABLE watch_titles
    ADD COLUMN IF NOT EXISTS trailer_site TEXT,
    ADD COLUMN IF NOT EXISTS trailer_key  TEXT,
    ADD COLUMN IF NOT EXISTS trailer_name TEXT;

-- == V45__seasons ==
-- One watch entry per *season*, decided 2026-08-16. A watch order exists to be followed, and
-- "watch Daredevil season 2 here" is an instruction where "watch Daredevil" is not; it is also
-- the only counting that reaches the ~165 figure honestly (Agents of S.H.I.E.L.D. is 7 entries,
-- the Netflix run ~13), because counting real releases once each tops out near 89.
--
-- Three columns on ``watch_titles`` rather than a ``watch_seasons`` table, for the same reason
-- V44 put the trailer here: every consumer already keys on ``watch_titles.id`` -- ordering
-- (``story_order``/``release_order``), progress (``watch_progress.title_id``), credits,
-- providers, importance, the release dispatcher's ``release_key``. A second table would fork
-- all of them and buy nothing a nullable column does not already give.
--
-- The shape:
--
--   * ``season_number`` is NULL for a film and for a series row. A film therefore behaves
--     exactly as it did before this migration -- no column it reads changed value.
--   * ``season_name`` is TMDB's own season name ("Season 2", "Specials", or a real name for an
--     anthology), kept verbatim rather than re-derived from the number, because the two differ
--     for exactly the shows where the name matters.
--   * ``parent_id`` points a season at its series. ON DELETE CASCADE: a series that leaves the
--     catalogue takes its seasons with it, which is what ``prune_titles`` already means.
--
-- The invariant is a biconditional, not two independent nullable columns: a season always has
-- a parent and only a season has one. Both sides are ``IS NULL`` tests, so no NULL leaks into
-- the CHECK's own result.
--
-- A series row that has season children is a **container**, not an entry: it keeps its identity,
-- its aggregate runtime/episodes, its series-level cast and its trailer, and the seasons are
-- what a reader watches and marks. That is why the read side (``list_universes``,
-- ``universe_stats``, ``titles_releasing_between``) excludes a row that any other row names as
-- its parent -- counting both would double every exploded show's runtime and announce a series
-- and its first season as two separate releases. A single-season show is never exploded, so it
-- stays one row and reads as "Hawkeye" rather than "Hawkeye: Season 1".
ALTER TABLE watch_titles
    ADD COLUMN IF NOT EXISTS season_number INTEGER,
    ADD COLUMN IF NOT EXISTS season_name   TEXT,
    ADD COLUMN IF NOT EXISTS parent_id     BIGINT REFERENCES watch_titles (id) ON DELETE CASCADE;

ALTER TABLE watch_titles DROP CONSTRAINT IF EXISTS watch_titles_season_parent_check;
ALTER TABLE watch_titles
    ADD CONSTRAINT watch_titles_season_parent_check
        CHECK ((season_number IS NULL) = (parent_id IS NULL));

-- The title identity was ``UNIQUE (universe, tmdb_type, tmdb_id)``, which per-season rows break:
-- every season of a show carries the show's ``tmdb_id``. Widening the constraint to include
-- ``season_number`` directly would not work either -- NULLs are distinct in a UNIQUE constraint,
-- so ``upsert_title``'s ON CONFLICT would stop matching films and every sync would insert a
-- fresh duplicate of every one of them. ``COALESCE(season_number, -1)`` restores the exact old
-- behaviour for films (-1 is not a TMDB season number) while giving each season its own slot,
-- and Postgres infers ON CONFLICT from an expression index as long as the expression is written
-- identically -- see ``WatchlistRepository.upsert_title``.
ALTER TABLE watch_titles DROP CONSTRAINT IF EXISTS watch_titles_universe_tmdb_type_tmdb_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS watch_titles_identity_idx
    ON watch_titles (universe, tmdb_type, tmdb_id, COALESCE(season_number, -1));

-- "Is this row a container?" is asked once per title by the count/runtime aggregates and by the
-- release dispatcher's window, always as a lookup by ``parent_id``.
CREATE INDEX IF NOT EXISTS watch_titles_parent_idx ON watch_titles (parent_id);

-- == V46__provider_links ==
-- TMDB's ``watch/providers`` response has always handed us a ``link`` per region alongside the
-- provider buckets, and ``provider_rows`` has always thrown it away. TMDB's terms ask for that
-- link wherever the provider logos are shown, and the watch-order page shows them -- so this is
-- a compliance gap closed with data we already fetch. No new upstream call.
--
-- The grain: ``link`` is a property of ``(title, region)``, not of a provider, so the normalised
-- home for it is a ``watch_provider_links (title_id, region, link)`` row. It goes on the provider
-- row anyway, and deliberately:
--
--   * The read path is already exactly one query per page (``list_providers``, batched over every
--     title id). A second table means a second query and a second grouping pass in
--     ``universe_payload`` for one short string -- or a join whose only job is to re-attach a
--     value that is constant within the group we just built.
--   * The write path is already atomic per ``(title_id, region)``: ``replace_providers`` deletes
--     and re-inserts that exact tuple in one transaction. A per-region column therefore has the
--     same lifetime as the rows beside it, with no second delete to keep in step and no way for
--     the link to outlive the offers it points at.
--   * The consumer wants it per row. The page renders one anchor per provider logo, so the
--     denormalised shape is the shape the renderer reads -- ``providers[].link`` needs no lookup.
--
-- The cost is a ~77-character URL repeated across the (typically fewer than ten) provider rows of
-- one title in one region. That is the trade taken: duplication measured in kilobytes across the
-- whole catalogue, against a query and a payload fork.
--
-- Nullable, with no backfill. A region TMDB tracks always carries a link, but a row written
-- before this migration has none and there is nothing to derive it from -- the URL embeds TMDB's
-- own title slug, so guessing it would mean inventing an attribution link rather than echoing the
-- one we were given. NULL is the honest "not re-synced yet", and the next ``watchlist sync``
-- replaces those rows wholesale.
ALTER TABLE watch_providers
    ADD COLUMN IF NOT EXISTS link TEXT;
