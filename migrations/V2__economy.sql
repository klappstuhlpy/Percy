-- Economy: balances, shop, lottery, effects, jobs/pets/quests, market, vote rewards

-- == V9__casino_economy_migration ==
CREATE TABLE IF NOT EXISTS economy
(
    id       SERIAL PRIMARY KEY,
    user_id  BIGINT NOT NULL,
    guild_id BIGINT NOT NULL,
    cash     BIGINT NOT NULL DEFAULT 0,
    bank     BIGINT NOT NULL DEFAULT 0
);


-- == V18__economy_shop ==
CREATE TABLE IF NOT EXISTS economy_items
(
    id          SERIAL    PRIMARY KEY,
    guild_id    BIGINT    NOT NULL,
    name        TEXT      NOT NULL,
    description TEXT,
    price       BIGINT    NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc')
);

-- Case-insensitive uniqueness of item names within a guild.
CREATE UNIQUE INDEX IF NOT EXISTS economy_items_guild_name_idx ON economy_items (guild_id, lower(name));

CREATE TABLE IF NOT EXISTS economy_inventory
(
    user_id  BIGINT  NOT NULL,
    guild_id BIGINT  NOT NULL,
    item_id  INTEGER NOT NULL REFERENCES economy_items (id) ON DELETE CASCADE,
    quantity INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, guild_id, item_id)
);

CREATE TABLE IF NOT EXISTS economy_dailies
(
    user_id    BIGINT    NOT NULL,
    guild_id   BIGINT    NOT NULL,
    last_claim TIMESTAMP NOT NULL,
    streak     INTEGER   NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, guild_id)
);

-- == V20__community_economy_voice ==
-- A single active lottery per guild, with its pot and weighted ticket entries.
CREATE TABLE IF NOT EXISTS economy_lottery
(
    guild_id    BIGINT    PRIMARY KEY,
    channel_id  BIGINT    NOT NULL,
    ticket_price BIGINT   NOT NULL,
    jackpot     BIGINT    NOT NULL DEFAULT 0,
    ends_at     TIMESTAMP NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc')
);

CREATE TABLE IF NOT EXISTS economy_lottery_entries
(
    guild_id BIGINT NOT NULL REFERENCES economy_lottery (guild_id) ON DELETE CASCADE,
    user_id  BIGINT NOT NULL,
    tickets  BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);


-- == V24__economy_item_effects ==
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

-- == V26__economy_balance_index ==
-- The `economy` table only had its `id` primary key, so every balance access —
-- both the per-user lookup (`WHERE user_id = ? AND guild_id = ?`, run by every
-- economy command) and the dashboard leaderboard (`WHERE guild_id = ? ORDER BY
-- cash + bank`) — full-scanned the table across all guilds. This composite index
-- turns those into index scans. Its columns are immutable per row, so it adds no
-- write overhead on the frequent cash/bank UPDATEs (only on INSERT/DELETE).
CREATE INDEX IF NOT EXISTS economy_guild_user_idx ON economy (guild_id, user_id);

-- == V31__vote_rewards ==
-- A single active vote reward per user, granted by voting on a bot list (top.gg /
-- discordbotlist.com). The boost is global (applies in every guild the user shares
-- with the bot) and renewable: each vote resets ``expires_at`` to now + the reward
-- window. ``multiplier`` is the XP factor (e.g. 1.10 for +10%).
CREATE TABLE IF NOT EXISTS vote_rewards
(
    user_id      BIGINT           PRIMARY KEY,
    multiplier   DOUBLE PRECISION NOT NULL DEFAULT 1.10,
    expires_at   TIMESTAMP        NOT NULL,
    last_source  TEXT             NOT NULL,  -- top.gg | discordbotlist.com
    last_voted_at TIMESTAMP       NOT NULL DEFAULT (now() at time zone 'utc'),
    total_votes  INTEGER          NOT NULL DEFAULT 0
);

-- == V34__economy_expansion ==
-- Per-guild economy tuning; a missing row means "all defaults".
CREATE TABLE IF NOT EXISTS economy_settings
(
    guild_id          BIGINT PRIMARY KEY,
    payout_multiplier DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    rob_enabled       BOOLEAN          NOT NULL DEFAULT TRUE,
    daily_base        INTEGER          NOT NULL DEFAULT 250,
    max_bet           BIGINT -- NULL = uncapped
);

-- Weekly/monthly claim timestamps live next to the daily bookkeeping. A member
-- may claim weekly/monthly before ever claiming daily, so last_claim becomes nullable.
ALTER TABLE economy_dailies ADD COLUMN IF NOT EXISTS last_weekly TIMESTAMP;
ALTER TABLE economy_dailies ADD COLUMN IF NOT EXISTS last_monthly TIMESTAMP;
ALTER TABLE economy_dailies ALTER COLUMN last_claim DROP NOT NULL;

-- A member's current job on the static ladder (job ids live in code).
CREATE TABLE IF NOT EXISTS economy_jobs
(
    user_id  BIGINT    NOT NULL,
    guild_id BIGINT    NOT NULL,
    job_id   TEXT      NOT NULL,
    shifts   INTEGER   NOT NULL DEFAULT 0, -- lifetime shifts worked (never resets on job change)
    hired_at TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'),
    PRIMARY KEY (user_id, guild_id)
);

-- Permanent prestige levels; each level grants a payout multiplier bonus.
CREATE TABLE IF NOT EXISTS economy_prestige
(
    user_id       BIGINT  NOT NULL,
    guild_id      BIGINT  NOT NULL,
    level         INTEGER NOT NULL DEFAULT 0,
    last_prestige TIMESTAMP,
    PRIMARY KEY (user_id, guild_id)
);

-- Earned achievement badges (achievement ids live in code).
CREATE TABLE IF NOT EXISTS economy_achievements
(
    user_id     BIGINT    NOT NULL,
    guild_id    BIGINT    NOT NULL,
    achievement TEXT      NOT NULL,
    earned_at   TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'),
    PRIMARY KEY (user_id, guild_id, achievement)
);

-- One pet per member per guild (species ids live in code).
CREATE TABLE IF NOT EXISTS economy_pets
(
    user_id    BIGINT    NOT NULL,
    guild_id   BIGINT    NOT NULL,
    species    TEXT      NOT NULL,
    name       TEXT      NOT NULL,
    adopted_at TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'),
    last_fed   TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'),
    last_claim TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'),
    PRIMARY KEY (user_id, guild_id)
);

-- Daily quest board: rows are generated lazily per member per day.
CREATE TABLE IF NOT EXISTS economy_quests
(
    user_id   BIGINT  NOT NULL,
    guild_id  BIGINT  NOT NULL,
    day       DATE    NOT NULL,
    quest     TEXT    NOT NULL, -- pool key, e.g. 'fish_n'
    kind      TEXT    NOT NULL, -- progress hook, e.g. 'fish'
    goal      INTEGER NOT NULL,
    progress  INTEGER NOT NULL DEFAULT 0,
    reward    INTEGER NOT NULL,
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (user_id, guild_id, day, quest)
);

-- Quest completions are counted for achievements; keep lookups cheap.
CREATE INDEX IF NOT EXISTS economy_quests_completed_idx
    ON economy_quests (user_id, guild_id) WHERE completed;

-- == V51__economy_item_overhaul ==
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

-- == V52__economy_builtin_items ==
-- Set for rows materialized from app/services/economy/catalogue.py; NULL for admin-made items.
ALTER TABLE economy_items ADD COLUMN IF NOT EXISTS builtin_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS economy_items_guild_builtin_idx
    ON economy_items (guild_id, builtin_key) WHERE builtin_key IS NOT NULL;
-- FALSE hides the item from the shop (activity drops) while it still shows in inventories.
ALTER TABLE economy_items ADD COLUMN IF NOT EXISTS for_sale BOOLEAN NOT NULL DEFAULT TRUE;
-- Uses left on the member's current tool unit; NULL means untracked (not a tool, or a fresh unit).
ALTER TABLE economy_inventory ADD COLUMN IF NOT EXISTS durability INTEGER;

-- == V53__economy_passive_mode ==
-- One row per member who ever toggled; changed_at drives the toggle cooldown
-- (PASSIVE_COOLDOWN in app/services/economy/core.py), so it is kept when passive is turned off.
CREATE TABLE IF NOT EXISTS economy_passive
(
    user_id    BIGINT    NOT NULL,
    guild_id   BIGINT    NOT NULL,
    enabled    BOOLEAN   NOT NULL,
    changed_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    PRIMARY KEY (user_id, guild_id)
);

-- == V54__economy_bank_space ==
ALTER TABLE economy ADD COLUMN IF NOT EXISTS bank_space BIGINT NOT NULL DEFAULT 10000;

-- Grandfather existing banks above the default so nobody is over capacity.
UPDATE economy SET bank_space = GREATEST(bank_space, bank);

-- Guilds can turn the cap off (deposits are then unlimited).
ALTER TABLE economy_settings ADD COLUMN IF NOT EXISTS bank_cap BOOLEAN NOT NULL DEFAULT TRUE;

-- == V55__economy_drops ==
ALTER TABLE economy_settings ADD COLUMN IF NOT EXISTS drops_enabled BOOLEAN NOT NULL DEFAULT FALSE;

-- == V56__economy_market ==
CREATE TABLE IF NOT EXISTS economy_market
(
    id         BIGSERIAL PRIMARY KEY,
    guild_id   BIGINT    NOT NULL,
    seller_id  BIGINT    NOT NULL,
    item_id    BIGINT    NOT NULL REFERENCES economy_items (id) ON DELETE CASCADE,
    quantity   INTEGER   NOT NULL CHECK (quantity > 0),
    price_each BIGINT    NOT NULL CHECK (price_each > 0),
    created_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS economy_market_guild_created_idx ON economy_market (guild_id, created_at DESC);

-- == V57__economy_discoveries ==
-- Insert-if-absent on the primary key; first_found is when the member first got that drop.
CREATE TABLE IF NOT EXISTS economy_discoveries
(
    user_id     BIGINT    NOT NULL,
    guild_id    BIGINT    NOT NULL,
    builtin_key TEXT      NOT NULL,
    first_found TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    PRIMARY KEY (user_id, guild_id, builtin_key)
);

-- == V58__economy_profiles ==
CREATE TABLE IF NOT EXISTS economy_profiles
(
    user_id  BIGINT NOT NULL,
    guild_id BIGINT NOT NULL,
    title    TEXT,
    PRIMARY KEY (user_id, guild_id)
);

-- == V59__economy_vote_claim ==
ALTER TABLE economy_dailies ADD COLUMN IF NOT EXISTS last_vote_claim TIMESTAMP;

-- == V68__economy_item_images ==
-- https URL of an image shown as the item card's thumbnail; NULL falls back to a custom emoji's image.
ALTER TABLE economy_items ADD COLUMN IF NOT EXISTS image_url TEXT;
