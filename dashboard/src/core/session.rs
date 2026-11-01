//! The dashboard's own stateless session.
//!
//! Unlike klappstuhl.me (whose session is an `account.id` row + DB-backed token),
//! the dashboard is keyed by the Discord user id and keeps **no** session table:
//! the session is a small struct signed with the config [`SecretKey`] and stored
//! in a cookie. Verifying it is a pure HMAC check — no database round-trip. This
//! is why the shared `Token` type (built around an i64 account id) isn't reused.

use axum::{
    extract::FromRequestParts,
    http::{header::COOKIE, request::Parts},
    response::Redirect,
};
use cookie::Cookie;
use kls_web_core::key::SecretKey;
use serde::{Deserialize, Serialize};

use crate::AppState;

/// Name of the signed session cookie.
pub const SESSION_COOKIE: &str = "session";
/// How long a session stays valid.
const SESSION_TTL_SECS: i64 = 60 * 60 * 24 * 30;

/// A signed-in Discord user. Serialised compactly (it rides in a cookie) and
/// signed with the config key.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Session {
    #[serde(rename = "u")]
    pub discord_user_id: String,
    #[serde(rename = "n")]
    pub username: String,
    /// Unix-seconds expiry.
    #[serde(rename = "e")]
    pub expires: i64,
}

fn now_unix() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

impl Session {
    /// A fresh session for the given Discord identity, expiring in [`SESSION_TTL_SECS`].
    pub fn new(discord_user_id: impl Into<String>, username: impl Into<String>) -> Self {
        Self {
            discord_user_id: discord_user_id.into(),
            username: username.into(),
            expires: now_unix() + SESSION_TTL_SECS,
        }
    }

    /// Whether the session is still within its lifetime.
    pub fn is_valid(&self) -> bool {
        self.expires > now_unix()
    }

    /// The signed `Set-Cookie` value carrying this session.
    ///
    /// `domain` scopes the cookie to the registrable domain (`.klappstuhl.me`) so
    /// that logging out on the main site can clear it too (single sign-out). Pass
    /// `None` in dev (localhost/IP) for a host-only cookie — see
    /// [`crate::config::Config::cookie_domain`]. It must match the scope logout
    /// clears at, or the browser keeps a second, uncleared copy.
    pub fn to_cookie(&self, key: &SecretKey, domain: Option<&str>) -> Cookie<'static> {
        let signed = key.sign(self).expect("session serialises");
        let mut builder = Cookie::build((SESSION_COOKIE, signed))
            .path("/")
            .http_only(true)
            .same_site(cookie::SameSite::Lax)
            .max_age(cookie::time::Duration::seconds(SESSION_TTL_SECS));
        if let Some(domain) = domain {
            builder = builder.domain(domain.to_owned());
        }
        builder.build()
    }

    /// A `Set-Cookie` that clears the session (for logout).
    ///
    /// Host-only (no `Domain`) — matches the cookie [`Session::to_cookie`] sets.
    pub fn clearing_cookie() -> Cookie<'static> {
        Self::clearing_cookie_scoped(None)
    }

    /// A `Set-Cookie` that clears the session at a specific `Domain` scope.
    ///
    /// A host-only clear does **not** remove a cookie that was stored with a
    /// `Domain` attribute (the browser keys them separately), so logout emits a
    /// clear at the registrable-domain scope too — otherwise a domain-scoped
    /// `session` cookie (e.g. shared with the main site, or left by an earlier
    /// deploy) would survive logout and keep the user signed in. `None` yields the
    /// host-only clear.
    pub fn clearing_cookie_scoped(domain: Option<&str>) -> Cookie<'static> {
        let mut builder = Cookie::build((SESSION_COOKIE, ""))
            .path("/")
            .http_only(true)
            .same_site(cookie::SameSite::Lax)
            .expires(cookie::time::OffsetDateTime::UNIX_EPOCH);
        if let Some(domain) = domain {
            builder = builder.domain(domain.to_owned());
        }
        builder.build()
    }
}

/// Reads a named cookie's value from the request's `Cookie` header.
pub fn read_cookie(parts: &Parts, name: &str) -> Option<String> {
    let header = parts.headers.get(COOKIE)?.to_str().ok()?;
    Cookie::split_parse(header.to_owned())
        .filter_map(Result::ok)
        .find(|c| c.name() == name)
        .map(|c| c.value().to_owned())
}

/// Extractor for a valid session. Rejects (redirect to `/login`) when the cookie
/// is missing, tampered, or expired.
#[async_trait::async_trait]
impl FromRequestParts<AppState> for Session {
    type Rejection = Redirect;

    async fn from_request_parts(parts: &mut Parts, state: &AppState) -> Result<Self, Self::Rejection> {
        let raw = read_cookie(parts, SESSION_COOKIE).ok_or_else(|| Redirect::to("/login"))?;
        let session: Session = state
            .config
            .secret_key
            .verify(&raw)
            .filter(Session::is_valid)
            .ok_or_else(|| Redirect::to("/login"))?;
        Ok(session)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sign_verify_round_trip() {
        let key = SecretKey::random().unwrap();
        let session = Session::new("42", "ferris");
        let cookie = session.to_cookie(&key, None);
        assert_eq!(cookie.name(), SESSION_COOKIE);

        // A correctly-signed value verifies back to the same session.
        let back: Session = key.verify(cookie.value()).expect("verify");
        assert_eq!(back, session);
        assert!(back.is_valid());

        // A different key rejects it (tamper/forgery protection).
        let other = SecretKey::random().unwrap();
        assert!(other.verify::<Session>(cookie.value()).is_none());
    }

    #[test]
    fn expired_session_is_invalid() {
        let session = Session {
            discord_user_id: "1".into(),
            username: "x".into(),
            expires: now_unix() - 1,
        };
        assert!(!session.is_valid());
    }
}
