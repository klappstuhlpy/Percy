-- Revises: V44
-- Creation Date: 2026-08-17 00:00:00.000000+00:00 UTC
-- Reason: seasons

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
