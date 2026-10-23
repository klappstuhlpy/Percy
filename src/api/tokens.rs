//! API tokens: scopes, generation, hashing and the `api_tokens` / `api_idempotency` tables.
//!
//! A token is `pct_` + 43 base62 characters (~256 bits from the OS CSPRNG). Only its SHA-256 hex
//! is stored, which is enough for a secret that cannot be guessed; Argon2 would only slow every
//! request. The plaintext leaves this module once, in [`NewToken`], and is never logged.

use kls_web_core::Database;
use sha2::{Digest, Sha256};

pub(crate) const PREFIX: &str = "pct_";
/// Active tokens per guild; keeps a compromised admin session from minting unbounded credentials.
pub(crate) const MAX_ACTIVE_PER_GUILD: usize = 10;
/// How long a stored idempotent response is replayed.
pub(crate) const IDEMPOTENCY_HOURS: u32 = 24;

/// Every scope a token can carry. Anything else is rejected at creation and never matches.
pub(crate) const SCOPES: &[(&str, &str)] = &[
    ("economy:read", "Read balances and the richest-members list"),
    ("economy:write", "Change balances"),
    ("items:read", "Read the shop and member inventories"),
    ("items:write", "Create, edit and delete items; give and take inventory"),
    ("leveling:read", "Read levels and the leveling leaderboard"),
    ("stats:read", "Reserved for statistics endpoints"),
];

pub(crate) fn is_known_scope(scope: &str) -> bool {
    SCOPES.iter().any(|(s, _)| *s == scope)
}

/// A stored token row, minus the hash (which never leaves the database layer).
#[derive(Debug, Clone)]
pub(crate) struct ApiToken {
    pub(crate) id: i64,
    pub(crate) guild_id: String,
    pub(crate) name: String,
    pub(crate) scopes: Vec<String>,
    #[allow(dead_code)] // stored for audit; surfaced when the tab shows who minted a token
    pub(crate) created_by: String,
    pub(crate) created_at: String,
    pub(crate) last_used_at: Option<String>,
}

impl ApiToken {
    fn from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<Self> {
        let scopes: String = row.get("scopes")?;
        Ok(Self {
            id: row.get("id")?,
            guild_id: row.get("guild_id")?,
            name: row.get("name")?,
            scopes: scopes.split_whitespace().map(str::to_string).collect(),
            created_by: row.get("created_by")?,
            created_at: row.get("created_at")?,
            last_used_at: row.get("last_used_at")?,
        })
    }

    pub(crate) fn has_scope(&self, scope: &str) -> bool {
        self.scopes.iter().any(|s| s == scope)
    }
}

/// The only place a plaintext token exists: returned from [`create`] to be shown once.
pub(crate) struct NewToken {
    pub(crate) id: i64,
    pub(crate) plaintext: String,
}

pub(crate) fn hash(token: &str) -> String {
    let digest = Sha256::digest(token.as_bytes());
    digest.iter().map(|b| format!("{b:02x}")).collect()
}

/// Lowercase hex SHA-256 of arbitrary bytes (request fingerprints for idempotency).
pub(crate) fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes).iter().map(|b| format!("{b:02x}")).collect()
}

/// `pct_` + 43 base62 characters. Rejection sampling (bytes >= 248 are dropped) keeps the
/// alphabet uniform; modulo bias would otherwise favour the first characters.
pub(crate) fn generate() -> Result<String, getrandom::Error> {
    const ALPHABET: &[u8; 62] = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
    let mut out = String::from(PREFIX);
    let mut buf = [0u8; 64];
    let mut n = 0;
    while n < 43 {
        getrandom::getrandom(&mut buf)?;
        for b in buf {
            if b < 248 && n < 43 {
                out.push(ALPHABET[(b % 62) as usize] as char);
                n += 1;
            }
        }
    }
    Ok(out)
}

pub(crate) enum CreateError {
    TooMany,
    Random,
    Db(rusqlite::Error),
}

impl From<rusqlite::Error> for CreateError {
    fn from(e: rusqlite::Error) -> Self {
        Self::Db(e)
    }
}

/// Mints a token for a guild. `scopes` must already be validated.
pub(crate) async fn create(
    db: &Database,
    guild_id: &str,
    name: &str,
    scopes: &[String],
    created_by: &str,
) -> Result<NewToken, CreateError> {
    let plaintext = generate().map_err(|_| CreateError::Random)?;
    let (guild, name, scopes, by, digest) = (
        guild_id.to_owned(),
        name.to_owned(),
        scopes.join(" "),
        created_by.to_owned(),
        hash(&plaintext),
    );
    let id = db
        .call(move |conn| -> rusqlite::Result<Option<i64>> {
            let tx = conn.transaction()?;
            let active: usize = tx.query_row(
                "SELECT COUNT(*) FROM api_tokens WHERE guild_id = ? AND revoked_at IS NULL",
                [&guild],
                |r| r.get(0),
            )?;
            if active >= MAX_ACTIVE_PER_GUILD {
                return Ok(None);
            }
            tx.execute(
                "INSERT INTO api_tokens(guild_id, name, token_hash, scopes, created_by) VALUES (?, ?, ?, ?, ?)",
                rusqlite::params![guild, name, digest, scopes, by],
            )?;
            let id = tx.last_insert_rowid();
            tx.commit()?;
            Ok(Some(id))
        })
        .await?;
    match id {
        Some(id) => Ok(NewToken { id, plaintext }),
        None => Err(CreateError::TooMany),
    }
}

/// The live (not revoked) token whose hash matches, if any.
pub(crate) async fn find_by_plaintext(db: &Database, token: &str) -> rusqlite::Result<Option<ApiToken>> {
    let digest = hash(token);
    db.call(move |conn| -> rusqlite::Result<Option<ApiToken>> {
        let mut stmt = conn.prepare_cached("SELECT * FROM api_tokens WHERE token_hash = ? AND revoked_at IS NULL")?;
        let mut rows = stmt.query_map([&digest], ApiToken::from_row)?;
        rows.next().transpose()
    })
    .await
}

/// A guild's live tokens, newest first.
pub(crate) async fn list(db: &Database, guild_id: &str) -> rusqlite::Result<Vec<ApiToken>> {
    let guild = guild_id.to_owned();
    db.call(move |conn| -> rusqlite::Result<Vec<ApiToken>> {
        let mut stmt = conn.prepare(
            "SELECT * FROM api_tokens WHERE guild_id = ? AND revoked_at IS NULL ORDER BY created_at DESC, id DESC",
        )?;
        let rows = stmt.query_map([&guild], ApiToken::from_row)?;
        rows.collect()
    })
    .await
}

/// Revokes a token of this guild; `false` when it does not exist (or was already revoked).
pub(crate) async fn revoke(db: &Database, guild_id: &str, id: i64) -> rusqlite::Result<bool> {
    let guild = guild_id.to_owned();
    let n = db
        .execute(
            "UPDATE api_tokens SET revoked_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') \
             WHERE id = ? AND guild_id = ? AND revoked_at IS NULL",
            kls_web_core::boxed_params!(id, guild),
        )
        .await?;
    Ok(n > 0)
}

/// Stamps `last_used_at`, at most once a minute per token so reads stay write-free.
pub(crate) async fn touch(db: &Database, id: i64) {
    let _ = db
        .execute(
            "UPDATE api_tokens SET last_used_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') \
             WHERE id = ? AND (last_used_at IS NULL OR last_used_at < strftime('%Y-%m-%dT%H:%M:%fZ','now','-60 seconds'))",
            kls_web_core::boxed_params!(id),
        )
        .await;
}

// -- Idempotency ----------------------------------------------------------------------------

/// What claiming an `Idempotency-Key` found.
pub(crate) enum Claim {
    /// First time: the caller runs the request, then calls [`finish`] (or [`release`]).
    Fresh,
    /// Same key, same request, finished earlier: replay this.
    Replay { status: u16, body: String },
    /// Same key, different request.
    Mismatch,
    /// Same key and request, still running.
    InFlight,
}

pub(crate) async fn claim(db: &Database, token_id: i64, key: &str, request_hash: &str) -> rusqlite::Result<Claim> {
    let (key, request_hash) = (key.to_owned(), request_hash.to_owned());
    db.call(move |conn| -> rusqlite::Result<Claim> {
        let tx = conn.transaction()?;
        tx.execute(
            "DELETE FROM api_idempotency WHERE created_at < strftime('%Y-%m-%dT%H:%M:%fZ','now', ?)",
            [format!("-{IDEMPOTENCY_HOURS} hours")],
        )?;
        let inserted = tx.execute(
            "INSERT OR IGNORE INTO api_idempotency(token_id, idem_key, request_hash) VALUES (?, ?, ?)",
            rusqlite::params![token_id, key, request_hash],
        )?;
        let claim = if inserted > 0 {
            Claim::Fresh
        } else {
            let (hash, status, body): (String, i64, String) = tx.query_row(
                "SELECT request_hash, status, body FROM api_idempotency WHERE token_id = ? AND idem_key = ?",
                rusqlite::params![token_id, key],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )?;
            if hash != request_hash {
                Claim::Mismatch
            } else if status == 0 {
                Claim::InFlight
            } else {
                Claim::Replay {
                    status: status as u16,
                    body,
                }
            }
        };
        tx.commit()?;
        Ok(claim)
    })
    .await
}

pub(crate) async fn finish(db: &Database, token_id: i64, key: &str, status: u16, body: &str) {
    let _ = db
        .execute(
            "UPDATE api_idempotency SET status = ?, body = ? WHERE token_id = ? AND idem_key = ?",
            kls_web_core::boxed_params!(status as i64, body.to_owned(), token_id, key.to_owned()),
        )
        .await;
}

/// Drops a claim so a retry can run again (used when the request itself failed upstream).
pub(crate) async fn release(db: &Database, token_id: i64, key: &str) {
    let _ = db
        .execute(
            "DELETE FROM api_idempotency WHERE token_id = ? AND idem_key = ?",
            kls_web_core::boxed_params!(token_id, key.to_owned()),
        )
        .await;
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

    #[test]
    fn generated_tokens_have_the_documented_shape() {
        let a = generate().unwrap();
        let b = generate().unwrap();
        assert_ne!(a, b);
        assert!(a.starts_with("pct_"));
        assert_eq!(a.len(), 4 + 43);
        assert!(a[4..].chars().all(|c| c.is_ascii_alphanumeric()));
    }

    #[test]
    fn hash_is_sha256_hex() {
        // sha256("abc")
        assert_eq!(
            hash("abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    #[tokio::test]
    async fn token_lifecycle_stores_only_the_hash_and_revokes_instantly() {
        let db = memory_db().await;
        let scopes = vec!["economy:read".to_string()];
        let Ok(new) = create(&db, "42", "bot", &scopes, "7").await else {
            panic!("create failed")
        };

        let stored: String = db
            .call(|c| c.query_row("SELECT token_hash FROM api_tokens", [], |r| r.get(0)))
            .await
            .unwrap();
        assert_ne!(stored, new.plaintext);
        assert_eq!(stored, hash(&new.plaintext));

        let found = find_by_plaintext(&db, &new.plaintext).await.unwrap().expect("live");
        assert_eq!(found.guild_id, "42");
        assert!(found.has_scope("economy:read") && !found.has_scope("economy:write"));
        assert!(find_by_plaintext(&db, "pct_nope").await.unwrap().is_none());

        // A different guild cannot revoke it; the owner can, and it stops working at once.
        assert!(!revoke(&db, "43", new.id).await.unwrap());
        assert!(revoke(&db, "42", new.id).await.unwrap());
        assert!(find_by_plaintext(&db, &new.plaintext).await.unwrap().is_none());
        assert!(list(&db, "42").await.unwrap().is_empty());
    }

    #[tokio::test]
    async fn per_guild_token_cap_is_enforced() {
        let db = memory_db().await;
        let scopes = vec!["items:read".to_string()];
        for _ in 0..MAX_ACTIVE_PER_GUILD {
            assert!(create(&db, "1", "t", &scopes, "7").await.is_ok());
        }
        assert!(matches!(
            create(&db, "1", "t", &scopes, "7").await,
            Err(CreateError::TooMany)
        ));
        assert!(create(&db, "2", "t", &scopes, "7").await.is_ok());
    }

    #[tokio::test]
    async fn idempotency_claims_replay_and_conflict() {
        let db = memory_db().await;
        assert!(matches!(claim(&db, 1, "k", "h1").await.unwrap(), Claim::Fresh));
        assert!(matches!(claim(&db, 1, "k", "h1").await.unwrap(), Claim::InFlight));
        finish(&db, 1, "k", 200, "{\"a\":1}").await;
        match claim(&db, 1, "k", "h1").await.unwrap() {
            Claim::Replay { status, body } => assert_eq!((status, body.as_str()), (200, "{\"a\":1}")),
            _ => panic!("expected replay"),
        }
        assert!(matches!(claim(&db, 1, "k", "h2").await.unwrap(), Claim::Mismatch));
        // Keys are scoped per token.
        assert!(matches!(claim(&db, 2, "k", "h1").await.unwrap(), Claim::Fresh));
        release(&db, 2, "k").await;
        assert!(matches!(claim(&db, 2, "k", "h1").await.unwrap(), Claim::Fresh));
    }
}
