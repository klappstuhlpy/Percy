//! Typed data access for the dashboard-owned tables in `dashboard.db`.
//!
//! Follows the shared kernel's `Table` row-mapping convention: each row type
//! implements [`kls_web_core::database::Table`] so it can be read back through
//! `Database::get` / `Database::all`, and mutations go through explicit SQL in
//! the repository functions here (the only place the dashboard writes SQL). The
//! two tables — `user_discord_admin_guilds` and `percy_leaderboard_vanity` — are
//! account-independent, so nothing here depends on klappstuhl.me. See
//! DASHBOARD_DECOUPLING_PLAN.md, Phase 6.
//!
//! The repository is intentionally ahead of its callers: the routes that consume
//! it are ported incrementally, so a few functions are currently only exercised
//! by tests. Remove this once the routes are wired.
#![allow(dead_code)]

use kls_web_core::{boxed_params, database::Table, Database};

/// A Discord guild the signed-in user can administrate, as captured from the
/// `/users/@me/guilds` OAuth response. Drives the "Add Percy" section.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AdminGuild {
    pub discord_user_id: String,
    pub guild_id: String,
    pub name: String,
    pub icon: Option<String>,
    pub owner: bool,
    pub updated_at: String,
}

impl Table for AdminGuild {
    const NAME: &'static str = "user_discord_admin_guilds";
    const COLUMNS: &'static [&'static str] = &["discord_user_id", "guild_id", "name", "icon", "owner", "updated_at"];
    // These tables are keyed by composite/business keys, not a surrogate `id`;
    // `Id` is required by the trait but unused here (we never call get_by_id).
    type Id = String;

    fn from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<Self> {
        Ok(Self {
            discord_user_id: row.get("discord_user_id")?,
            guild_id: row.get("guild_id")?,
            name: row.get("name")?,
            icon: row.get("icon")?,
            owner: row.get::<_, i64>("owner")? != 0,
            updated_at: row.get("updated_at")?,
        })
    }
}

/// A claimed vanity slug for a guild's public leaderboard page.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LeaderboardVanity {
    pub slug: String,
    pub guild_id: String,
    pub claimed_by: String,
    pub created_at: String,
}

impl Table for LeaderboardVanity {
    const NAME: &'static str = "percy_leaderboard_vanity";
    const COLUMNS: &'static [&'static str] = &["slug", "guild_id", "claimed_by", "created_at"];
    type Id = String;

    fn from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<Self> {
        Ok(Self {
            slug: row.get("slug")?,
            guild_id: row.get("guild_id")?,
            claimed_by: row.get("claimed_by")?,
            created_at: row.get("created_at")?,
        })
    }
}

/// Returns the guilds the given Discord user can administrate, newest first.
pub async fn admin_guilds_for_user(db: &Database, discord_user_id: &str) -> rusqlite::Result<Vec<AdminGuild>> {
    db.all(
        "SELECT * FROM user_discord_admin_guilds WHERE discord_user_id = ? ORDER BY name COLLATE NOCASE",
        boxed_params!(discord_user_id.to_owned()),
    )
    .await
}

/// Replaces the cached admin-guild set for a user with the freshly-fetched list
/// (from a login's `/users/@me/guilds`). Runs in one transaction: clear then
/// re-insert, so the cache exactly mirrors the latest OAuth response.
pub async fn replace_admin_guilds(
    db: &Database,
    discord_user_id: &str,
    guilds: Vec<AdminGuild>,
) -> rusqlite::Result<()> {
    let uid = discord_user_id.to_owned();
    db.call(move |conn| -> rusqlite::Result<()> {
        let tx = conn.transaction()?;
        tx.execute(
            "DELETE FROM user_discord_admin_guilds WHERE discord_user_id = ?",
            [&uid],
        )?;
        {
            let mut stmt = tx.prepare_cached(
                "INSERT INTO user_discord_admin_guilds(discord_user_id, guild_id, name, icon, owner) \
                 VALUES (?, ?, ?, ?, ?)",
            )?;
            for g in &guilds {
                stmt.execute(rusqlite::params![
                    g.discord_user_id,
                    g.guild_id,
                    g.name,
                    g.icon,
                    g.owner as i64
                ])?;
            }
        }
        tx.commit()
    })
    .await
}

/// Looks up the vanity claim for a guild, if any.
pub async fn vanity_for_guild(db: &Database, guild_id: &str) -> rusqlite::Result<Option<LeaderboardVanity>> {
    db.get(
        "SELECT * FROM percy_leaderboard_vanity WHERE guild_id = ?",
        boxed_params!(guild_id.to_owned()),
    )
    .await
}

/// Resolves a vanity slug to its claim, if the slug is taken.
pub async fn vanity_by_slug(db: &Database, slug: &str) -> rusqlite::Result<Option<LeaderboardVanity>> {
    db.get(
        "SELECT * FROM percy_leaderboard_vanity WHERE slug = ?",
        boxed_params!(slug.to_owned()),
    )
    .await
}

/// Claims `slug` for `guild_id`. Returns `false` if the slug (or the guild's
/// existing claim) collides — the claim is first-come-first-serve.
pub async fn claim_vanity(db: &Database, slug: &str, guild_id: &str, claimed_by: &str) -> rusqlite::Result<bool> {
    let affected = db
        .execute(
            "INSERT INTO percy_leaderboard_vanity(slug, guild_id, claimed_by) VALUES (?, ?, ?) \
             ON CONFLICT DO NOTHING",
            boxed_params!(slug.to_owned(), guild_id.to_owned(), claimed_by.to_owned()),
        )
        .await?;
    Ok(affected > 0)
}

/// Sets (claims or updates) a guild's vanity slug. A guild may change its own
/// slug freely; taking a slug another guild already holds is rejected with
/// `Err(msg)`. Mirrors the monolith's upsert-on-`guild_id` semantics.
pub async fn set_vanity(
    db: &Database,
    slug: &str,
    guild_id: &str,
    claimed_by: &str,
) -> rusqlite::Result<Result<(), &'static str>> {
    let (slug, gid, claimed_by) = (slug.to_owned(), guild_id.to_owned(), claimed_by.to_owned());
    db.call(move |conn| -> rusqlite::Result<Result<(), &'static str>> {
        let existing: Option<String> = conn
            .prepare_cached("SELECT guild_id FROM percy_leaderboard_vanity WHERE slug = ?")
            .and_then(|mut stmt| stmt.query_row([&slug], |row| row.get(0)))
            .ok();
        if let Some(ref owner_guild) = existing {
            if owner_guild != &gid {
                return Ok(Err("This vanity URL is already taken"));
            }
        }
        conn.execute(
            "INSERT INTO percy_leaderboard_vanity (slug, guild_id, claimed_by) VALUES (?, ?, ?) \
             ON CONFLICT(guild_id) DO UPDATE SET slug = excluded.slug",
            rusqlite::params![slug, gid, claimed_by],
        )?;
        Ok(Ok(()))
    })
    .await
}

/// Removes a guild's vanity slug (no-op if it has none).
pub async fn delete_vanity(db: &Database, guild_id: &str) -> rusqlite::Result<()> {
    db.execute(
        "DELETE FROM percy_leaderboard_vanity WHERE guild_id = ?",
        boxed_params!(guild_id.to_owned()),
    )
    .await
    .map(|_| ())
}

/// One row of the per-server change log (see `crate::changes`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Change {
    pub id: i64,
    pub guild_id: String,
    pub actor_id: String,
    pub actor_name: String,
    pub surface: String,
    pub verb: String,
    /// Comma-joined names of the fields the request carried (never their values).
    pub fields: String,
    pub created_at: String,
}

impl Table for Change {
    const NAME: &'static str = "dashboard_changes";
    const COLUMNS: &'static [&'static str] = &[
        "id",
        "guild_id",
        "actor_id",
        "actor_name",
        "surface",
        "verb",
        "fields",
        "created_at",
    ];
    type Id = i64;

    fn from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<Self> {
        Ok(Self {
            id: row.get("id")?,
            guild_id: row.get("guild_id")?,
            actor_id: row.get("actor_id")?,
            actor_name: row.get("actor_name")?,
            surface: row.get("surface")?,
            verb: row.get("verb")?,
            fields: row.get("fields")?,
            created_at: row.get("created_at")?,
        })
    }
}

/// A change about to be recorded.
#[derive(Debug, Clone)]
pub struct NewChange {
    pub guild_id: String,
    pub actor_id: String,
    pub actor_name: String,
    pub surface: String,
    pub verb: String,
    pub fields: String,
}

/// Stores a change and prunes every row older than `retention_days` (all guilds) in one
/// transaction, so the log never outgrows its retention window.
pub async fn record_change(db: &Database, change: NewChange, retention_days: u32) -> rusqlite::Result<()> {
    db.call(move |conn| -> rusqlite::Result<()> {
        let tx = conn.transaction()?;
        tx.execute(
            "INSERT INTO dashboard_changes(guild_id, actor_id, actor_name, surface, verb, fields) VALUES (?, ?, ?, ?, ?, ?)",
            rusqlite::params![
                change.guild_id,
                change.actor_id,
                change.actor_name,
                change.surface,
                change.verb,
                change.fields
            ],
        )?;
        tx.execute(
            "DELETE FROM dashboard_changes WHERE created_at < strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?)",
            [format!("-{retention_days} days")],
        )?;
        tx.commit()
    })
    .await
}

/// The newest changes for a guild, newest first.
pub async fn recent_changes(db: &Database, guild_id: &str, limit: u32) -> rusqlite::Result<Vec<Change>> {
    db.all(
        "SELECT * FROM dashboard_changes WHERE guild_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
        boxed_params!(guild_id.to_owned(), limit),
    )
    .await
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    async fn memory_db() -> Database {
        Database::file(Path::new(":memory:"))
            .connections(1)
            .with_init(crate::migrations::migrate)
            .open()
            .await
            .expect("open in-memory dashboard db")
    }

    #[tokio::test]
    async fn admin_guilds_round_trip() {
        let db = memory_db().await;
        let guilds = vec![
            AdminGuild {
                discord_user_id: "u1".into(),
                guild_id: "g2".into(),
                name: "Beta".into(),
                icon: None,
                owner: false,
                updated_at: String::new(),
            },
            AdminGuild {
                discord_user_id: "u1".into(),
                guild_id: "g1".into(),
                name: "Alpha".into(),
                icon: Some("hash".into()),
                owner: true,
                updated_at: String::new(),
            },
        ];
        replace_admin_guilds(&db, "u1", guilds).await.expect("replace");

        let got = admin_guilds_for_user(&db, "u1").await.expect("read back");
        assert_eq!(got.len(), 2);
        // Ordered by name COLLATE NOCASE → Alpha before Beta.
        assert_eq!(got[0].guild_id, "g1");
        assert!(got[0].owner);
        assert_eq!(got[0].icon.as_deref(), Some("hash"));
        assert_eq!(got[1].guild_id, "g2");

        // A different user sees nothing.
        assert!(admin_guilds_for_user(&db, "other").await.unwrap().is_empty());

        // Replacing again mirrors the new set exactly.
        replace_admin_guilds(&db, "u1", vec![]).await.expect("clear");
        assert!(admin_guilds_for_user(&db, "u1").await.unwrap().is_empty());
    }

    #[tokio::test]
    async fn vanity_claim_is_first_come_first_serve() {
        let db = memory_db().await;
        assert!(claim_vanity(&db, "cool", "g1", "u1").await.unwrap(), "first claim wins");
        // Same slug again → rejected.
        assert!(
            !claim_vanity(&db, "cool", "g2", "u2").await.unwrap(),
            "slug already taken"
        );
        // Same guild, different slug → rejected (guild_id is UNIQUE).
        assert!(
            !claim_vanity(&db, "other", "g1", "u1").await.unwrap(),
            "guild already has a slug"
        );

        let by_slug = vanity_by_slug(&db, "cool").await.unwrap().expect("slug resolves");
        assert_eq!(by_slug.guild_id, "g1");
        let by_guild = vanity_for_guild(&db, "g1").await.unwrap().expect("guild resolves");
        assert_eq!(by_guild.slug, "cool");
        assert!(vanity_by_slug(&db, "missing").await.unwrap().is_none());
    }

    #[tokio::test]
    async fn changes_are_recorded_newest_first_and_pruned_after_retention() {
        let db = memory_db().await;
        let change = |surface: &str| NewChange {
            guild_id: "1".into(),
            actor_id: "9".into(),
            actor_name: "mod".into(),
            surface: surface.into(),
            verb: "Updated".into(),
            fields: "enabled".into(),
        };
        record_change(&db, change("Leveling config"), 90).await.unwrap();
        db.execute(
            "UPDATE dashboard_changes SET created_at = '2000-01-01T00:00:00.000Z' WHERE guild_id = ?",
            boxed_params!("1".to_owned()),
        )
        .await
        .unwrap();
        record_change(&db, change("Economy settings"), 90).await.unwrap();

        let rows = recent_changes(&db, "1", 10).await.unwrap();
        assert_eq!(rows.len(), 1, "the 2000-era row is past retention and pruned");
        assert_eq!(rows[0].surface, "Economy settings");
        assert!(recent_changes(&db, "2", 10).await.unwrap().is_empty());
    }
}
