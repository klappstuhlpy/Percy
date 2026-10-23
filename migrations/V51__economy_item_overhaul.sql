-- Revises: V50
-- Creation Date: 2026-10-23 00:00:00.000000+00:00 UTC
-- Reason: economy item overhaul (presentation, rarity, stock, purchase gates)
-- (add '-- migration: no-transaction' below to run outside a transaction)

-- Shown before the item name everywhere (shop, inventory, autocomplete). NULL falls back to the category emoji.
ALTER TABLE economy_items ADD COLUMN IF NOT EXISTS emoji TEXT;
-- common | uncommon | rare | epic | legendary — sorting and colour only, no gameplay effect.
ALTER TABLE economy_items ADD COLUMN IF NOT EXISTS rarity TEXT NOT NULL DEFAULT 'common';
-- Units left for sale; NULL is unlimited. Decremented inside the purchase transaction.
ALTER TABLE economy_items ADD COLUMN IF NOT EXISTS stock INTEGER;
-- Only members holding this role may buy the item; NULL is everyone.
ALTER TABLE economy_items ADD COLUMN IF NOT EXISTS required_role_id BIGINT;
-- Most a single member may hold at once; NULL is unlimited.
ALTER TABLE economy_items ADD COLUMN IF NOT EXISTS max_per_user INTEGER;
-- Naive UTC; after this moment the item can no longer be bought. NULL is on sale indefinitely.
ALTER TABLE economy_items ADD COLUMN IF NOT EXISTS available_until TIMESTAMP;
-- Role taken away from the member when the item is used (alongside any effect); NULL removes nothing.
ALTER TABLE economy_items ADD COLUMN IF NOT EXISTS remove_role_id BIGINT;
-- Extra line appended to the `use` result (max 500 characters); NULL adds nothing.
ALTER TABLE economy_items ADD COLUMN IF NOT EXISTS reply_message TEXT;
