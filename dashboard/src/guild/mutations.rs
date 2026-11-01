//! Mutation handlers (form/JSON POST-PATCH-DELETE), ported from the monolith's
//! `dashboard/handlers.rs`. Each checks a manage-access session, forwards the
//! payload to Percy's internal API (which invalidates its own caches), and answers
//! either JSON (AJAX) or a redirect. Access/forward logic is copied 1:1; only the
//! auth prelude (`Session` in place of `Account`/`Flasher`) is adapted.

use std::collections::HashMap;

use axum::{
    extract::{Path, State},
    http::HeaderMap,
    response::{IntoResponse, Redirect, Response},
    Form, Json,
};
use percy_client::BatchOperation;
use serde::Deserialize;

use crate::{
    errors::percy_message,
    forms::{build_patch, build_sentinel_patch, json_or_redirect, saved_with_warnings, ConfigForm, AI_FLAG_NAMES},
    guilds::user_can_manage,
    session::Session,
    AppState,
};

/// Maps a Percy `Result<(), _>` to the dashboard JS's `{ok}` / `{ok,error}` JSON.
pub(crate) fn ok_or_err(result: Result<(), percy_client::PercyError>) -> Response {
    match result {
        Ok(()) => Json(serde_json::json!({"ok": true})).into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

/// Gate for the JSON API mutations: requires Percy configured + a manage-access
/// session, returning the client or an early-return JSON error (matching the
/// monolith's `{"ok": false, "error": …}` shape the dashboard JS expects).
pub(crate) async fn gate_json<'a>(
    app: &'a AppState,
    session: &Session,
    guild_id: u64,
) -> Result<&'a percy_client::PercyClient, Response> {
    let Some(percy) = &app.percy else {
        return Err(Json(serde_json::json!({"ok": false, "error": "not configured"})).into_response());
    };
    if !user_can_manage(app, &session.discord_user_id, guild_id).await {
        return Err(Json(serde_json::json!({"ok": false, "error": "access denied"})).into_response());
    }
    Ok(percy)
}

/// `POST /dashboard/guild/:guild_id/commands/permissions`
pub async fn guild_command_permissions(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.set_command_permissions(guild_id, &body).await {
        Ok(()) => Json(serde_json::json!({"ok": true})).into_response(),
        Err(e) => Json(serde_json::json!({"error": percy_message(&e)})).into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/commands/toggle`
pub async fn guild_command_toggle(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.toggle_command(guild_id, &body).await {
        Ok(()) => Json(serde_json::json!({"ok": true})).into_response(),
        Err(e) => Json(serde_json::json!({"error": percy_message(&e)})).into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/plonks`
pub async fn guild_plonk_manage(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.manage_plonk(guild_id, &body).await {
        Ok(()) => Json(serde_json::json!({"ok": true})).into_response(),
        Err(e) => Json(serde_json::json!({"error": percy_message(&e)})).into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/lockdowns/lock`
pub async fn guild_lockdown_lock(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.lock_channels(guild_id, &body).await {
        Ok(()) => Json(serde_json::json!({"ok": true})).into_response(),
        Err(e) => Json(serde_json::json!({"error": percy_message(&e)})).into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/lockdowns/unlock`
pub async fn guild_lockdown_unlock(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.unlock_channels(guild_id, &body).await {
        Ok(()) => Json(serde_json::json!({"ok": true})).into_response(),
        Err(e) => Json(serde_json::json!({"error": percy_message(&e)})).into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/moderation/ignore`
pub async fn guild_moderation_ignore(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.manage_moderation_ignore(guild_id, &body).await {
        Ok(()) => Json(serde_json::json!({"ok": true})).into_response(),
        Err(e) => Json(serde_json::json!({"error": percy_message(&e)})).into_response(),
    }
}

/// `PATCH /dashboard/guild/:guild_id/audit-log-flags`
pub async fn guild_audit_log_flags(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<HashMap<String, bool>>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    match percy.patch_audit_log_flags(guild_id, &body).await {
        Ok(()) => Json(serde_json::json!({"ok": true})).into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/config` — save a section of the config form.
pub async fn guild_config_update(
    State(app): State<AppState>,
    session: Session,
    headers: HeaderMap,
    Path(guild_id): Path<u64>,
    Form(form): Form<ConfigForm>,
) -> Response {
    let Some(percy) = &app.percy else {
        return Redirect::to("/").into_response();
    };
    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return json_or_redirect(&headers, false, "Access denied.", "/dashboard");
    }

    let patch = build_patch(&form.section, &form.fields);
    let redirect_url = format!("/dashboard/guild/{guild_id}");

    if form.section == "flags" {
        // Batch: config + sentinel toggle + clear in one round-trip.
        let gk_enabled = form.fields.contains_key("sentinel");
        let mut ops = vec![
            BatchOperation {
                op_type: "config".into(),
                data: patch,
            },
            BatchOperation {
                op_type: "sentinel_toggle".into(),
                data: serde_json::json!({"enabled": gk_enabled}),
            },
        ];

        let mut clear = serde_json::Map::new();
        if !form.fields.contains_key("audit_log") {
            clear.insert("audit_log_channel_id".into(), serde_json::Value::Null);
        }
        if !form.fields.contains_key("alerts") {
            clear.insert("alert_channel_id".into(), serde_json::Value::Null);
        }
        if !form.fields.contains_key("mentions") {
            clear.insert("mention_count".into(), serde_json::Value::Null);
        }
        if !clear.is_empty() {
            ops.push(BatchOperation {
                op_type: "config".into(),
                data: serde_json::Value::Object(clear),
            });
        }

        match percy.batch_guild_config(guild_id, &ops).await {
            Ok(resp) => {
                // A refused step (Sentinel cannot start yet) does not fail the save, but the user is told why.
                let warnings: Vec<String> = resp
                    .results
                    .into_iter()
                    .flat_map(|r| r.error.filter(|_| !r.ok).into_iter().chain(r.warnings))
                    .collect();
                saved_with_warnings(&headers, "Settings saved.", &warnings, &redirect_url)
            }
            Err(e) => {
                tracing::error!(error = %e, "Failed to batch-update guild config");
                json_or_redirect(&headers, false, &percy_message(&e), &redirect_url)
            }
        }
    } else {
        match percy.patch_guild_config(guild_id, &patch).await {
            Ok(()) => json_or_redirect(&headers, true, "Settings saved.", &redirect_url),
            Err(e) => {
                tracing::error!(error = %e, "Failed to update guild config");
                json_or_redirect(&headers, false, &percy_message(&e), &redirect_url)
            }
        }
    }
}

/// `POST /dashboard/guild/:guild_id/ai` — toggle the server-wide AI feature flags (form).
pub async fn guild_ai_flags_update(
    State(app): State<AppState>,
    session: Session,
    headers: HeaderMap,
    Path(guild_id): Path<u64>,
    Form(fields): Form<HashMap<String, String>>,
) -> Response {
    let Some(percy) = &app.percy else {
        return Redirect::to("/").into_response();
    };
    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return json_or_redirect(&headers, false, "Access denied.", "/dashboard");
    }

    let mut flags = serde_json::Map::new();
    for name in AI_FLAG_NAMES {
        flags.insert(name.to_string(), serde_json::Value::Bool(fields.contains_key(name)));
    }
    let patch = serde_json::json!({ "flags": flags });
    let redirect_url = format!("/dashboard/guild/{guild_id}");

    match percy.patch_ai_flags(guild_id, &patch).await {
        Ok(()) => json_or_redirect(&headers, true, "AI settings saved.", &redirect_url),
        Err(e) => {
            tracing::error!(error = %e, "Failed to update AI flags");
            json_or_redirect(&headers, false, "Failed to save AI settings.", &redirect_url)
        }
    }
}

#[derive(Deserialize)]
pub struct AiOverrideBody {
    channel_id: String,
    #[serde(default)]
    controlled: HashMap<String, bool>,
    #[serde(default)]
    enabled: HashMap<String, bool>,
}

/// `POST /dashboard/guild/:guild_id/ai/override` — create/replace a per-channel AI override (JSON).
pub async fn guild_ai_override_update(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<AiOverrideBody>,
) -> Response {
    let Some(percy) = &app.percy else {
        return Json(serde_json::json!({"ok": false, "error": "Not configured"})).into_response();
    };
    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return Json(serde_json::json!({"ok": false, "error": "Access denied"})).into_response();
    }
    let Ok(channel_id) = body.channel_id.parse::<u64>() else {
        return Json(serde_json::json!({"ok": false, "error": "Invalid channel id"})).into_response();
    };

    let payload = serde_json::json!({ "controlled": body.controlled, "enabled": body.enabled });
    match percy.put_ai_override(guild_id, channel_id, &payload).await {
        Ok(()) => Json(serde_json::json!({"ok": true})).into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

#[derive(Deserialize)]
pub struct AiAskBody {
    question: String,
}

/// `POST /dashboard/guild/:guild_id/ai/ask` — ask the command-palette AI assistant (JSON).
pub async fn guild_ai_ask(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<AiAskBody>,
) -> Response {
    let Some(percy) = &app.percy else {
        return Json(serde_json::json!({"ok": false, "error": "Not configured"})).into_response();
    };
    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return Json(serde_json::json!({"ok": false, "error": "Access denied"})).into_response();
    }
    let question = body.question.trim();
    if question.is_empty() {
        return Json(serde_json::json!({"ok": false, "error": "Ask a question first."})).into_response();
    }

    match percy.ai_ask(guild_id, question).await {
        Ok(resp) => Json(serde_json::json!({
            "ok": true,
            "available": resp.available,
            "reason": resp.reason,
            "answer": resp.answer,
            "suggestions": resp.suggestions,
        }))
        .into_response(),
        Err(e) => {
            tracing::error!(error = %e, "Failed to query AI assistant");
            Json(serde_json::json!({"ok": false, "error": "The AI assistant could not be reached."})).into_response()
        }
    }
}

/// `POST /dashboard/guild/:guild_id/ai/override/:channel_id/delete` — remove an override.
pub async fn guild_ai_override_delete(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, channel_id)): Path<(u64, u64)>,
) -> Response {
    let Some(percy) = &app.percy else {
        return Json(serde_json::json!({"ok": false, "error": "Not configured"})).into_response();
    };
    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return Json(serde_json::json!({"ok": false, "error": "Access denied"})).into_response();
    }
    match percy.delete_ai_override(guild_id, channel_id).await {
        Ok(()) => Json(serde_json::json!({"ok": true})).into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/sentinel` — save sentinel (gatekeeper) settings.
pub async fn guild_sentinel_update(
    State(app): State<AppState>,
    session: Session,
    headers: HeaderMap,
    Path(guild_id): Path<u64>,
    Form(form): Form<HashMap<String, String>>,
) -> Response {
    let Some(percy) = &app.percy else {
        return Redirect::to("/").into_response();
    };
    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return json_or_redirect(&headers, false, "Access denied.", "/dashboard");
    }

    let redirect_url = format!("/dashboard/guild/{guild_id}");

    let starter_title = form.get("starter_title").filter(|s| !s.is_empty());
    let starter_content = form.get("starter_content").filter(|s| !s.is_empty());
    let channel_id = form.get("channel_id").and_then(|s| s.parse::<u64>().ok());
    let sent_starter = starter_title.is_some() && starter_content.is_some() && channel_id.is_some();

    let mut warnings = Vec::new();
    if let (Some(title), Some(content), Some(ch_id)) = (starter_title, starter_content, channel_id) {
        match percy.send_sentinel_message(guild_id, ch_id, title, content).await {
            Ok((_, sent)) => warnings = sent,
            Err(e) => {
                tracing::error!(error = %e, "Failed to send sentinel starter message");
                let msg = format!("Could not send the verification message: {}", percy_message(&e));
                return json_or_redirect(&headers, false, &msg, &redirect_url);
            }
        }
    }

    let mut patch = build_sentinel_patch(&form);
    // channel_id was already applied by send_sentinel_message.
    if sent_starter {
        if let Some(obj) = patch.as_object_mut() {
            obj.remove("channel_id");
        }
    }
    if !patch.as_object().is_none_or(|m| m.is_empty()) {
        match percy.patch_sentinel(guild_id, &patch).await {
            // Both calls report the same channel; the later answer is the current one.
            Ok(saved) => warnings = saved,
            Err(e) => {
                tracing::error!(error = %e, "Failed to update sentinel config");
                let msg = format!("Could not save the sentinel settings: {}", percy_message(&e));
                return json_or_redirect(&headers, false, &msg, &redirect_url);
            }
        }
    }

    // Try to auto-enable once all required fields are set (harmless if not ready).
    if let Err(e) = percy.toggle_sentinel(guild_id, true).await {
        tracing::debug!(error = %e, "Sentinel not ready to enable (expected if setup incomplete)");
    }

    saved_with_warnings(&headers, "Sentinel settings saved.", &warnings, &redirect_url)
}
