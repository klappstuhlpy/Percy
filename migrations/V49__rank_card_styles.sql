-- Revises: V48
-- Creation Date: 2026-10-21 15:08:59.157564+00:00 UTC
-- Reason: rank card styles
-- (add '-- migration: no-transaction' below to run outside a transaction)


-- A member's rank card style, global across servers: a *partial* JSON object of preset keys
-- (theme/accent/bar/font, validated in app/rendering/card_style.py). Absent keys follow the
-- server default, then the built-in default.
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS rank_card JSONB NOT NULL DEFAULT '{}'::JSONB;

-- A server's default card style (same partial shape) and whether members may override it.
-- A locked server renders every card in its default and hides the member editor.
ALTER TABLE level_config ADD COLUMN IF NOT EXISTS card_style JSONB NOT NULL DEFAULT '{}'::JSONB;
ALTER TABLE level_config ADD COLUMN IF NOT EXISTS card_locked BOOLEAN NOT NULL DEFAULT FALSE;
