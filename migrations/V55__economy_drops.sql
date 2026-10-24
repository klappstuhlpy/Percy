-- Revises: V54
-- Creation Date: 2026-10-24 00:00:00.000000+00:00 UTC
-- Reason: random chat drops — an opt-in per guild
-- (add '-- migration: no-transaction' below to run outside a transaction)

ALTER TABLE economy_settings ADD COLUMN IF NOT EXISTS drops_enabled BOOLEAN NOT NULL DEFAULT FALSE;
