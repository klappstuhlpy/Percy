//! Moderation audit log + cases, bulk actions, member activity/avatars, CSV
//! exports, and the per-guild custom-bot profile — ported 1:1 from the monolith.

use std::collections::HashMap;

use axum::{
    extract::{Path, Query, State},
    response::{IntoResponse, Redirect, Response},
    Json,
};
use percy_client::{CasesResponse, LeaderboardResponse, PercyError};
use serde::Deserialize;

use crate::{
    errors::percy_message, guilds::user_can_manage, mutations::gate_json, session::Session,
    templates::AuditLogTemplate, view::Viewer, AppState,
};

#[derive(Deserialize)]
pub struct CasesQuery {
    #[serde(default)]
    action: String,
    #[serde(default)]
    moderator_id: String,
    #[serde(default)]
    after: String,
    #[serde(default)]
    before: String,
    #[serde(default = "default_cases_limit")]
    limit: u32,
    #[serde(default)]
    offset: u32,
}

fn default_cases_limit() -> u32 {
    50
}

/// Turns an empty filter string into `None`, else `Some(&str)`.
fn opt(s: &str) -> Option<&str> {
    if s.is_empty() {
        None
    } else {
        Some(s)
    }
}

/// `GET /dashboard/guild/:guild_id/audit-log` — the moderation history page.
pub async fn guild_audit_log(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Query(q): Query<CasesQuery>,
) -> Response {
    let Some(percy) = &app.percy else {
        return Redirect::to("/").into_response();
    };
    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return Redirect::to("/dashboard").into_response();
    }

    let (guild, cases) = tokio::join!(
        percy.get_guild(guild_id),
        percy.get_cases(
            guild_id,
            q.limit,
            q.offset,
            opt(&q.action),
            opt(&q.moderator_id),
            None,
            opt(&q.after),
            opt(&q.before),
        ),
    );

    let guild = match guild {
        Ok(g) => g,
        Err(_) => return Redirect::to("/dashboard").into_response(),
    };
    let cases = cases.unwrap_or(CasesResponse {
        cases: Vec::new(),
        total: 0,
    });

    AuditLogTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "audit-log",
        page_title: "Audit Log",
        cases,
        filter_action: q.action,
        filter_moderator: q.moderator_id,
        filter_after: q.after,
        filter_before: q.before,
    }
    .into_response()
}

/// `GET /dashboard/guild/:guild_id/audit-log.json` — filtered cases feed.
pub async fn guild_audit_log_json(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Query(q): Query<CasesQuery>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy
        .get_cases(
            guild_id,
            q.limit,
            q.offset,
            opt(&q.action),
            opt(&q.moderator_id),
            None,
            opt(&q.after),
            opt(&q.before),
        )
        .await
    {
        Ok(data) => Json(serde_json::json!(data)).into_response(),
        Err(e) => Json(serde_json::json!({"error": percy_message(&e)})).into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/members/bulk-action`
pub async fn guild_bulk_action(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(mut body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    // The acting moderator is the authenticated user.
    body["moderator_id"] = serde_json::Value::String(session.discord_user_id.clone());
    match percy.bulk_member_action(guild_id, &body).await {
        Ok(resp) => Json(serde_json::json!(resp)).into_response(),
        Err(e) => Json(serde_json::json!({"error": percy_message(&e)})).into_response(),
    }
}

/// `GET /dashboard/guild/:guild_id/members/:user_id/activity`
pub async fn guild_member_activity(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, user_id)): Path<(u64, String)>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.get_member_activity(guild_id, &user_id, 365).await {
        Ok(data) => Json(serde_json::json!(data)).into_response(),
        Err(e) => Json(serde_json::json!({"error": percy_message(&e)})).into_response(),
    }
}

/// `GET /dashboard/guild/:guild_id/members/:user_id/avatars`
pub async fn guild_member_avatars(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, user_id)): Path<(u64, String)>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.get_member_avatars(guild_id, &user_id, 20).await {
        Ok(data) => Json(serde_json::json!(data)).into_response(),
        Err(_) => Json(serde_json::json!({"avatars": [], "total": 0})).into_response(),
    }
}

/// `GET /dashboard/guild/:guild_id/leveling/export.csv`
pub async fn guild_export_leaderboard(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
) -> Response {
    let Some(percy) = &app.percy else {
        return Redirect::to("/").into_response();
    };
    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return Redirect::to("/dashboard").into_response();
    }

    let leaderboard = percy
        .get_leveling_leaderboard(guild_id, 500)
        .await
        .unwrap_or(LeaderboardResponse {
            entries: Vec::new(),
            total: 0,
        });

    let mut csv = String::from("Rank,Username,Level,XP,Total XP\n");
    for (i, entry) in leaderboard.entries.iter().enumerate() {
        csv.push_str(&format!(
            "{},{},{},{},{}\n",
            i + 1,
            entry.username.replace(',', " "),
            entry.level,
            entry.xp,
            entry.total_xp,
        ));
    }

    (
        [
            ("Content-Type", "text/csv; charset=utf-8"),
            ("Content-Disposition", "attachment; filename=\"leaderboard.csv\""),
        ],
        csv,
    )
        .into_response()
}

/// `GET /dashboard/guild/:guild_id/audit-log/export.csv`
pub async fn guild_export_cases(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let Some(percy) = &app.percy else {
        return Redirect::to("/").into_response();
    };
    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return Redirect::to("/dashboard").into_response();
    }

    let cases = percy
        .get_cases(guild_id, 100, 0, None, None, None, None, None)
        .await
        .unwrap_or(CasesResponse {
            cases: Vec::new(),
            total: 0,
        });

    let mut csv = String::from("Case #,Action,Target,Moderator,Reason,Date\n");
    for case in &cases.cases {
        csv.push_str(&format!(
            "{},{},{},{},{},{}\n",
            case.case_index,
            case.action,
            case.target_name.replace(',', " "),
            case.moderator_name.as_deref().unwrap_or("Unknown").replace(',', " "),
            case.reason.as_deref().unwrap_or("").replace([',', '\n'], " "),
            case.created_at.as_deref().unwrap_or(""),
        ));
    }

    (
        [
            ("Content-Type", "text/csv; charset=utf-8"),
            ("Content-Disposition", "attachment; filename=\"moderation_history.csv\""),
        ],
        csv,
    )
        .into_response()
}

#[derive(Deserialize)]
pub struct CaseCreateBody {
    action: String,
    target_id: String,
    reason: Option<String>,
}

/// `POST /dashboard/guild/:guild_id/cases`
pub async fn guild_case_create(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<CaseCreateBody>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy
        .create_case(
            guild_id,
            &body.action,
            &body.target_id,
            Some(&session.discord_user_id),
            body.reason.as_deref(),
        )
        .await
    {
        Ok(resp) => Json(serde_json::json!(resp)).into_response(),
        Err(e) => Json(serde_json::json!({"error": percy_message(&e)})).into_response(),
    }
}

#[derive(Deserialize)]
pub struct CaseUpdateBody {
    reason: String,
}

/// `PATCH /dashboard/guild/:guild_id/cases/:case_index`
pub async fn guild_case_update(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, case_index)): Path<(u64, u64)>,
    Json(body): Json<CaseUpdateBody>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.update_case_reason(guild_id, case_index, &body.reason).await {
        Ok(resp) => Json(serde_json::json!(resp)).into_response(),
        Err(PercyError::NotFound) => Json(serde_json::json!({"error": "case not found"})).into_response(),
        Err(e) => Json(serde_json::json!({"error": percy_message(&e)})).into_response(),
    }
}

/// `DELETE /dashboard/guild/:guild_id/cases/:case_index`
pub async fn guild_case_delete(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, case_index)): Path<(u64, u64)>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.delete_case(guild_id, case_index).await {
        Ok(()) => Json(serde_json::json!({"ok": true})).into_response(),
        Err(PercyError::NotFound) => Json(serde_json::json!({"error": "case not found"})).into_response(),
        Err(e) => Json(serde_json::json!({"error": percy_message(&e)})).into_response(),
    }
}

/// `GET /dashboard/guild/:guild_id/cases/recent?since=…`
pub async fn guild_cases_recent(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Query(params): Query<HashMap<String, String>>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    let since = params.get("since").map(|s| s.as_str()).unwrap_or("");
    if since.is_empty() {
        return Json(serde_json::json!({"error": "since parameter required"})).into_response();
    }
    match percy.get_recent_cases(guild_id, since).await {
        Ok(data) => Json(serde_json::json!(data)).into_response(),
        Err(e) => Json(serde_json::json!({"error": percy_message(&e)})).into_response(),
    }
}

/// `PATCH /dashboard/guild/:guild_id/custom-bot`
pub async fn guild_custom_bot_update(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.patch_custom_bot(guild_id, &body).await {
        Ok(()) => Json(serde_json::json!({"ok": true, "message": "Bot profile updated"})).into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/custom-bot/reset`
pub async fn guild_custom_bot_reset(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.reset_custom_bot(guild_id).await {
        Ok(()) => Json(serde_json::json!({"ok": true, "message": "Bot profile reset to defaults"})).into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}
