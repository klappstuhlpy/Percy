-- Revises: V26
-- Creation Date: 2026-06-13 00:00:00.000000+00:00 UTC
-- Reason: per-user opt-out for name/nickname/avatar history tracking

-- Presence history already has a per-user switch (`track_presence`). Username,
-- nickname and avatar history were collected unconditionally with no way to turn
-- them off. Add a matching `track_history` flag so a user can disable that
-- collection too. Both default to TRUE to preserve existing behaviour (tracking
-- stays on by default); a user can opt out of either or both at any time.

ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS track_history BOOLEAN NOT NULL DEFAULT TRUE;
