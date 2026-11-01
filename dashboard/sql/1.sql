-- The per-server change log: who changed what on the dashboard, and when (src/changes.rs).
-- Field *names* only, never values. Rows older than 90 days are pruned on write.
CREATE TABLE IF NOT EXISTS dashboard_changes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    TEXT    NOT NULL,
    actor_id    TEXT    NOT NULL,
    actor_name  TEXT    NOT NULL,
    surface     TEXT    NOT NULL,
    verb        TEXT    NOT NULL,
    fields      TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS dashboard_changes_guild_idx ON dashboard_changes (guild_id, created_at DESC);
CREATE INDEX IF NOT EXISTS dashboard_changes_created_idx ON dashboard_changes (created_at);
