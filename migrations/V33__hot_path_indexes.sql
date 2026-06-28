-- Revises: V32
-- Creation Date: 2026-06-27 00:00:00.000000+00:00 UTC
-- Reason: hot_path_indexes

-- Indexes for queries that the slow-query tracker flagged as full table scans.

-- highlights: the on_message listener runs `WHERE location_id = $1` for every
-- message in a guild (highlight.get_guild_highlights), and the user-data export /
-- import-locations paths filter on user_id. The table previously had only a PK on
-- id, so both were sequential scans on every hit.
CREATE INDEX IF NOT EXISTS highlights_location_id_idx ON highlights (location_id);
CREATE INDEX IF NOT EXISTS highlights_user_id_idx ON highlights (user_id);

-- polls: the reconciliation loop scans `WHERE expires < now()` to recover polls
-- whose end timer was missed. No index existed on expires, so it scanned the whole
-- table each tick.
CREATE INDEX IF NOT EXISTS polls_expires_idx ON polls (expires);
