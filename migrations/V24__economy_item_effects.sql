-- Revises: V23
-- Creation Date: 2026-06-11 00:00:00.000000+00:00 UTC
-- Reason: economy item effects and timed boosts

-- What happens when a shop item is used: none | cash | lootbox | role | xp_boost | loot_boost.
ALTER TABLE economy_items ADD COLUMN IF NOT EXISTS effect TEXT NOT NULL DEFAULT 'none';
-- Effect payload: cash amount, lootbox base value, boost bonus percent, or role id.
ALTER TABLE economy_items ADD COLUMN IF NOT EXISTS effect_value BIGINT;
-- How long a boost effect lasts once used.
ALTER TABLE economy_items ADD COLUMN IF NOT EXISTS duration_minutes INTEGER;

-- Active timed boosts per member; at most one per kind (re-using extends the expiry).
CREATE TABLE IF NOT EXISTS economy_boosts
(
    user_id    BIGINT           NOT NULL,
    guild_id   BIGINT           NOT NULL,
    kind       TEXT             NOT NULL,  -- xp | loot
    multiplier DOUBLE PRECISION NOT NULL,
    expires_at TIMESTAMP        NOT NULL,
    PRIMARY KEY (user_id, guild_id, kind)
);
