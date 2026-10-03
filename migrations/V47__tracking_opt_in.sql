-- Revises: V46
-- Creation Date: 2026-10-03 00:00:00.000000+00:00 UTC
-- Reason: presence and name/avatar history tracking become opt-in

-- V4/V27 defaulted `track_presence` and `track_history` to TRUE, so every member
-- Percy saw was tracked until they opted out. Tracking is now opt-in: new rows
-- start FALSE, and existing rows are reset to FALSE because a TRUE there was the
-- default, not a choice anyone made. Users who want the history features turn
-- them back on from their `settings` panel.

ALTER TABLE user_settings
    ALTER COLUMN track_presence SET DEFAULT FALSE,
    ALTER COLUMN track_history SET DEFAULT FALSE;

UPDATE user_settings SET track_presence = FALSE, track_history = FALSE;
