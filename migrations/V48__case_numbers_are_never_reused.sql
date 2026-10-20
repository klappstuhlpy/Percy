-- Revises: V47
-- Creation Date: 2026-10-20 16:26:02.766709+00:00 UTC
-- Reason: case numbers are never reused

-- The highest case number a guild has ever been given. ``MAX(case_index) + 1`` alone handed a
-- deleted newest case's number to the next action, while old modlog posts still pointed at it.
-- Existing cases keep their numbers: 0 here means "not tracked yet", and the allocator takes
-- whichever is higher, this or the live maximum, so the first case after this migration
-- continues from the current history.
ALTER TABLE modlog_config ADD COLUMN IF NOT EXISTS last_case_index INTEGER NOT NULL DEFAULT 0;
