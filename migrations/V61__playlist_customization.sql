-- Revises: V60
-- Creation Date: 2026-10-26 00:00:00.000000+00:00 UTC
-- Reason: playlist customization — a custom cover per playlist, and each saved track keeps its
-- artist and cover so the dashboard can show them instead of "Liked song"
-- (add '-- migration: no-transaction' below to run outside a transaction)

ALTER TABLE playlist ADD COLUMN IF NOT EXISTS thumbnail TEXT;

ALTER TABLE playlist_lookup ADD COLUMN IF NOT EXISTS author TEXT;
ALTER TABLE playlist_lookup ADD COLUMN IF NOT EXISTS artwork TEXT;
