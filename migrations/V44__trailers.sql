-- Revises: V43
-- Creation Date: 2026-08-05 08:52:24.476798+00:00 UTC
-- Reason: trailers

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
