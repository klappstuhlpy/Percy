-- Revises: V58
-- Creation Date: 2026-10-24 00:00:00.000000+00:00 UTC
-- Reason: vote coins — remember when a member last claimed a vote reward in a guild
-- (add '-- migration: no-transaction' below to run outside a transaction)

ALTER TABLE economy_dailies ADD COLUMN IF NOT EXISTS last_vote_claim TIMESTAMP;
