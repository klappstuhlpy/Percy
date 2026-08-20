-- Revises: V40
-- Creation Date: 2026-08-04 12:00:00.000000+00:00 UTC
-- Reason: people

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
