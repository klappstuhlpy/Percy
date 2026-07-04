//! Discord OAuth2 login for the dashboard.
//!
//! The dashboard authenticates users with their **own** Discord OAuth flow —
//! independent of klappstuhl.me's account system (per the decoupling plan):
//! `/login` redirects to Discord, `/auth/discord/callback` exchanges the code and
//! mints a signed [`Session`] cookie, `/logout` clears it. Admin guilds captured
//! from the OAuth response are cached in `dashboard.db` for the guild list.

use anyhow::Context;
use axum::{
    extract::{Query, State},
    http::{header::SET_COOKIE, HeaderMap, HeaderValue, StatusCode},
    response::{IntoResponse, Redirect, Response},
};
use cookie::Cookie;
use kls_web_core::key::SecretKey;
use serde::{de::DeserializeOwned, Deserialize, Deserializer};

use crate::{
    config::Config,
    db::{self, AdminGuild},
    session::Session,
    AppState,
};

const DISCORD_AUTHORIZE: &str = "https://discord.com/oauth2/authorize";
/// Pin the API version explicitly. Unversioned `/api` requests route to an old
/// default version where `/users/@me/guilds` returns `permissions` as a *number*
/// rather than the modern string, which breaks deserialization.
const DISCORD_API: &str = "https://discord.com/api/v10";
const OAUTH_SCOPES: &str = "identify guilds";
/// Short-lived cookie holding the CSRF `state` value between `/login` and callback.
const OAUTH_STATE_COOKIE: &str = "oauth_state";
/// Discord's `MANAGE_GUILD` permission bit — the dashboard treats a member with
/// it (or the owner) as able to administrate that guild.
const MANAGE_GUILD: u64 = 0x20;

/// Builds the Discord OAuth2 authorize URL, including the CSRF `state`.
pub fn authorize_url(config: &Config, state: &str) -> anyhow::Result<reqwest::Url> {
    let redirect = config.discord_redirect_uri();
    let url = reqwest::Url::parse_with_params(
        DISCORD_AUTHORIZE,
        &[
            ("client_id", config.discord.client_id.as_str()),
            ("redirect_uri", redirect.as_str()),
            ("response_type", "code"),
            ("scope", OAUTH_SCOPES),
            ("state", state),
        ],
    )?;
    Ok(url)
}

/// `GET /login` — start the Discord OAuth flow (or explain it's not configured).
pub async fn login(State(state): State<AppState>) -> Response {
    if !state.config.discord_login_enabled() {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            "Discord login is not configured on this dashboard.",
        )
            .into_response();
    }
    // CSRF: a random nonce echoed in both the URL and a short-lived cookie; the
    // callback requires the two to match.
    let csrf = SecretKey::random().map(|k| k.hex()).unwrap_or_default();
    let url = match authorize_url(&state.config, &csrf) {
        Ok(url) => url,
        Err(e) => {
            tracing::error!(error = %e, "failed to build the Discord authorize URL");
            return (StatusCode::INTERNAL_SERVER_ERROR, "could not start login").into_response();
        }
    };
    let cookie = Cookie::build((OAUTH_STATE_COOKIE, csrf))
        .path("/")
        .http_only(true)
        .same_site(cookie::SameSite::Lax)
        .max_age(cookie::time::Duration::minutes(10))
        .build();
    ([(SET_COOKIE, cookie.to_string())], Redirect::to(url.as_str())).into_response()
}

/// `GET /logout` — clear the session cookie and return home.
pub async fn logout() -> Response {
    (
        [(SET_COOKIE, Session::clearing_cookie().to_string())],
        Redirect::to("/"),
    )
        .into_response()
}

#[derive(Debug, Deserialize)]
pub struct CallbackQuery {
    code: Option<String>,
    state: Option<String>,
    error: Option<String>,
}

/// `GET /auth/discord/callback` — exchange the code, cache the user's admin
/// guilds, and set the session cookie. Any failure logs and returns to `/login`.
pub async fn callback(State(app): State<AppState>, headers: HeaderMap, Query(query): Query<CallbackQuery>) -> Response {
    if let Some(err) = query.error {
        tracing::info!(error = %err, "Discord OAuth denied");
        return Redirect::to("/").into_response();
    }
    let (Some(code), Some(returned_state)) = (query.code, query.state) else {
        return bad("missing code/state");
    };

    // CSRF: the state must match the cookie we set at /login.
    let parts_state = read_cookie_from(&headers, OAUTH_STATE_COOKIE);
    if parts_state.as_deref() != Some(returned_state.as_str()) {
        return bad("OAuth state mismatch");
    }

    match complete_login(&app, &code).await {
        Ok(session) => {
            let clear_state = Cookie::build((OAUTH_STATE_COOKIE, ""))
                .path("/")
                .expires(cookie::time::OffsetDateTime::UNIX_EPOCH)
                .build();
            let mut resp = Redirect::to("/dashboard").into_response();
            let h = resp.headers_mut();
            append_cookie(h, &session.to_cookie(&app.config.secret_key).to_string());
            append_cookie(h, &clear_state.to_string());
            resp
        }
        Err(e) => {
            tracing::error!(error = %e, "OAuth callback failed");
            Redirect::to("/login").into_response()
        }
    }
}

/// Reads a Discord API response, turning both HTTP errors and decode failures
/// into descriptive errors (Discord's error bodies and serde's type-mismatch
/// messages are far more useful than reqwest's opaque "error decoding response
/// body"). The success body is never echoed, so the token is not logged.
async fn read_json<T: DeserializeOwned>(resp: reqwest::Response, what: &str) -> anyhow::Result<T> {
    let status = resp.status();
    let body = resp
        .text()
        .await
        .with_context(|| format!("reading {what} response body"))?;
    if !status.is_success() {
        let snippet: String = body.chars().take(300).collect();
        anyhow::bail!("{what} returned HTTP {status}: {snippet}");
    }
    serde_json::from_str(&body).map_err(|e| anyhow::anyhow!("{what}: could not decode response ({e})"))
}

/// Runs the token exchange + profile/guild fetch and returns a fresh session.
async fn complete_login(app: &AppState, code: &str) -> anyhow::Result<Session> {
    let http = reqwest::Client::new();
    let redirect = app.config.discord_redirect_uri();

    // 1. Exchange the authorization code for an access token.
    let resp = http
        .post(format!("{DISCORD_API}/oauth2/token"))
        .form(&[
            ("client_id", app.config.discord.client_id.as_str()),
            ("client_secret", app.config.discord.client_secret.as_str()),
            ("grant_type", "authorization_code"),
            ("code", code),
            ("redirect_uri", redirect.as_str()),
        ])
        .send()
        .await?;
    let token: TokenResponse = read_json(resp, "token exchange").await?;

    // 2. Identity + guilds.
    let resp = http
        .get(format!("{DISCORD_API}/users/@me"))
        .bearer_auth(&token.access_token)
        .send()
        .await?;
    let user: DiscordUser = read_json(resp, "identity (/users/@me)").await?;

    let resp = http
        .get(format!("{DISCORD_API}/users/@me/guilds"))
        .bearer_auth(&token.access_token)
        .send()
        .await?;
    let guilds: Vec<DiscordGuild> = read_json(resp, "guilds (/users/@me/guilds)").await?;

    // 3. Cache the guilds the user can administrate.
    let admin: Vec<AdminGuild> = guilds
        .into_iter()
        .filter(DiscordGuild::is_admin)
        .map(|g| AdminGuild {
            discord_user_id: user.id.clone(),
            guild_id: g.id,
            name: g.name,
            icon: g.icon,
            owner: g.owner,
            updated_at: String::new(),
        })
        .collect();
    db::replace_admin_guilds(&app.db, &user.id, admin).await?;

    Ok(Session::new(user.id, user.username))
}

#[derive(Debug, Deserialize)]
struct TokenResponse {
    access_token: String,
}

#[derive(Debug, Deserialize)]
struct DiscordUser {
    id: String,
    username: String,
}

#[derive(Debug, Deserialize)]
struct DiscordGuild {
    id: String,
    name: String,
    icon: Option<String>,
    #[serde(default)]
    owner: bool,
    /// The member's permission bitfield in the guild. Modern Discord API versions
    /// return this as a string (`"2147483647"`); older ones as a number. Parsed
    /// tolerantly so either shape works.
    #[serde(default, deserialize_with = "de_permissions")]
    permissions: u64,
}

impl DiscordGuild {
    fn is_admin(&self) -> bool {
        self.owner || (self.permissions & MANAGE_GUILD != 0)
    }
}

/// Deserializes a Discord permission bitfield that may arrive as a string, a
/// number, or be absent — any unexpected shape yields `0` (no permissions).
fn de_permissions<'de, D: Deserializer<'de>>(de: D) -> Result<u64, D::Error> {
    Ok(match serde_json::Value::deserialize(de)? {
        serde_json::Value::String(s) => s.parse().unwrap_or(0),
        serde_json::Value::Number(n) => n.as_u64().unwrap_or(0),
        _ => 0,
    })
}

fn bad(msg: &'static str) -> Response {
    tracing::info!("OAuth callback rejected: {msg}");
    Redirect::to("/login").into_response()
}

fn read_cookie_from(headers: &HeaderMap, name: &str) -> Option<String> {
    let header = headers.get(axum::http::header::COOKIE)?.to_str().ok()?;
    Cookie::split_parse(header.to_owned())
        .filter_map(Result::ok)
        .find(|c| c.name() == name)
        .map(|c| c.value().to_owned())
}

fn append_cookie(headers: &mut HeaderMap, cookie: &str) {
    if let Ok(value) = HeaderValue::from_str(cookie) {
        headers.append(SET_COOKIE, value);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn config_with_creds() -> Config {
        let mut c = Config::test_default();
        c.discord.client_id = "123456".to_string();
        c.discord.client_secret = "shh".to_string();
        c.base_url = "https://dash.example".to_string();
        c
    }

    #[test]
    fn authorize_url_has_the_expected_params() {
        let url = authorize_url(&config_with_creds(), "csrf-nonce").expect("build url");
        let s = url.as_str();
        assert!(s.starts_with("https://discord.com/oauth2/authorize?"), "endpoint: {s}");
        assert!(s.contains("client_id=123456"), "client_id: {s}");
        assert!(s.contains("response_type=code"), "response_type: {s}");
        assert!(s.contains("state=csrf-nonce"), "state: {s}");
        assert!(
            s.contains("scope=identify+guilds") || s.contains("scope=identify%20guilds"),
            "scope: {s}"
        );
        assert!(
            s.contains("redirect_uri=https%3A%2F%2Fdash.example%2Fauth%2Fdiscord%2Fcallback"),
            "redirect_uri: {s}"
        );
    }

    #[test]
    fn admin_detection_uses_owner_or_manage_guild() {
        let owner = DiscordGuild {
            id: "1".into(),
            name: "a".into(),
            icon: None,
            owner: true,
            permissions: 0,
        };
        let manager = DiscordGuild {
            id: "2".into(),
            name: "b".into(),
            icon: None,
            owner: false,
            permissions: 32, // MANAGE_GUILD
        };
        let member = DiscordGuild {
            id: "3".into(),
            name: "c".into(),
            icon: None,
            owner: false,
            permissions: 1024, // some other bit
        };
        assert!(owner.is_admin());
        assert!(manager.is_admin());
        assert!(!member.is_admin());
    }

    #[test]
    fn permissions_deserialize_from_string_or_number() {
        // Modern API: string bitfield.
        let g: DiscordGuild =
            serde_json::from_str(r#"{"id":"1","name":"x","icon":null,"owner":false,"permissions":"32"}"#).unwrap();
        assert!(g.is_admin());
        // Older API: numeric bitfield.
        let g: DiscordGuild =
            serde_json::from_str(r#"{"id":"1","name":"x","icon":null,"owner":false,"permissions":32}"#).unwrap();
        assert!(g.is_admin());
        // Absent → no permissions.
        let g: DiscordGuild = serde_json::from_str(r#"{"id":"1","name":"x","icon":null,"owner":false}"#).unwrap();
        assert!(!g.is_admin());
    }
}
