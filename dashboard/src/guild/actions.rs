//! Member, poll, giveaway, tag, leveling and music mutations / JSON endpoints —
//! ported 1:1 from the monolith. Each gates on a manage-access session then
//! forwards to Percy's internal API. Poll creation is a single multipart request
//! (poll fields plus an optional inline banner file); Percy routes the image into
//! the guild's shared gallery. Tags import scans via the external klappstuhl.me
//! scanner before forwarding parsed rows to Percy.

use std::collections::HashMap;

use axum::{
    body::Body,
    extract::{Multipart, Path, Query, State},
    http::{header, HeaderMap, StatusCode},
    response::{IntoResponse, Redirect, Response},
    Json,
};
use percy_client::PercyError;
use serde::Deserialize;

use crate::{
    errors::percy_message,
    forms::json_or_redirect,
    guilds::{user_can_manage, user_is_member},
    mutations::{gate_json, ok_or_err},
    scan,
    session::Session,
    AppState,
};

fn default_limit() -> u32 {
    100
}

// ── Members ─────────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct MembersQuery {
    #[serde(default = "default_limit")]
    limit: u32,
    #[serde(default)]
    after: u64,
    #[serde(default)]
    search: Option<String>,
}

/// `GET /dashboard/guild/:guild_id/members.json` — live search/pagination feed.
pub async fn guild_members_json(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Query(query): Query<MembersQuery>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy
        .get_guild_members(guild_id, query.limit.min(100), query.after, query.search.as_deref())
        .await
    {
        Ok(r) => Json(serde_json::json!({"members": r.members, "total": r.total})).into_response(),
        Err(_) => Json(serde_json::json!({"members": [], "total": 0})).into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/members/:user_id/action` — kick, ban, unban,
/// softban, warn, mute/unmute, timeout/untimeout, purge.
///
/// The body is forwarded to Percy as-is (minus the moderator id, which is overwritten):
/// each action carries its own parameters (`duration`, `delete_message_days`, `limit`, `channel_id`, `nickname`) and
/// Percy owns the whitelist, so the dashboard does not re-validate them.
pub async fn guild_member_action(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, user_id)): Path<(u64, String)>,
    Json(mut body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    // The acting moderator is the authenticated user, never a client-supplied id.
    let Some(obj) = body.as_object_mut() else {
        return Json(serde_json::json!({"ok": false, "error": "expected a JSON object"})).into_response();
    };
    obj.insert(
        "moderator_id".into(),
        serde_json::Value::String(session.discord_user_id.clone()),
    );

    match percy.member_action_json(guild_id, &user_id, &body).await {
        Ok(data) => Json(data).into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

#[derive(Deserialize)]
pub struct MemberMessagesQuery {
    #[serde(default = "default_message_limit")]
    limit: u32,
}

fn default_message_limit() -> u32 {
    25
}

/// `GET /dashboard/guild/:guild_id/members/:user_id/messages` — the member message viewer.
pub async fn guild_member_messages(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, user_id)): Path<(u64, String)>,
    Query(query): Query<MemberMessagesQuery>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy
        .get_member_messages(guild_id, &user_id, query.limit.min(100))
        .await
    {
        Ok(data) => Json(serde_json::json!(data)).into_response(),
        Err(e) => Json(serde_json::json!({"error": percy_message(&e)})).into_response(),
    }
}

#[derive(Deserialize)]
pub struct MemberRolesBody {
    #[serde(default)]
    add: Vec<String>,
    #[serde(default)]
    remove: Vec<String>,
}

/// `PATCH /dashboard/guild/:guild_id/members/:user_id/roles` — sent as the signed-in user, so
/// Percy can refuse roles at or above their own top role.
pub async fn guild_member_roles(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, user_id)): Path<(u64, String)>,
    Json(body): Json<MemberRolesBody>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(
        percy
            .member_roles(guild_id, &user_id, &body.add, &body.remove, &session.discord_user_id)
            .await,
    )
}

// ── Polls ───────────────────────────────────────────────────────────────────

/// `POST /dashboard/guild/:guild_id/polls/:poll_id`
pub async fn guild_poll_edit(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, poll_id)): Path<(u64, i64)>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.patch_poll(guild_id, poll_id, &body).await)
}

/// `POST /dashboard/guild/:guild_id/polls/:poll_id/end`
pub async fn guild_poll_end(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, poll_id)): Path<(u64, i64)>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.end_poll(guild_id, poll_id).await)
}

/// `POST /dashboard/guild/:guild_id/polls` — create a poll from a single
/// multipart submission: text fields plus an optional inline banner file. When a
/// file is present it is malware-scanned before being forwarded to Percy, which
/// routes it into the guild's shared image gallery. The frontend JS
/// (`polls.js`) sends the file under the `file` field and each poll option as a
/// repeated `options` field.
pub async fn guild_poll_create(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    mut multipart: Multipart,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };

    // Collect text fields and the optional banner file from the multipart body.
    let mut question = String::new();
    let mut description = String::new();
    let mut channel_id = String::new();
    let mut thread_question = String::new();
    let mut color = String::new();
    let mut image_url = String::new();
    let mut hide_results = false;
    let mut role_id = String::new();
    let mut duration_seconds: i64 = 0;
    let mut options: Vec<String> = Vec::new();
    let mut file_data: Option<Vec<u8>> = None;

    while let Ok(Some(field)) = multipart.next_field().await {
        let name = field.name().unwrap_or("").to_string();
        if name == "file" {
            if let Ok(data) = field.bytes().await {
                if !data.is_empty() {
                    file_data = Some(data.to_vec());
                }
            }
            continue;
        }
        let value = field.text().await.unwrap_or_default();
        match name.as_str() {
            "question" => question = value,
            "description" => description = value,
            "channel_id" => channel_id = value,
            "thread_question" => thread_question = value,
            "color" => color = value,
            "image_url" => image_url = value,
            "hide_results" => hide_results = value == "true",
            "role_id" => role_id = value.trim().to_string(),
            "duration_seconds" => duration_seconds = value.trim().parse().unwrap_or(0),
            "options" => {
                let v = value.trim().to_string();
                if !v.is_empty() {
                    options.push(v);
                }
            }
            _ => {}
        }
    }

    // Scan the banner first (degrades to allow when the scanner is unavailable).
    if let Some(data) = &file_data {
        if let Some(token) = app.config.percy_scan_token() {
            if let Ok(report) = scan::scan_file(data, &token, &app.scan_endpoint).await {
                if report.verdict == "infected" {
                    return Json(serde_json::json!({"ok": false, "error": "file rejected: malware detected"}))
                        .into_response();
                }
            }
        }
    }

    let fields = serde_json::json!({
        "question": question,
        "options": options,
        "duration_seconds": duration_seconds,
        "channel_id": channel_id,
        "description": description,
        "color": color,
        "image_url": image_url,
        "thread_question": thread_question,
        "hide_results": hide_results,
        "role_id": role_id,
    });

    let mime = file_data.as_deref().map(detect_mime);
    let image = match (&file_data, &mime) {
        (Some(data), Some(m)) => Some((data.as_slice(), m.as_str())),
        _ => None,
    };

    match percy.create_poll(guild_id, &fields, image).await {
        Ok(data) => Json(serde_json::json!({"ok": true, "id": data.get("id")})).into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

// ── Giveaways ───────────────────────────────────────────────────────────────

/// `POST /dashboard/guild/:guild_id/giveaways`
pub async fn guild_giveaway_create(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.create_giveaway(guild_id, &body).await {
        Ok(data) => Json(serde_json::json!({"ok": true, "id": data.get("id")})).into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/giveaways/:giveaway_id/end`
pub async fn guild_giveaway_end(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, giveaway_id)): Path<(u64, i64)>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.end_giveaway(guild_id, giveaway_id).await)
}

/// `DELETE /dashboard/guild/:guild_id/giveaways/:giveaway_id`
pub async fn guild_giveaway_delete(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, giveaway_id)): Path<(u64, i64)>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.delete_giveaway(guild_id, giveaway_id).await)
}

// ── Tags ────────────────────────────────────────────────────────────────────

/// `GET /dashboard/guild/:guild_id/tags/:tag_id`
pub async fn guild_tag_detail(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, tag_id)): Path<(u64, i64)>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.get_tag_detail(guild_id, tag_id).await {
        Ok(tag) => Json(serde_json::json!(tag)).into_response(),
        Err(PercyError::NotFound) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "tag not found"})),
        )
            .into_response(),
        Err(e) => Json(serde_json::json!({"error": percy_message(&e)})).into_response(),
    }
}

/// `DELETE /dashboard/guild/:guild_id/tags/:tag_id`
pub async fn guild_tag_delete(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, tag_id)): Path<(u64, i64)>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.delete_tag(guild_id, tag_id).await)
}

/// `GET /dashboard/guild/:guild_id/tags/export.csv` — download tags as CSV,
/// optionally filtered to `?names=a,b,c`.
pub async fn guild_tags_export(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Query(params): Query<HashMap<String, String>>,
) -> Response {
    let Some(percy) = &app.percy else {
        return Redirect::to("/dashboard").into_response();
    };
    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return Redirect::to("/dashboard").into_response();
    }

    let export = match percy.export_tags(guild_id).await {
        Ok(e) => e,
        Err(_) => return (StatusCode::BAD_GATEWAY, "Could not fetch tags").into_response(),
    };

    let selected: Option<std::collections::HashSet<String>> = params.get("names").map(|s| {
        s.split(',')
            .map(|n| n.trim().to_lowercase())
            .filter(|n| !n.is_empty())
            .collect()
    });

    let mut csv = String::from("name,content\n");
    for row in &export.tags {
        if let Some(ref set) = selected {
            if !set.contains(&row.name.to_lowercase()) {
                continue;
            }
        }
        csv.push_str(&csv_escape_field(&row.name));
        csv.push(',');
        csv.push_str(&csv_escape_field(&row.content));
        csv.push('\n');
    }

    (
        StatusCode::OK,
        [
            (header::CONTENT_TYPE, "text/csv; charset=utf-8".to_string()),
            (
                header::CONTENT_DISPOSITION,
                format!("attachment; filename=\"{guild_id}_tags.csv\""),
            ),
        ],
        csv,
    )
        .into_response()
}

fn csv_escape_field(value: &str) -> String {
    if value.contains([',', '"', '\n', '\r']) {
        format!("\"{}\"", value.replace('"', "\"\""))
    } else {
        value.to_string()
    }
}

// ── Leveling ────────────────────────────────────────────────────────────────

/// `PATCH /dashboard/guild/:guild_id/leveling/users/:user_id`
pub async fn guild_leveling_update(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, user_id)): Path<(u64, String)>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.patch_leveling_user(guild_id, &user_id, &body).await)
}

/// `POST /dashboard/guild/:guild_id/leveling/config`
pub async fn guild_leveling_config_update(
    State(app): State<AppState>,
    session: Session,
    headers: HeaderMap,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let Some(percy) = &app.percy else {
        return Redirect::to("/").into_response();
    };
    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return json_or_redirect(&headers, false, "Access denied.", "/dashboard");
    }
    let redirect_url = format!("/dashboard/guild/{guild_id}/leveling");
    match percy.patch_leveling_config(guild_id, &body).await {
        Ok(()) => json_or_redirect(&headers, true, "Configuration saved.", &redirect_url),
        Err(e) => json_or_redirect(&headers, false, &percy_message(&e), &redirect_url),
    }
}

/// `POST /dashboard/guild/:guild_id/leveling/test-message` — post the level-up message once,
/// for the signed-in member, so an admin sees exactly what members will get.
pub async fn guild_leveling_test_message(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
) -> Response {
    let Some(percy) = &app.percy else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({"ok": false, "error": "Access denied."})),
        )
            .into_response();
    }
    match percy.send_level_up_test(guild_id, &session.discord_user_id).await {
        Ok(result) => Json(serde_json::json!({"ok": true, "message": result.message})).into_response(),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"ok": false, "error": percy_message(&e)})),
        )
            .into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/setup/logging` — the Moderation tab's one-step logging setup.
/// Body: `{ "channel_id": "<id>" | null, "logs": ["audit", "mod", ...] }`.
pub async fn guild_setup_logging(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let Some(percy) = &app.percy else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({"ok": false, "error": "Access denied."})),
        )
            .into_response();
    }
    let channel_id = body["channel_id"].as_str().and_then(|s| s.parse::<u64>().ok());
    let logs: Vec<String> = body["logs"]
        .as_array()
        .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_owned)).collect())
        .unwrap_or_default();
    match percy.setup_logging(guild_id, channel_id, &logs).await {
        Ok(result) => Json(serde_json::json!({"ok": true, "message": result.message})).into_response(),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"ok": false, "error": percy_message(&e)})),
        )
            .into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/setup/sentinel` — the Sentinel tab's one-step setup.
/// Body: `{ "channel_id": "<id>" | null, "role_id": "<id>" | null, "start": bool }`; null means Percy creates it.
pub async fn guild_setup_sentinel(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    let id = |key: &str| body[key].as_str().and_then(|s| s.parse::<u64>().ok());
    let start = body["start"].as_bool().unwrap_or(false);
    match percy
        .setup_sentinel(guild_id, id("channel_id"), id("role_id"), start)
        .await
    {
        Ok(result) => Json(serde_json::json!({"ok": true, "message": result.message})).into_response(),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"ok": false, "error": percy_message(&e)})),
        )
            .into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/test-message` — a "Send test" button beside a posting feature's channel.
/// Body: `{ "feature": "audit_log" | "alerts" | "mod_log" | "message_log" | "voice_log" | "polls" }`.
pub async fn guild_test_message(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy
        .send_test_message(guild_id, body["feature"].as_str().unwrap_or_default())
        .await
    {
        Ok(result) => Json(serde_json::json!({"ok": true, "message": result.message})).into_response(),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"ok": false, "error": percy_message(&e)})),
        )
            .into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/setup/leveling` — the Leveling tab's one-step setup.
/// Body: `{ "level_up_channel": "0" | "1" | "2" | "<channel id>", "preset_roles": bool }`.
pub async fn guild_setup_leveling(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    let target = &body["level_up_channel"];
    let channel = target
        .as_i64()
        .or_else(|| target.as_str().and_then(|s| s.parse::<i64>().ok()))
        .unwrap_or(1);
    let preset = body["preset_roles"].as_bool().unwrap_or(false);
    match percy.setup_leveling(guild_id, channel, preset).await {
        Ok(result) => Json(serde_json::json!({"ok": true, "message": result.message})).into_response(),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"ok": false, "error": percy_message(&e)})),
        )
            .into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/leveling/roles`
pub async fn guild_leveling_roles(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.post_leveling_roles(guild_id, &body).await)
}

/// `POST /dashboard/guild/:guild_id/leveling/roles/preset`
pub async fn guild_leveling_roles_preset(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.create_leveling_role_preset(guild_id).await)
}

/// `POST /dashboard/guild/:guild_id/leveling/multipliers`
pub async fn guild_leveling_multipliers(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.post_leveling_multipliers(guild_id, &body).await)
}

/// `POST /dashboard/guild/:guild_id/leveling/blacklist`
pub async fn guild_leveling_blacklist(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.post_leveling_blacklist(guild_id, &body).await)
}

// ── Live events ─────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct EventsQuery {
    #[serde(default)]
    topics: String,
}

/// Whether a `topics` list names only topics any guild member may watch (the player). An empty
/// list means "everything" to Percy, so it needs the manage gate like the pages it serves.
fn member_visible_topics(topics: &str) -> bool {
    !topics.trim().is_empty() && topics.split(',').all(|t| t.trim() == "music")
}

/// `GET /dashboard/guild/:guild_id/events?topics=` — Percy's server-sent event stream, relayed
/// byte for byte. Gated like the data the topics carry: `music` alone is the overview player's
/// (membership); anything else (cases, stats, ...) needs Manage Server. A GET, so the change
/// log never records it.
pub async fn guild_events(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Query(q): Query<EventsQuery>,
) -> Response {
    let Some(percy) = &app.percy else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    let discord_id = &session.discord_user_id;
    let allowed = if member_visible_topics(&q.topics) {
        user_is_member(&app, discord_id, guild_id).await
    } else {
        user_can_manage(&app, discord_id, guild_id).await
    };
    if !allowed {
        return StatusCode::FORBIDDEN.into_response();
    }
    match percy.events_stream(guild_id, &q.topics).await {
        Ok(upstream) => Response::builder()
            .header(header::CONTENT_TYPE, "text/event-stream")
            .header(header::CACHE_CONTROL, "no-cache")
            .header("x-accel-buffering", "no")
            .body(Body::from_stream(upstream.bytes_stream()))
            .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response()),
        Err(PercyError::NotFound) => StatusCode::NOT_FOUND.into_response(),
        Err(_) => StatusCode::BAD_GATEWAY.into_response(),
    }
}

// ── Music ───────────────────────────────────────────────────────────────────

/// `GET /dashboard/guild/:guild_id/music/status` — live player poll.
pub async fn guild_music_status(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.get_music(guild_id).await {
        Ok(music) => Json(serde_json::json!({
            "ok": true,
            "active": music.active,
            "channel": music.channel,
            "channel_name": music.channel_name,
            "now_playing": music.now_playing,
            "queue": music.queue,
            "history": music.history,
            // Dashboard admins (Manage Server) can always drive the player.
            "can_control": true,
            "equalizer": music.equalizer,
            "filters": music.filters,
            "setup": music.setup,
            "always_on": music.always_on,
        }))
        .into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/music/control` — drive the live player.
pub async fn guild_music_control(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(mut body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    // Inject the authenticated identity; never trust a client-supplied user_id.
    if let Some(obj) = body.as_object_mut() {
        obj.insert(
            "user_id".into(),
            serde_json::Value::String(session.discord_user_id.clone()),
        );
    }
    ok_or_err(percy.music_control(guild_id, &body).await)
}

/// `GET /dashboard/guild/:guild_id/music/lyrics` — synced lyrics.
pub async fn guild_music_lyrics(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.get_music_lyrics(guild_id).await {
        Ok(l) => Json(serde_json::json!({
            "ok": true,
            "has_synced": l.has_synced,
            "title": l.title,
            "source": l.source,
            "lines": l.lines,
            "plain": l.plain,
        }))
        .into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/music/247`
pub async fn guild_music_247(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.post_music_247(guild_id, &body).await)
}

/// `PATCH /dashboard/guild/:guild_id/music/dj-mode`
pub async fn guild_music_dj_mode(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.patch_music_dj_mode(guild_id, &body).await)
}

/// `POST /dashboard/guild/:guild_id/music/setup`
pub async fn guild_music_setup(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.post_music_setup(guild_id, &body).await {
        Ok(resp) => Json(serde_json::json!({
            "ok": true,
            "channel_id": resp.channel_id,
            "channel_name": resp.channel_name,
        }))
        .into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/music/reset`
pub async fn guild_music_reset(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.post_music_reset(guild_id).await)
}

// ── Guild image gallery ─────────────────────────────────────────────────────

/// `POST /dashboard/guild/:guild_id/gallery/upload` — accept one or more image
/// files from the owner, scan each via the external klappstuhl.me API, then
/// forward to Percy's internal API which stores them in the guild's shared
/// gallery. Returns `{ok, total, errors, ...}` for the page JS.
pub async fn guild_gallery_upload(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    mut multipart: Multipart,
) -> Response {
    let Some(percy) = &app.percy else {
        return Json(serde_json::json!({"ok": false, "error": "not configured"})).into_response();
    };
    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return Json(serde_json::json!({"ok": false, "error": "access denied"})).into_response();
    }

    // Collect every uploaded file as (filename, bytes, mime).
    let mut files: Vec<(String, Vec<u8>, String)> = Vec::new();
    while let Ok(Some(field)) = multipart.next_field().await {
        let name = field
            .file_name()
            .map(str::to_string)
            .unwrap_or_else(|| "image.png".to_string());
        if let Ok(data) = field.bytes().await {
            if !data.is_empty() {
                let bytes = data.to_vec();
                let mime = detect_mime(&bytes);
                files.push((name, bytes, mime));
            }
        }
    }
    if files.is_empty() {
        return Json(serde_json::json!({"ok": false, "error": "No image files were provided."})).into_response();
    }

    // Scan each file first (degrades to allow when the scanner is unavailable).
    if let Some(token) = app.config.percy_scan_token() {
        for (_name, bytes, _mime) in &files {
            if let Ok(report) = scan::scan_file(bytes, &token, &app.scan_endpoint).await {
                if report.verdict == "infected" {
                    return Json(serde_json::json!({"ok": false, "error": "a file was rejected: malware detected"}))
                        .into_response();
                }
            }
        }
    }

    match percy.upload_guild_gallery(guild_id, files).await {
        Ok(result) => Json(serde_json::json!({
            "ok": result.errors == 0 && result.infected == 0,
            "total": result.total,
            "errors": result.errors,
            "skipped": result.skipped,
            "infected": result.infected,
        }))
        .into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/gallery/:image_id/delete` — remove an image
/// from the guild's shared gallery.
pub async fn guild_gallery_delete(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, image_id)): Path<(u64, String)>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.delete_guild_gallery(guild_id, &image_id).await)
}

// ── Tags import ─────────────────────────────────────────────────────────────

/// `POST /dashboard/guild/:guild_id/tags/import` — accept a CSV upload from the
/// dashboard user, scan it via the external klappstuhl.me API, parse into tag rows,
/// and forward to Percy's `import_tags` endpoint.
pub async fn guild_tags_import(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    mut multipart: Multipart,
) -> Response {
    let Some(percy) = &app.percy else {
        return Json(serde_json::json!({"ok": false, "error": "not configured"})).into_response();
    };
    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return Json(serde_json::json!({"ok": false, "error": "access denied"})).into_response();
    }

    // Extract the CSV text from the first field with data.
    let mut csv_text: Option<String> = None;
    while let Ok(Some(field)) = multipart.next_field().await {
        if let Ok(data) = field.bytes().await {
            if data.is_empty() {
                continue;
            }
            // Reject non-UTF-8 uploads outright (same as monolith).
            match String::from_utf8(data.to_vec()) {
                Ok(text) => {
                    csv_text = Some(text);
                    break;
                }
                Err(_) => {
                    return Json(serde_json::json!({"ok": false, "error": "file is not valid UTF-8 text"}))
                        .into_response()
                }
            }
        }
    }
    let Some(csv_text) = csv_text else {
        return Json(serde_json::json!({"ok": false, "error": "no file uploaded"})).into_response();
    };

    // Cap upload size (1 MiB is plenty for a tag CSV).
    if csv_text.len() > 1024 * 1024 {
        return Json(serde_json::json!({"ok": false, "error": "file too large (max 1 MB)"})).into_response();
    }

    // Scan via external endpoint. Degrades to allow on failure.
    if let Some(token) = app.config.percy_scan_token() {
        match scan::scan_file(csv_text.as_bytes(), &token, &app.scan_endpoint).await {
            Ok(report) if report.verdict == "infected" => {
                return Json(serde_json::json!({
                    "ok": false,
                    "error": "file rejected: malware detected"
                }))
                .into_response();
            }
            Ok(_) | Err(_) => {} // clean or scanner unavailable → proceed
        }
    }

    // Parse CSV into TagExportRow items (RFC 4180 subset — no quoted-newline support needed).
    let rows: Vec<percy_client::TagExportRow> = csv_text
        .lines()
        .skip(1) // skip header
        .filter_map(|line| {
            let parts: Vec<&str> = line.splitn(2, ',').collect();
            if parts.len() < 2 {
                return None;
            }
            let name = parts[0].trim().to_string();
            let content = parts[1].trim().to_string();
            if name.is_empty() || content.is_empty() {
                return None;
            }
            Some(percy_client::TagExportRow { name, content })
        })
        .collect();

    if rows.is_empty() {
        return Json(serde_json::json!({"ok": false, "error": "no tags found in file"})).into_response();
    }

    // Forward to Percy's import_tags endpoint.
    match percy.import_tags(guild_id, &rows, &session.discord_user_id).await {
        Ok(result) => Json(serde_json::json!({
            "ok": true,
            "created": result.created,
            "skipped": result.skipped,
            "failed": result.failed,
        }))
        .into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

/// Detect a safe MIME type from the first bytes of a file.
fn detect_mime(data: &[u8]) -> String {
    if data.starts_with(b"\x89PNG\r\n\x1a\n") {
        "image/png".to_string()
    } else if data.starts_with(b"\xff\xd8\xff") {
        "image/jpeg".to_string()
    } else if data.starts_with(b"GIF87a") || data.starts_with(b"GIF89a") {
        "image/gif".to_string()
    } else if data.starts_with(b"RIFF") && &data[8..12] == b"WEBP" {
        "image/webp".to_string()
    } else {
        "image/png".to_string()
    } // default safe fallback
}

#[cfg(test)]
mod tests {
    use super::member_visible_topics;

    #[test]
    fn only_music_is_member_visible() {
        assert!(member_visible_topics("music"));
        assert!(member_visible_topics(" music , music"));
        assert!(
            !member_visible_topics(""),
            "empty means every topic, so it needs manage"
        );
        assert!(!member_visible_topics("music,case"));
        assert!(!member_visible_topics("stats"));
    }
}
