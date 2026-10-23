-- Revises: V51
-- Creation Date: 2026-10-23 00:00:00.000000+00:00 UTC
-- Reason: built-in catalogue items (activity drops and tools) materialized per guild
-- (add '-- migration: no-transaction' below to run outside a transaction)

-- Set for rows materialized from app/services/economy/catalogue.py; NULL for admin-made items.
ALTER TABLE economy_items ADD COLUMN IF NOT EXISTS builtin_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS economy_items_guild_builtin_idx
    ON economy_items (guild_id, builtin_key) WHERE builtin_key IS NOT NULL;
-- FALSE hides the item from the shop (activity drops) while it still shows in inventories.
ALTER TABLE economy_items ADD COLUMN IF NOT EXISTS for_sale BOOLEAN NOT NULL DEFAULT TRUE;
-- Uses left on the member's current tool unit; NULL means untracked (not a tool, or a fresh unit).
ALTER TABLE economy_inventory ADD COLUMN IF NOT EXISTS durability INTEGER;
