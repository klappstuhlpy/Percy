//! Member, poll, giveaway, tag, leveling and music mutations / JSON endpoints —
//! ported 1:1 from the monolith. Each gates on a manage-access session then
//! forwards to Percy's internal API. Two monolith endpoints are intentionally not
//! ported because they depend on klappstuhl.me-only infrastructure the standalone
//! dashboard doesn't have: `guild_poll_image_upload` (the image gallery/host) and
//! `guild_tags_import` (the ClamAV upload scanner).

use std::collections::HashMap;

use axum::{
    extract::{Path, Query, State},
    http::{header, HeaderMap, StatusCode},
    response::{IntoResponse, Redirect, Response},
    Json,
};
use percy_client::PercyError;
use serde::Deserialize;

use crate::{
    forms::json_or_redirect,
    guilds::user_can_manage,
    mutations::{gate_json, ok_or_err},
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

#[derive(Deserialize)]
pub struct MemberActionBody {
    action: String,
    reason: Option<String>,
}

/// `POST /dashboard/guild/:guild_id/members/:user_id/action` — kick/ban/unban.
pub async fn guild_member_action(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, user_id)): Path<(u64, String)>,
    Json(body): Json<MemberActionBody>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    // The acting moderator is the authenticated user, never a client-supplied id.
    let result = percy
        .member_action(
            guild_id,
            &user_id,
            &body.action,
            body.reason.as_deref(),
            Some(&session.discord_user_id),
        )
        .await;
    ok_or_err(result)
}

#[derive(Deserialize)]
pub struct MemberRolesBody {
    #[serde(default)]
    add: Vec<String>,
    #[serde(default)]
    remove: Vec<String>,
}

/// `PATCH /dashboard/guild/:guild_id/members/:user_id/roles`
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
    ok_or_err(percy.member_roles(guild_id, &user_id, &body.add, &body.remove).await)
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

/// `POST /dashboard/guild/:guild_id/polls`
pub async fn guild_poll_create(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.create_poll(guild_id, &body).await {
        Ok(data) => Json(serde_json::json!({"ok": true, "id": data.get("id")})).into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": e.to_string()})).into_response(),
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
        Err(e) => Json(serde_json::json!({"ok": false, "error": e.to_string()})).into_response(),
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
        Err(e) => Json(serde_json::json!({"error": e.to_string()})).into_response(),
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
        Err(e) => json_or_redirect(&headers, false, &format!("Failed: {e}"), &redirect_url),
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

// ── Autoresponders ──────────────────────────────────────────────────────────

/// `POST /dashboard/guild/:guild_id/autoresponders` — create an autoresponder.
pub async fn guild_autoresponders_action(
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
    let redirect_url = format!("/dashboard/guild/{guild_id}/autoresponders");
    match percy.create_autoresponder(guild_id, &body).await {
        Ok(()) => json_or_redirect(&headers, true, "Autoresponder created.", &redirect_url),
        Err(e) => json_or_redirect(&headers, false, &format!("Failed: {e}"), &redirect_url),
    }
}

// ── Music ───────────────────────────────────────────────────────────────────

/// `POST /dashboard/guild/:guild_id/music/equalizer`
pub async fn guild_music_equalizer(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.post_music_equalizer(guild_id, &body).await)
}

/// `POST /dashboard/guild/:guild_id/music/filters`
pub async fn guild_music_filters(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.post_music_filters(guild_id, &body).await)
}

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
        Err(e) => Json(serde_json::json!({"ok": false, "error": e.to_string()})).into_response(),
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
        Err(e) => Json(serde_json::json!({"ok": false, "error": e.to_string()})).into_response(),
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
        Err(e) => Json(serde_json::json!({"ok": false, "error": e.to_string()})).into_response(),
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
