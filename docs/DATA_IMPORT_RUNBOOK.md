# Data import runbook (Phase 6 cutover)

> **Do not run this against production yet.** This is the procedure for the *final*
> cutover, when the dashboard has its own auth + the routes that read these
> tables. Running it before then creates split-brain: `dashboard.db` would hold a
> stale copy of rows that klappstuhl.me still owns and mutates. See
> `DASHBOARD_DECOUPLING_PLAN.md`, Phase 6.

## What moves, and what does not

`dashboard.db` (this repo, `sql/0.sql`) owns two account-independent tables:

| Table | Source (klappstuhl.me `main.db`) | Notes |
|-------|----------------------------------|-------|
| `user_discord_admin_guilds` | same table | Keyed by `discord_user_id`; copies verbatim. |
| `percy_leaderboard_vanity` | same table | Keyed by `guild_id`/`slug`; copies verbatim. |

**`user_discord_links` deliberately does NOT move.** It is FK-bound to
klappstuhl.me's `account` table and is JOINed into that app's own session
resolution (its "log in with Discord"). klappstuhl.me keeps it. The dashboard
builds its **own** Discord-OAuth session/identity model when auth is ported —
it does not inherit the `account` table. So there is no row-level identity
migration here, only the two self-contained tables above.

## Preconditions

1. The dashboard's auth + the routes that read these tables are ported and
   deployed behind a flag (dark).
2. A **read-only** copy of klappstuhl.me's `main.db` is available to the importer
   (never point the importer at the live file — copy it first).
3. `dashboard.db` has been created and migrated to the current version
   (`user_version` matches `sql/`), i.e. the dashboard has booted at least once.

## Procedure (at cutover)

Take a backup of `dashboard.db`, then copy the two tables across with the
`sqlite3` CLI. `INSERT OR IGNORE` makes the import re-runnable (idempotent) if it
is interrupted.

```bash
# 0. Stop writers to the tables on the klappstuhl.me side (put the relevant
#    dashboard routes into maintenance/read-only), then snapshot main.db:
cp /data/data/klappstuhl_me/main.db /tmp/main.readonly.db

# 1. Back up the dashboard database:
cp "$DASHBOARD_DB" "$DASHBOARD_DB.pre-import.bak"

# 2. Copy the two tables (attach the read-only source, insert-or-ignore):
sqlite3 "$DASHBOARD_DB" <<'SQL'
ATTACH DATABASE '/tmp/main.readonly.db' AS src;
BEGIN IMMEDIATE;
INSERT OR IGNORE INTO user_discord_admin_guilds
    SELECT discord_user_id, guild_id, name, icon, owner, updated_at
    FROM src.user_discord_admin_guilds;
INSERT OR IGNORE INTO percy_leaderboard_vanity
    SELECT slug, guild_id, claimed_by, created_at
    FROM src.percy_leaderboard_vanity;
COMMIT;
DETACH DATABASE src;
SQL

# 3. Verify row counts match:
sqlite3 /tmp/main.readonly.db "SELECT 'src admin', COUNT(*) FROM user_discord_admin_guilds;
                               SELECT 'src vanity', COUNT(*) FROM percy_leaderboard_vanity;"
sqlite3 "$DASHBOARD_DB"       "SELECT 'dst admin', COUNT(*) FROM user_discord_admin_guilds;
                               SELECT 'dst vanity', COUNT(*) FROM percy_leaderboard_vanity;"
```

## After the import

1. Flip the dashboard routes to read/write `dashboard.db` (remove the dark flag).
2. Once verified in production, drop the migrated tables from klappstuhl.me with a
   **new** forward migration in its `sql/` (never edit an applied one) — but only
   after a soak period with backups retained.
3. `rm /tmp/main.readonly.db` (it contained a copy of production data).

## Rollback

Restore `dashboard.db` from `"$DASHBOARD_DB.pre-import.bak"` and re-point the
dashboard routes back to the flagged-off state. klappstuhl.me still holds the
authoritative rows until step 2 above, so no data is lost.
