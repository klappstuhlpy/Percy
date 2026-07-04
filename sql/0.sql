-- Dashboard-owned schema, v0.
--
-- These two tables are genuinely dashboard-domain and already keyed independently
-- of klappstuhl.me's `account` table, so they move here cleanly (see
-- DASHBOARD_DECOUPLING_PLAN.md, Phase 6). The account-bound Discord *identity*
-- link (klappstuhl.me's `user_discord_links`) intentionally does NOT move: it
-- backs the main site's own Discord login and is FK-joined into its session
-- resolution. The dashboard grows its own Discord-OAuth session model when auth
-- is ported, rather than inheriting the account table.
--
-- The runner owns `user_version`; do not set it here.

-- Discord guilds the signed-in user can manage, captured from the
-- `/users/@me/guilds` OAuth response at login. Drives the "Add Percy" section:
-- servers the user administrates that Percy is not yet a member of. Keyed by the
-- Discord user id, so it needs no account row.
CREATE TABLE IF NOT EXISTS user_discord_admin_guilds (
    discord_user_id TEXT    NOT NULL,
    guild_id        TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    icon            TEXT,
    owner           INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (discord_user_id, guild_id)
);

CREATE INDEX IF NOT EXISTS user_discord_admin_guilds_user_idx
    ON user_discord_admin_guilds (discord_user_id);

-- Vanity URLs for public leaderboard pages. First-come-first-serve; transferable
-- on request by updating the guild_id. Purely guild-keyed — no account needed.
CREATE TABLE IF NOT EXISTS percy_leaderboard_vanity (
    slug       TEXT NOT NULL PRIMARY KEY,
    guild_id   TEXT NOT NULL UNIQUE,
    claimed_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS percy_leaderboard_vanity_guild_idx
    ON percy_leaderboard_vanity (guild_id);
