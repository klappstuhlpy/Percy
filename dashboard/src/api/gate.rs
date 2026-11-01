//! The public API's front door: the error envelope, the caller identity, and the two middlewares
//! (`guild_gate` for token-authenticated routes, `public_gate` for the open ones).
//!
//! `guild_gate` order: bearer token -> hash lookup -> **rate limit** -> guild binding -> scope.
//! The limit sits before the 403 checks (a deliberate tightening of "auth -> scope -> limit") so a
//! valid token cannot probe other guilds or scopes for free. A request that carries no valid
//! token is charged to the client's IP bucket instead, which bounds token guessing.

use axum::{
    extract::{ConnectInfo, Request, State},
    http::{header, HeaderMap, HeaderValue, Method, StatusCode},
    middleware::Next,
    response::{IntoResponse, Response},
    Json,
};

use super::{
    limit::{Verdict, IP_RATE, TOKEN_RATE},
    tokens,
};
use crate::{db, AppState};
use std::net::{IpAddr, SocketAddr};

/// How long a guild-changing API call stays in the change log (same window as dashboard edits).
const RETENTION_DAYS: u32 = crate::changes::RETENTION_DAYS;

// -- Errors ----------------------------------------------------------------------------------

/// `{"error": {"code", "message"}}`. The `code` is part of the contract, the message is not.
#[derive(Debug)]
pub(crate) struct ApiError {
    pub(crate) status: StatusCode,
    pub(crate) code: &'static str,
    pub(crate) message: String,
}

impl ApiError {
    pub(crate) fn new(status: StatusCode, code: &'static str, message: impl Into<String>) -> Self {
        Self {
            status,
            code,
            message: message.into(),
        }
    }

    pub(crate) fn bad_request(code: &'static str, message: impl Into<String>) -> Self {
        Self::new(StatusCode::BAD_REQUEST, code, message)
    }

    pub(crate) fn body(&self) -> serde_json::Value {
        serde_json::json!({"error": {"code": self.code, "message": self.message}})
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.status, Json(self.body())).into_response()
    }
}

/// Maps a refusal from Percy's private API onto the public contract. Percy's text is only used
/// to choose a code (and, for 400s, as the message); transport details never reach the caller.
pub(crate) fn from_percy(e: percy_client::PercyError) -> ApiError {
    use percy_client::PercyError;
    match e {
        PercyError::NotFound => ApiError::new(StatusCode::NOT_FOUND, "not_found", "Not found."),
        PercyError::Rejected { status, detail } => {
            let d = detail.to_lowercase();
            match status {
                400 if d.contains("negative") => ApiError::new(
                    StatusCode::BAD_REQUEST,
                    "insufficient_funds",
                    "That change would take cash or bank below zero.",
                ),
                400 | 422 => ApiError::bad_request(
                    "invalid_request",
                    if status == 422 {
                        "One or more fields have an invalid value.".to_string()
                    } else {
                        detail
                    },
                ),
                404 if d.contains("item") => ApiError::new(StatusCode::NOT_FOUND, "item_not_found", "Item not found."),
                404 if d.contains("guild") => {
                    ApiError::new(StatusCode::NOT_FOUND, "guild_not_found", "Percy is not in that server.")
                }
                404 => ApiError::new(StatusCode::NOT_FOUND, "not_found", "Not found."),
                409 if d.contains("limit") => ApiError::new(
                    StatusCode::CONFLICT,
                    "item_limit_reached",
                    "The member already holds the most of this item they may own.",
                ),
                409 if d.contains("own that many") => ApiError::new(
                    StatusCode::CONFLICT,
                    "insufficient_items",
                    "The member does not own that many of this item.",
                ),
                409 if d.contains("already exists") => ApiError::new(
                    StatusCode::CONFLICT,
                    "item_exists",
                    "An item with that name already exists.",
                ),
                409 => ApiError::new(StatusCode::CONFLICT, "conflict", detail),
                _ => upstream(),
            }
        }
        PercyError::Http(err) => {
            tracing::warn!(error = %err, "public API could not reach Percy");
            upstream()
        }
        PercyError::Api(_) => upstream(),
    }
}

fn upstream() -> ApiError {
    ApiError::new(
        StatusCode::BAD_GATEWAY,
        "upstream_unavailable",
        "Percy could not complete the request. Try again shortly.",
    )
}

// -- Caller ----------------------------------------------------------------------------------

/// Who is calling, as established by `guild_gate`. The guild comes from the token, never the path.
#[derive(Clone, Debug)]
pub(crate) struct Caller {
    pub(crate) token_id: i64,
    pub(crate) name: String,
    pub(crate) guild_id: u64,
}

/// Records a successful API write in the guild's change log as `api:<token name>`.
pub(crate) async fn audit(app: &AppState, caller: &Caller, surface: &str, verb: &str, fields: &str) {
    let change = db::NewChange {
        guild_id: caller.guild_id.to_string(),
        actor_id: format!("api:{}", caller.token_id),
        actor_name: format!("api:{}", caller.name),
        surface: surface.to_string(),
        verb: verb.to_string(),
        fields: fields.to_string(),
    };
    if let Err(e) = db::record_change(&app.db, change, RETENTION_DAYS).await {
        tracing::warn!(error = %e, "could not record API change");
    }
}

// -- Scopes ----------------------------------------------------------------------------------

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Required {
    /// Any valid token for the guild.
    Any,
    Scope(&'static str),
}

/// The scope a `(method, path-after-the-guild-id)` pair needs. `None` = not an API route, and
/// is refused: scopes are default-deny, so a route added without a row here is unusable.
pub(crate) fn required_scope(method: &Method, rest: &str) -> Option<Required> {
    use Required::{Any, Scope};
    let segs: Vec<&str> = rest.split('/').filter(|s| !s.is_empty()).collect();
    let read = *method == Method::GET;
    let write = matches!(*method, Method::POST | Method::PATCH | Method::PUT | Method::DELETE);
    match segs.as_slice() {
        [] if read => Some(Any),
        ["users"] if read => Some(Scope("economy:read")),
        ["users", _] if read => Some(Scope("economy:read")),
        ["users", _] if *method == Method::PATCH || *method == Method::PUT => Some(Scope("economy:write")),
        ["users", _, "inventory"] if read => Some(Scope("items:read")),
        ["users", _, "inventory"] if *method == Method::POST => Some(Scope("items:write")),
        ["users", _, "inventory", _] if *method == Method::DELETE => Some(Scope("items:write")),
        ["items"] if read => Some(Scope("items:read")),
        ["items"] if *method == Method::POST => Some(Scope("items:write")),
        ["items", _] if read => Some(Scope("items:read")),
        ["items", _] if write && *method != Method::POST && *method != Method::PUT => Some(Scope("items:write")),
        ["leveling", "leaderboard"] if read => Some(Scope("leveling:read")),
        ["leveling", "users", _] if read => Some(Scope("leveling:read")),
        _ => None,
    }
}

// -- Middleware ------------------------------------------------------------------------------

/// The client address for rate limiting. With `trusted_proxies = n > 0`, the entry `n` places
/// from the right of `X-Forwarded-For` (each proxy appends the address it saw, so only the
/// rightmost `n` are not client-forged), falling back to `X-Real-IP`. With `0`, headers are
/// ignored and the socket peer is used.
fn client_ip(headers: &HeaderMap, peer: Option<IpAddr>, trusted_proxies: u8) -> String {
    let peer = || peer.map_or_else(|| "unknown".to_string(), |p| p.to_string());
    if trusted_proxies == 0 {
        return peer();
    }
    headers
        .get("x-forwarded-for")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.rsplit(',').nth(usize::from(trusted_proxies) - 1))
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .or_else(|| {
            headers
                .get("x-real-ip")
                .and_then(|v| v.to_str().ok())
                .map(|s| s.trim().to_string())
        })
        .unwrap_or_else(peer)
}

fn request_ip(app: &AppState, request: &Request) -> String {
    let peer = request
        .extensions()
        .get::<ConnectInfo<SocketAddr>>()
        .map(|c| c.0.ip());
    client_ip(request.headers(), peer, app.config.trusted_proxies)
}

fn bearer(headers: &HeaderMap) -> Option<&str> {
    let value = headers.get(header::AUTHORIZATION)?.to_str().ok()?;
    let (scheme, token) = value.split_once(' ')?;
    (scheme.eq_ignore_ascii_case("bearer") && !token.trim().is_empty()).then(|| token.trim())
}

fn set_limit_headers(headers: &mut HeaderMap, v: &Verdict) {
    for (name, value) in [
        ("x-ratelimit-limit", v.limit as u64),
        ("x-ratelimit-remaining", v.remaining as u64),
        ("x-ratelimit-reset", v.reset_secs),
    ] {
        headers.insert(name, HeaderValue::from(value));
    }
}

fn rate_limited(v: &Verdict) -> Response {
    let mut resp = ApiError::new(
        StatusCode::TOO_MANY_REQUESTS,
        "rate_limited",
        format!("Too many requests. Retry in {} s.", v.retry_after_secs),
    )
    .into_response();
    set_limit_headers(resp.headers_mut(), v);
    resp.headers_mut()
        .insert(header::RETRY_AFTER, HeaderValue::from(v.retry_after_secs));
    resp
}

fn unauthorized(code: &'static str, message: &'static str) -> Response {
    let mut resp = ApiError::new(StatusCode::UNAUTHORIZED, code, message).into_response();
    resp.headers_mut()
        .insert(header::WWW_AUTHENTICATE, HeaderValue::from_static("Bearer"));
    resp
}

/// Token-authenticated routes under `/api/v1/guilds/{guild_id}`.
pub(crate) async fn guild_gate(State(app): State<AppState>, mut request: Request, next: Next) -> Response {
    let ip = request_ip(&app, &request);

    let Some(plaintext) = bearer(request.headers()).map(str::to_owned) else {
        let v = app.limiter.check(&format!("ip:{ip}"), IP_RATE);
        return if v.allowed {
            unauthorized("missing_token", "Send your token as `Authorization: Bearer pct_…`.")
        } else {
            rate_limited(&v)
        };
    };
    let token = match tokens::find_by_plaintext(&app.db, &plaintext).await {
        Ok(Some(t)) => t,
        Ok(None) => {
            let v = app.limiter.check(&format!("ip:{ip}"), IP_RATE);
            return if v.allowed {
                unauthorized("invalid_token", "That token is invalid or has been revoked.")
            } else {
                rate_limited(&v)
            };
        }
        Err(e) => {
            tracing::error!(error = %e, "api token lookup failed");
            return ApiError::new(StatusCode::INTERNAL_SERVER_ERROR, "internal_error", "Internal error.")
                .into_response();
        }
    };

    let verdict = app.limiter.check(&format!("token:{}", token.id), TOKEN_RATE);
    if !verdict.allowed {
        return rate_limited(&verdict);
    }

    // `/api/v1/guilds/{guild}/{rest…}`
    let path = request.uri().path().to_owned();
    let after = path.strip_prefix("/api/v1/guilds/").unwrap_or("");
    let (path_guild, rest) = after.split_once('/').unwrap_or((after, ""));
    let deny = |status, code, message: &str| {
        let mut resp = ApiError::new(status, code, message).into_response();
        set_limit_headers(resp.headers_mut(), &verdict);
        resp
    };
    if path_guild != token.guild_id {
        return deny(
            StatusCode::FORBIDDEN,
            "guild_mismatch",
            "This token belongs to a different server.",
        );
    }
    match required_scope(request.method(), rest) {
        Some(Required::Any) => {}
        Some(Required::Scope(s)) if token.has_scope(s) => {}
        Some(Required::Scope(s)) => {
            return deny(
                StatusCode::FORBIDDEN,
                "missing_scope",
                &format!("This token lacks the `{s}` scope."),
            )
        }
        None => {
            return deny(
                StatusCode::FORBIDDEN,
                "forbidden",
                "This token cannot call that endpoint.",
            )
        }
    }

    let db = app.db.clone();
    let token_id = token.id;
    tokio::spawn(async move { tokens::touch(&db, token_id).await });

    // `guild_id` is a decimal snowflake we stored from a validated path; parse cannot fail in practice.
    let Ok(guild_id) = token.guild_id.parse::<u64>() else {
        return deny(StatusCode::FORBIDDEN, "forbidden", "Invalid token.");
    };
    request.extensions_mut().insert(Caller {
        token_id: token.id,
        name: token.name,
        guild_id,
    });
    let mut response = next.run(request).await;
    set_limit_headers(response.headers_mut(), &verdict);
    response
}

/// Open routes (`/api/v1/bot/stats`): per-IP limit only.
pub(crate) async fn public_gate(State(app): State<AppState>, request: Request, next: Next) -> Response {
    let ip = request_ip(&app, &request);
    let verdict = app.limiter.check(&format!("ip:{ip}"), IP_RATE);
    if !verdict.allowed {
        return rate_limited(&verdict);
    }
    let mut response = next.run(request).await;
    set_limit_headers(response.headers_mut(), &verdict);
    response
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scopes_are_default_deny_and_writes_need_write() {
        use Required::{Any, Scope};
        let g = Method::GET;
        assert_eq!(required_scope(&g, ""), Some(Any));
        assert_eq!(required_scope(&g, "users/1"), Some(Scope("economy:read")));
        assert_eq!(required_scope(&Method::PATCH, "users/1"), Some(Scope("economy:write")));
        assert_eq!(required_scope(&Method::PUT, "users/1"), Some(Scope("economy:write")));
        assert_eq!(
            required_scope(&Method::POST, "users/1/inventory"),
            Some(Scope("items:write"))
        );
        assert_eq!(required_scope(&g, "users/1/inventory"), Some(Scope("items:read")));
        assert_eq!(required_scope(&Method::DELETE, "items/3"), Some(Scope("items:write")));
        assert_eq!(required_scope(&g, "leveling/users/9"), Some(Scope("leveling:read")));
        // Unlisted verbs and paths are refused.
        assert_eq!(required_scope(&Method::DELETE, "users/1"), None);
        assert_eq!(required_scope(&Method::POST, "users"), None);
        assert_eq!(required_scope(&Method::PUT, "items/3"), None);
        assert_eq!(required_scope(&g, "unknown"), None);
    }

    #[test]
    fn bearer_parsing_and_ip_use_the_trustworthy_hop() {
        let mut h = HeaderMap::new();
        assert!(bearer(&h).is_none());
        h.insert(header::AUTHORIZATION, HeaderValue::from_static("Bearer pct_abc"));
        assert_eq!(bearer(&h), Some("pct_abc"));
        h.insert(header::AUTHORIZATION, HeaderValue::from_static("Basic abc"));
        assert!(bearer(&h).is_none());

        let mut h = HeaderMap::new();
        assert_eq!(client_ip(&h, None, 1), "unknown");
        h.insert("x-forwarded-for", HeaderValue::from_static("6.6.6.6, 203.0.113.9"));
        assert_eq!(client_ip(&h, None, 1), "203.0.113.9");
        // Two proxies: the second from the right.
        assert_eq!(client_ip(&h, None, 2), "6.6.6.6");
        // Direct exposure: the forged header is ignored, the socket peer wins.
        let peer: IpAddr = "198.51.100.7".parse().unwrap();
        assert_eq!(client_ip(&h, Some(peer), 0), "198.51.100.7");
        // Trusted but header absent: fall back to the peer.
        assert_eq!(client_ip(&HeaderMap::new(), Some(peer), 1), "198.51.100.7");
    }

    #[test]
    fn percy_refusals_map_to_stable_codes() {
        use percy_client::PercyError;
        let rej = |status, d: &str| {
            from_percy(PercyError::Rejected {
                status,
                detail: d.into(),
            })
        };
        assert_eq!(rej(400, "balance cannot go negative").code, "insufficient_funds");
        assert_eq!(rej(404, "item not found").code, "item_not_found");
        assert_eq!(rej(409, "per-user item limit reached").code, "item_limit_reached");
        assert_eq!(rej(409, "member does not own that many").code, "insufficient_items");
        assert_eq!(rej(500, "boom").status, StatusCode::BAD_GATEWAY);
        assert_eq!(from_percy(PercyError::Api("x".into())).code, "upstream_unavailable");
    }
}
