//! JSON / action handlers for the three dashboard-facing platform domains added
//! to Percy's internal API in Percy-v2 `d751252`: outbound webhooks (event
//! subscriptions), analytics (time-series + summary), and backup / templates.
//!
//! Each handler gates on a manage-access session ([`gate_json`]) then forwards to
//! Percy's internal API (which owns validation and cache invalidation), answering
//! the `{ok}` / `{ok,error}` JSON the dashboard JS expects — or a raw passthrough
//! of Percy's report for the read/preview endpoints.

use axum::{
    extract::{Path, Query, State},
    http::{header, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use percy_client::PercyError;
use serde::Deserialize;

use crate::{
    errors::percy_message,
    mutations::{gate_json, ok_or_err},
    session::Session,
    AppState,
};

/// Query flag shared by the backup-import and template-apply endpoints. Both
/// default to a preview (`dry_run=true`) so a stray click can never write.
#[derive(Deserialize)]
pub struct DryRunQuery {
    #[serde(default = "default_true")]
    pub dry_run: bool,
}

fn default_true() -> bool {
    true
}

/// The `?range=` picker shared by the analytics JSON endpoints.
#[derive(Deserialize)]
pub struct RangeQuery {
    #[serde(default)]
    pub range: Option<String>,
    #[serde(default)]
    pub metric: Option<String>,
    #[serde(default)]
    pub granularity: Option<String>,
}

// ── Outbound webhooks ─────────────────────────────────────────────────────────

/// `POST /dashboard/guild/:guild_id/webhooks` — create a subscription. The
/// response carries the signing secret exactly once (Percy masks it afterwards),
/// so it is forwarded verbatim for the JS to reveal.
pub async fn guild_webhook_create(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.create_webhook(guild_id, &body).await {
        Ok(resp) => Json(serde_json::json!({"ok": true, "subscription": resp.subscription})).into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

/// `PATCH /dashboard/guild/:guild_id/webhooks/:sub_id` — update url/events/label/enabled.
pub async fn guild_webhook_patch(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, sub_id)): Path<(u64, i64)>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.patch_webhook(guild_id, sub_id, &body).await)
}

/// `DELETE /dashboard/guild/:guild_id/webhooks/:sub_id`
pub async fn guild_webhook_delete(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, sub_id)): Path<(u64, i64)>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.delete_webhook(guild_id, sub_id).await)
}

/// `POST /dashboard/guild/:guild_id/webhooks/:sub_id/test` — send a sample ping
/// and report the synchronous delivery result.
pub async fn guild_webhook_test(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, sub_id)): Path<(u64, i64)>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.test_webhook(guild_id, sub_id).await {
        Ok(resp) => Json(serde_json::json!({"ok": resp.ok, "result": resp.result})).into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

/// `GET /dashboard/guild/:guild_id/webhooks/:sub_id/deliveries` — the health log.
pub async fn guild_webhook_deliveries(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, sub_id)): Path<(u64, i64)>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.get_webhook_deliveries(guild_id, sub_id, 25).await {
        Ok(resp) => Json(serde_json::json!({"ok": true, "deliveries": resp.deliveries})).into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

// ── Analytics ─────────────────────────────────────────────────────────────────

/// `GET /dashboard/guild/:guild_id/analytics/series` — a zero-filled time-series
/// for one metric over a range (JSON passthrough for the client-side chart).
pub async fn guild_analytics_series(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Query(q): Query<RangeQuery>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    let metric = q.metric.as_deref().unwrap_or("commands");
    let range = q.range.as_deref().unwrap_or("30d");
    match percy
        .get_analytics_series(guild_id, metric, range, q.granularity.as_deref())
        .await
    {
        Ok(series) => Json(serde_json::json!(series)).into_response(),
        Err(e) => Json(serde_json::json!({"error": percy_message(&e)})).into_response(),
    }
}

/// `GET /dashboard/guild/:guild_id/analytics/summary` — headline numbers with deltas.
pub async fn guild_analytics_summary(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Query(q): Query<RangeQuery>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    let range = q.range.as_deref().unwrap_or("30d");
    match percy.get_analytics_summary(guild_id, range).await {
        Ok(summary) => Json(serde_json::json!(summary)).into_response(),
        Err(e) => Json(serde_json::json!({"error": percy_message(&e)})).into_response(),
    }
}

// ── Backup & templates ────────────────────────────────────────────────────────

/// `GET /dashboard/guild/:guild_id/backup/export.json` — download the portable
/// config snapshot as an attachment.
pub async fn guild_backup_export(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.export_backup(guild_id).await {
        Ok(blob) => {
            let body = serde_json::to_string_pretty(&blob).unwrap_or_else(|_| "{}".to_string());
            (
                StatusCode::OK,
                [
                    (header::CONTENT_TYPE, "application/json".to_string()),
                    (
                        header::CONTENT_DISPOSITION,
                        format!("attachment; filename=\"percy-backup-{guild_id}.json\""),
                    ),
                ],
                body,
            )
                .into_response()
        }
        Err(e) => (StatusCode::BAD_GATEWAY, percy_message(&e)).into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/backup/import` — apply a backup blob. The JS
/// posts `{data, sections?}`; `?dry_run=` (default true) previews. Percy's
/// `{ok, dry_run, plan|applied}` report is forwarded verbatim.
pub async fn guild_backup_import(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Query(q): Query<DryRunQuery>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.import_backup(guild_id, &body, q.dry_run).await {
        Ok(report) => Json(report).into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/templates` — publish a template from the
/// current config (or a supplied blob). Surfaces Percy's slug-conflict / validation text.
pub async fn guild_template_publish(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.publish_template(guild_id, &body).await)
}

/// `POST /dashboard/guild/:guild_id/templates/:slug/apply` — apply a template
/// (public or own). `?dry_run=` (default true) previews.
pub async fn guild_template_apply(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, slug)): Path<(u64, String)>,
    Query(q): Query<DryRunQuery>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.apply_template(guild_id, &slug, q.dry_run).await {
        Ok(report) => Json(report).into_response(),
        Err(PercyError::NotFound) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"ok": false, "error": "template not found"})),
        )
            .into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

/// `DELETE /dashboard/guild/:guild_id/templates/:slug` — delete a template this guild authored.
pub async fn guild_template_delete(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, slug)): Path<(u64, String)>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.delete_template(guild_id, &slug).await)
}
