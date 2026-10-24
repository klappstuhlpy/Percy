-- Revises: V53
-- Creation Date: 2026-10-24 00:00:00.000000+00:00 UTC
-- Reason: bank space — a per-member bank capacity that grows with work, daily and Banknotes
-- (add '-- migration: no-transaction' below to run outside a transaction)

ALTER TABLE economy ADD COLUMN IF NOT EXISTS bank_space BIGINT NOT NULL DEFAULT 10000;

-- Grandfather existing banks above the default so nobody is over capacity.
UPDATE economy SET bank_space = GREATEST(bank_space, bank);

-- Guilds can turn the cap off (deposits are then unlimited).
ALTER TABLE economy_settings ADD COLUMN IF NOT EXISTS bank_cap BOOLEAN NOT NULL DEFAULT TRUE;
