//! The per-server **change log**: who changed what on the dashboard, and when.
//!
//! Recorded here in the BFF, not in Percy: the dashboard is the only party that knows which
//! signed-in user made a request, and the log is dashboard-owned data (`dashboard.db`), so the
//! "never write Percy's database" rule is untouched. A middleware wraps every guild-scoped
//! mutation and, once the handler answered 2xx, stores one row: the actor, the surface, and the
//! *names* of the fields the request carried — never their values, which can hold webhook URLs
//! or message text. Rows older than [`RETENTION_DAYS`] are pruned on write.

use crate::{db, guilds::gate_and_load_guild, session::Session, templates::ChangesTemplate, view::Viewer, AppState};
use axum::{
    body::{to_bytes, Body},
    extract::{Path, Request, State},
    http::{header, Method},
    middleware::Next,
    response::{IntoResponse, Response},
};

/// How long a change stays in the log.
pub(crate) const RETENTION_DAYS: u32 = 90;
/// JSON bodies above this are forwarded untouched and logged without field names.
const MAX_INSPECT_BYTES: usize = 256 * 1024;

/// Paths under a guild that are not configuration changes: the member's own pages, read-only
/// helpers that happen to be POSTs, and live player controls.
const IGNORED: &[&str] = &[
    "me",
    "overview",
    "ai/ask",
    "music/control",
    "music/lyrics",
    "changes",
    "leveling/test-message",
];

/// What a request changed, in words: `("Leveling", "Updated")`, plus the guild id.
#[derive(Debug, PartialEq, Eq)]
pub(crate) struct Described {
    pub(crate) guild_id: u64,
    pub(crate) surface: String,
    pub(crate) verb: &'static str,
}

/// `None` for anything that is not a guild-scoped mutation worth logging.
pub(crate) fn describe(method: &Method, path: &str) -> Option<Described> {
    let verb = match *method {
        Method::POST => "Changed",
        Method::PATCH | Method::PUT => "Updated",
        Method::DELETE => "Removed",
        _ => return None,
    };
    let rest = path.strip_prefix("/dashboard/guild/")?;
    let (id, tail) = rest.split_once('/').unwrap_or((rest, ""));
    let guild_id = id.parse().ok()?;
    if IGNORED.iter().any(|p| tail == *p || tail.starts_with(&format!("{p}/"))) {
        return None;
    }
    let segments: Vec<&str> = tail.split('/').filter(|s| !s.is_empty()).collect();
    let (surface, verb) = match segments.as_slice() {
        [] | ["config"] | ["batch"] => ("Server settings".to_string(), verb),
        ["members", "bulk-action"] => ("Members (bulk action)".to_string(), "Ran"),
        ["members", _, "action"] => ("Member action".to_string(), "Ran"),
        ["members", _, "roles"] => ("Member roles".to_string(), "Updated"),
        [.., "delete"] => (title(&segments[..segments.len() - 1]), "Removed"),
        [.., "end"] => (title(&segments[..segments.len() - 1]), "Ended"),
        _ => (title(&segments), verb),
    };
    Some(Described {
        guild_id,
        surface,
        verb,
    })
}

/// `["leveling", "config"]` -> `"Leveling config"`; numeric ids are dropped.
fn title(segments: &[&str]) -> String {
    let words: Vec<String> = segments
        .iter()
        .filter(|s| !s.chars().all(|c| c.is_ascii_digit()))
        .map(|s| s.replace('-', " "))
        .collect();
    let mut text = words.join(" ");
    if let Some(first) = text.get(0..1) {
        text.replace_range(0..1, &first.to_uppercase());
    }
    if text.is_empty() {
        "Server settings".into()
    } else {
        text
    }
}

/// Top-level JSON keys of a body, sorted, comma-joined (values are never kept).
fn field_names(bytes: &[u8]) -> String {
    match serde_json::from_slice::<serde_json::Value>(bytes) {
        Ok(serde_json::Value::Object(map)) => {
            let mut keys: Vec<&str> = map.keys().map(String::as_str).collect();
            keys.sort_unstable();
            keys.join(", ")
        }
        _ => String::new(),
    }
}

/// Middleware: records successful guild-scoped mutations (see the module docs).
pub async fn record(State(app): State<AppState>, session: Option<Session>, request: Request, next: Next) -> Response {
    let (Some(session), Some(described)) = (session, describe(request.method(), request.uri().path())) else {
        return next.run(request).await;
    };

    let headers = request.headers();
    let is_json = headers
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .is_some_and(|v| v.starts_with("application/json"));
    // Only a body whose declared length is small is buffered; anything else (a backup import,
    // a chunked upload) is forwarded untouched and logged without field names.
    let small = headers
        .get(header::CONTENT_LENGTH)
        .and_then(|v| v.to_str().ok()?.parse::<usize>().ok())
        .is_some_and(|len| len <= MAX_INSPECT_BYTES);
    let (request, fields) = if is_json && small {
        let (parts, body) = request.into_parts();
        match to_bytes(body, MAX_INSPECT_BYTES).await {
            Ok(bytes) => {
                let fields = field_names(&bytes);
                (Request::from_parts(parts, Body::from(bytes)), fields)
            }
            // The client sent more than it declared; the body is consumed, so refuse it.
            Err(_) => return axum::http::StatusCode::PAYLOAD_TOO_LARGE.into_response(),
        }
    } else {
        (request, String::new())
    };

    let response = next.run(request).await;
    // Many handlers answer a refusal as `200 {"ok": false, ...}`, so a 2xx alone is not success.
    let (response, succeeded) = confirmed_success(response).await;
    if succeeded {
        let change = db::NewChange {
            guild_id: described.guild_id.to_string(),
            actor_id: session.discord_user_id.clone(),
            actor_name: session.username.clone(),
            surface: described.surface,
            verb: described.verb.to_string(),
            fields,
        };
        if let Err(e) = db::record_change(&app.db, change, RETENTION_DAYS).await {
            tracing::warn!(error = %e, "could not record dashboard change");
        }
    }
    response
}

/// A 2xx response whose JSON body (if any) does not say `"ok": false`. A JSON body is
/// buffered to read it and handed back intact; redirects (form posts) count as success.
async fn confirmed_success(response: Response) -> (Response, bool) {
    let status = response.status();
    if !(status.is_success() || status.is_redirection()) {
        return (response, false);
    }
    let is_json = response
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .is_some_and(|v| v.starts_with("application/json"));
    if !is_json {
        return (response, true);
    }
    let (parts, body) = response.into_parts();
    match to_bytes(body, 4 * 1024 * 1024).await {
        Ok(bytes) => {
            let refused = serde_json::from_slice::<serde_json::Value>(&bytes)
                .ok()
                .and_then(|v| v.get("ok").and_then(serde_json::Value::as_bool))
                == Some(false);
            (Response::from_parts(parts, Body::from(bytes)), !refused)
        }
        Err(_) => (axum::http::StatusCode::BAD_GATEWAY.into_response(), false),
    }
}

/// `GET /dashboard/guild/:guild_id/changes` — the last changes made on the dashboard.
pub async fn guild_changes(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let changes = db::recent_changes(&app.db, &guild_id.to_string(), 200)
        .await
        .unwrap_or_default();
    ChangesTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "changes",
        page_title: "Change Log",
        changes,
        retention_days: RETENTION_DAYS,
    }
    .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(method: Method, path: &str) -> Option<(String, &'static str)> {
        describe(&method, path).map(|d| (d.surface, d.verb))
    }

    #[test]
    fn describes_guild_mutations_in_words() {
        assert_eq!(
            d(Method::POST, "/dashboard/guild/1/leveling/config"),
            Some(("Leveling config".into(), "Changed"))
        );
        assert_eq!(
            d(Method::PATCH, "/dashboard/guild/1/config"),
            Some(("Server settings".into(), "Updated"))
        );
        assert_eq!(
            d(Method::POST, "/dashboard/guild/1/members/55/action"),
            Some(("Member action".into(), "Ran"))
        );
        assert_eq!(
            d(Method::POST, "/dashboard/guild/1/gallery/77/delete"),
            Some(("Gallery".into(), "Removed"))
        );
        assert_eq!(
            d(Method::POST, "/dashboard/guild/1/polls/9/end"),
            Some(("Polls".into(), "Ended"))
        );
        assert_eq!(
            d(Method::POST, "/dashboard/guild/1/temp-channels"),
            Some(("Temp channels".into(), "Changed"))
        );
    }

    #[test]
    fn skips_reads_personal_pages_and_live_controls() {
        assert_eq!(d(Method::GET, "/dashboard/guild/1/leveling"), None);
        assert_eq!(d(Method::POST, "/dashboard/guild/1/me/rank-card"), None);
        assert_eq!(d(Method::POST, "/dashboard/guild/1/music/control"), None);
        assert_eq!(d(Method::POST, "/dashboard/guild/1/ai/ask"), None);
        assert_eq!(d(Method::POST, "/account/progress"), None);
        assert_eq!(d(Method::POST, "/dashboard/guild/notanid/config"), None);
    }

    #[test]
    fn keeps_field_names_never_values() {
        assert_eq!(
            field_names(br#"{"webhook_url":"https://secret","enabled":true}"#),
            "enabled, webhook_url"
        );
        assert_eq!(field_names(b"not json"), "");
    }
}
