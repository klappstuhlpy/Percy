//! The **Notifications** page: the inbox feed, moderator action requests awaiting a decision,
//! recent moderation, and the request settings. Everything is read from, and written through,
//! Percy's internal API (the BFF never touches Percy's database).
//!
//! Every control is a plain form that works with JS off; `notifications.js` only upgrades them
//! (confirm on destructive approvals, in-place card update). The deciding moderator is always the
//! session's Discord id, never a form field.

use crate::{
    errors::percy_message,
    forms::is_ajax,
    guilds::gate_and_load_guild,
    mutations::{gate_json, ok_or_err},
    session::Session,
    templates::NotificationsTemplate,
    view::Viewer,
    AppState,
};
use axum::{
    body::Bytes,
    extract::{Path, Query, State},
    http::{header, HeaderMap},
    response::{IntoResponse, Redirect, Response},
    Form, Json,
};
use percy_client::{ActionRequest, Notification};
use serde::Deserialize;

/// A feed row with the bits the template would otherwise have to compute.
pub(crate) struct FeedRow {
    pub(crate) n: Notification,
    pub(crate) when: String,
    /// Only same-site paths are linked; anything else Percy sent is not made clickable.
    pub(crate) link: Option<String>,
}

pub(crate) struct RequestRow {
    pub(crate) r: ActionRequest,
    pub(crate) requester: String,
    pub(crate) target: String,
    pub(crate) duration: Option<String>,
    pub(crate) created: String,
    pub(crate) expires: String,
    /// Kick/ban/softban cannot be undone, so approving asks first (the JS reads this).
    pub(crate) destructive: bool,
}

pub(crate) struct CaseRow {
    pub(crate) action: String,
    pub(crate) target: String,
    pub(crate) moderator: String,
    pub(crate) when: String,
}

/// `2026-10-21T12:30:00.123Z` -> `2026-10-21 12:30` (JS swaps in a relative time).
fn short_time(iso: &str) -> String {
    iso.get(..16).unwrap_or(iso).replace('T', " ")
}

fn duration_label(secs: i64) -> String {
    match secs {
        s if s >= 86_400 && s % 86_400 == 0 => format!("{}d", s / 86_400),
        s if s >= 3_600 && s % 3_600 == 0 => format!("{}h", s / 3_600),
        s if s >= 60 && s % 60 == 0 => format!("{}m", s / 60),
        s => format!("{s}s"),
    }
}

/// `YYYY-MM-DDTHH:MM:SSZ` for `days` ago (Hinnant's civil-from-days; no date crate needed).
fn iso_days_ago(days: u64) -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
        .saturating_sub(days * 86_400);
    let (d, rem) = ((now / 86_400) as i64, now % 86_400);
    let z = d + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        rem % 3600 / 60,
        rem % 60
    )
}

/// The signed-in Discord id as a number; never 0.
fn uid(session: &Session) -> Option<u64> {
    session.discord_user_id.parse().ok().filter(|id| *id != 0)
}

fn bad_session() -> Response {
    Json(serde_json::json!({"ok": false, "error": "Your session has no valid Discord id; sign in again."}))
        .into_response()
}

fn safe_link(link: Option<&String>) -> Option<String> {
    link.filter(|l| l.starts_with('/') && !l.starts_with("//") && !l.contains('\\'))
        .cloned()
}

/// Percent-encodes a notice for the redirect query (no extra crate for one message).
fn encode(text: &str) -> String {
    text.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' => (b as char).to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
}

#[derive(Deserialize, Default)]
pub struct NoticeQuery {
    notice: Option<String>,
    ok: Option<String>,
}

/// `GET /dashboard/guild/:guild_id/notifications`
pub async fn guild_notifications(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Query(notice): Query<NoticeQuery>,
) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");
    let Some(user_id) = uid(&session) else {
        return Redirect::to("/dashboard").into_response();
    };
    let since = iso_days_ago(7);
    let (feed, requests, settings, channels, roles, cases) = tokio::join!(
        percy.get_notifications(guild_id, user_id, 50, None),
        percy.get_action_requests(guild_id, "all", 40),
        percy.get_request_settings(guild_id),
        percy.get_guild_channels(guild_id),
        percy.get_guild_roles(guild_id),
        percy.get_recent_cases(guild_id, &since),
    );
    let feed = feed.unwrap_or_default();
    let everyone = guild_id.to_string();
    let rows: Vec<RequestRow> = requests
        .unwrap_or_default()
        .items
        .into_iter()
        .map(|r| RequestRow {
            requester: r.requester_name.clone().unwrap_or_else(|| r.requester_id.clone()),
            target: r.target_name.clone().unwrap_or_else(|| r.target_id.clone()),
            duration: r.duration.map(duration_label),
            created: short_time(&r.created_at),
            expires: short_time(&r.expires_at),
            destructive: matches!(r.action.as_str(), "ban" | "kick" | "softban"),
            r,
        })
        .collect();
    let (pending, history): (Vec<_>, Vec<_>) = rows.into_iter().partition(|r| r.r.status == "pending");
    let mut cases = cases.map(|c| c.cases).unwrap_or_default();
    cases.sort_by(|a, b| b.created_at.cmp(&a.created_at));
    let settings_ok = settings.is_ok();
    let settings = settings.unwrap_or_default();
    let changes = crate::db::recent_changes(&app.db, &guild_id.to_string(), 200)
        .await
        .unwrap_or_default();
    NotificationsTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "notifications",
        page_title: "Notifications",
        unread: feed.unread.max(0) as u32,
        newest_id: feed.items.first().map(|n| n.id.to_string()).unwrap_or_default(),
        feed: feed
            .items
            .into_iter()
            .map(|n| FeedRow {
                when: short_time(&n.created_at),
                link: safe_link(n.link.as_ref()),
                n,
            })
            .collect(),
        pending,
        history,
        cases: cases
            .into_iter()
            .take(10)
            .map(|c| CaseRow {
                action: c.action,
                target: c.target_name,
                moderator: c.moderator_name.unwrap_or_default(),
                when: c.created_at.as_deref().map(short_time).unwrap_or_default(),
            })
            .collect(),
        selected_channel: settings.channel.as_ref().map(|c| c.id.clone()).unwrap_or_default(),
        selected_roles: settings.role_ids.clone(),
        settings_ok,
        channels: channels
            .unwrap_or_default()
            .into_iter()
            .filter(|c| c.type_ == "text")
            .collect(),
        roles: roles
            .unwrap_or_default()
            .into_iter()
            .filter(|r| r.id != everyone)
            .collect(),
        notice: notice.notice.unwrap_or_default().chars().take(300).collect(),
        notice_ok: notice.ok.as_deref() == Some("1"),
        changes,
        retention_days: crate::changes::RETENTION_DAYS,
    }
    .into_response()
}

/// `GET /dashboard/guild/:guild_id/notifications/summary` — what the sidebar badge shows.
pub async fn notifications_summary(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let Some(user_id) = uid(&session) else {
        return bad_session();
    };
    match percy.get_notification_summary(guild_id, user_id).await {
        Ok(s) => Json(serde_json::json!({"ok": true, "unread": s.unread, "pending_requests": s.pending_requests}))
            .into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/notifications/read` — form (`up_to_id=`) or JSON (`{up_to_id}`).
pub async fn notifications_read(
    State(app): State<AppState>,
    session: Session,
    headers: HeaderMap,
    Path(guild_id): Path<u64>,
    body: Bytes,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let is_json = headers
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .is_some_and(|v| v.starts_with("application/json"));
    let up_to = if is_json {
        serde_json::from_slice::<serde_json::Value>(&body)
            .ok()
            .and_then(|v| match &v["up_to_id"] {
                serde_json::Value::String(s) => Some(s.clone()),
                serde_json::Value::Number(n) => Some(n.to_string()),
                _ => None,
            })
    } else {
        String::from_utf8_lossy(&body)
            .split('&')
            .find_map(|kv| kv.strip_prefix("up_to_id=").map(str::to_owned))
    }
    .filter(|id| !id.is_empty() && id.bytes().all(|b| b.is_ascii_digit()));
    let json = is_json || is_ajax(&headers);
    let Some(up_to) = up_to else {
        return if json {
            Json(serde_json::json!({"ok": false, "error": "up_to_id must be a notification id"})).into_response()
        } else {
            Redirect::to(&format!("/dashboard/guild/{guild_id}/notifications#feed")).into_response()
        };
    };
    let Some(user_id) = uid(&session) else {
        return bad_session();
    };
    let result = percy.mark_notifications_read(guild_id, user_id, &up_to).await;
    if json {
        return match result {
            Ok(v) => Json(serde_json::json!({"ok": true, "unread": v.get("unread")})).into_response(),
            Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
        };
    }
    Redirect::to(&format!("/dashboard/guild/{guild_id}/notifications#feed")).into_response()
}

#[derive(Deserialize)]
pub struct DecideForm {
    decision: String,
    #[serde(default)]
    note: String,
}

/// `POST /dashboard/guild/:guild_id/requests/:rid/decide`
pub async fn request_decide(
    State(app): State<AppState>,
    session: Session,
    headers: HeaderMap,
    Path((guild_id, rid)): Path<(u64, String)>,
    Form(form): Form<DecideForm>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) if is_ajax(&headers) => return r,
        Err(_) => return notice_redirect(guild_id, "You cannot decide requests on this server.", false),
    };
    let approve = form.decision == "approve";
    let valid = (approve || form.decision == "deny") && !rid.is_empty() && rid.bytes().all(|b| b.is_ascii_digit());
    // The moderator is whoever is signed in, never a form field.
    let (ok, message, request, status) = if !valid {
        (false, "That is not a valid decision.".to_string(), None, String::new())
    } else if let Some(moderator) = uid(&session) {
        let note = form.note.trim();
        let note = (!note.is_empty()).then(|| note.chars().take(300).collect::<String>());
        match percy
            .decide_action_request(guild_id, &rid, moderator, approve, note.as_deref())
            .await
        {
            Ok(d) => (d.ok, d.message, d.request, d.status),
            Err(e) => (false, percy_message(&e), None, String::new()),
        }
    } else {
        (
            false,
            "Your session has no valid Discord id; sign in again.".to_string(),
            None,
            String::new(),
        )
    };
    if is_ajax(&headers) {
        let key = if ok { "message" } else { "error" };
        return Json(serde_json::json!({"ok": ok, key: message, "status": status, "request": request})).into_response();
    }
    notice_redirect(guild_id, &message, ok)
}

fn notice_redirect(guild_id: u64, message: &str, ok: bool) -> Response {
    Redirect::to(&format!(
        "/dashboard/guild/{guild_id}/notifications?notice={}&ok={}#requests",
        encode(message),
        u8::from(ok)
    ))
    .into_response()
}

/// `PATCH /dashboard/guild/:guild_id/request-settings` — Percy replaces both fields, so both are always sent.
pub async fn request_settings_update(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let channel = body
        .get("channel_id")
        .and_then(|v| v.as_str())
        .and_then(|s| s.parse::<u64>().ok())
        .map_or(serde_json::Value::Null, serde_json::Value::from);
    let roles: Vec<u64> = body
        .get("role_ids")
        .and_then(|v| v.as_array())
        .into_iter()
        .flatten()
        .filter_map(|v| v.as_str()?.parse().ok())
        .collect();
    let body = serde_json::json!({"channel_id": channel, "role_ids": roles});
    ok_or_err(percy.update_request_settings(guild_id, &body).await.map(|_| ()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_helpers() {
        assert_eq!(short_time("2026-10-21T12:30:00.1Z"), "2026-10-21 12:30");
        assert_eq!(duration_label(3600), "1h");
        assert_eq!(duration_label(90), "90s");
        assert_eq!(duration_label(172_800), "2d");
        assert_eq!(encode("a b."), "a%20b.");
        assert_eq!(safe_link(Some(&"//evil".to_string())), None);
        assert!(iso_days_ago(0).ends_with('Z') && iso_days_ago(7).len() == 20);
    }
}
