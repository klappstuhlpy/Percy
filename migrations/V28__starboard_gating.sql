-- Revises: V27
-- Creation Date: 2026-06-16 21:38:43.422349+00:00 UTC
-- Reason: starboard gating
-- (add '-- migration: no-transaction' below to run outside a transaction)

-- Gating controls for the starboard:
--   max_age_hours: ignore messages older than this many hours (0 = no limit)
--   allow_nsfw:    whether messages from NSFW channels may reach a non-NSFW starboard
ALTER TABLE starboard_config
    ADD COLUMN IF NOT EXISTS max_age_hours INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS allow_nsfw    BOOLEAN NOT NULL DEFAULT FALSE;

