//! CRUD sub-resource proxies (economy items/lottery/balances, comics, temp
//! channels, autoresponders, highlights, status feed) — ported 1:1 from the
//! monolith. Each gates on a manage-access session then forwards the payload to
//! Percy's internal API and answers the dashboard JS's `{ok}` / `{ok,error}` JSON.

use axum::{
    extract::{Path, State},
    http::HeaderMap,
    response::{IntoResponse, Redirect, Response},
    Json,
};

use crate::{
    forms::json_or_redirect,
    guilds::user_can_manage,
    mutations::{gate_json, ok_or_err},
    session::Session,
    AppState,
};

/// `POST /dashboard/guild/:guild_id/comics/:brand/push`
pub async fn guild_comics_push(
    State(app): State<AppState>,
    session: Session,
    headers: HeaderMap,
    Path((guild_id, brand)): Path<(u64, String)>,
) -> Response {
    let Some(percy) = &app.percy else {
        return Redirect::to("/").into_response();
    };
    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return json_or_redirect(&headers, false, "Access denied.", "/dashboard");
    }
    let redirect_url = format!("/dashboard/guild/{guild_id}/comics");
    match percy.push_comic(guild_id, &brand).await {
        Ok(()) => json_or_redirect(&headers, true, "Feed pushed.", &redirect_url),
        Err(e) => json_or_redirect(&headers, false, &format!("Failed: {e}"), &redirect_url),
    }
}

/// `PATCH /dashboard/guild/:guild_id/autoresponders/:trigger`
pub async fn guild_autoresponder_patch(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, trigger)): Path<(u64, String)>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    match gate_json(&app, &session, guild_id).await {
        Ok(percy) => ok_or_err(percy.patch_autoresponder(guild_id, &trigger, &body).await),
        Err(response) => response,
    }
}

/// `DELETE /dashboard/guild/:guild_id/autoresponders/:trigger`
pub async fn guild_autoresponder_delete(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, trigger)): Path<(u64, String)>,
) -> Response {
    match gate_json(&app, &session, guild_id).await {
        Ok(percy) => ok_or_err(percy.delete_autoresponder(guild_id, &trigger).await),
        Err(response) => response,
    }
}

/// `DELETE /dashboard/guild/:guild_id/economy/items/:name`
pub async fn guild_economy_item_delete(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, name)): Path<(u64, String)>,
) -> Response {
    match gate_json(&app, &session, guild_id).await {
        Ok(percy) => ok_or_err(percy.delete_economy_item(guild_id, &name).await),
        Err(response) => response,
    }
}

/// `POST /dashboard/guild/:guild_id/economy/items`
pub async fn guild_economy_items_create(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    match gate_json(&app, &session, guild_id).await {
        Ok(percy) => ok_or_err(percy.create_economy_item(guild_id, &body).await),
        Err(response) => response,
    }
}

/// `PATCH /dashboard/guild/:guild_id/economy/settings`
pub async fn guild_economy_settings_update(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    match gate_json(&app, &session, guild_id).await {
        Ok(percy) => ok_or_err(percy.patch_economy_settings(guild_id, &body).await),
        Err(response) => response,
    }
}

/// `POST /dashboard/guild/:guild_id/economy/lottery`
pub async fn guild_economy_lottery_create(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    match gate_json(&app, &session, guild_id).await {
        Ok(percy) => ok_or_err(percy.create_lottery(guild_id, &body).await),
        Err(response) => response,
    }
}

/// `DELETE /dashboard/guild/:guild_id/economy/lottery`
pub async fn guild_economy_lottery_delete(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
) -> Response {
    match gate_json(&app, &session, guild_id).await {
        Ok(percy) => ok_or_err(percy.delete_lottery(guild_id).await),
        Err(response) => response,
    }
}

/// `PATCH /dashboard/guild/:guild_id/economy/balances/:user_id`
pub async fn guild_economy_balance_update(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, user_id)): Path<(u64, String)>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    match gate_json(&app, &session, guild_id).await {
        Ok(percy) => ok_or_err(percy.patch_economy_balance(guild_id, &user_id, &body).await),
        Err(response) => response,
    }
}

/// `POST /dashboard/guild/:guild_id/status-feed`
pub async fn guild_status_feed_subscribe(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    match gate_json(&app, &session, guild_id).await {
        Ok(percy) => ok_or_err(percy.post_status_feed(guild_id, &body).await),
        Err(response) => response,
    }
}

/// `DELETE /dashboard/guild/:guild_id/status-feed`
pub async fn guild_status_feed_unsubscribe(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
) -> Response {
    match gate_json(&app, &session, guild_id).await {
        Ok(percy) => ok_or_err(percy.delete_status_feed(guild_id).await),
        Err(response) => response,
    }
}

/// `PATCH /dashboard/guild/:guild_id/comics/:brand`
pub async fn guild_comic_patch(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, brand)): Path<(u64, String)>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    match gate_json(&app, &session, guild_id).await {
        Ok(percy) => ok_or_err(percy.patch_comic(guild_id, &brand, &body).await),
        Err(response) => response,
    }
}

/// `DELETE /dashboard/guild/:guild_id/comics/:brand`
pub async fn guild_comic_delete(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, brand)): Path<(u64, String)>,
) -> Response {
    match gate_json(&app, &session, guild_id).await {
        Ok(percy) => ok_or_err(percy.delete_comic(guild_id, &brand).await),
        Err(response) => response,
    }
}

/// `POST /dashboard/guild/:guild_id/comics`
pub async fn guild_comic_create(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    match gate_json(&app, &session, guild_id).await {
        Ok(percy) => ok_or_err(percy.create_comic(guild_id, &body).await),
        Err(response) => response,
    }
}

/// `POST /dashboard/guild/:guild_id/temp-channels`
pub async fn guild_temp_channel_create(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    match gate_json(&app, &session, guild_id).await {
        Ok(percy) => ok_or_err(percy.create_temp_channel(guild_id, &body).await),
        Err(response) => response,
    }
}

/// `PATCH /dashboard/guild/:guild_id/temp-channels/:channel_id`
pub async fn guild_temp_channel_patch(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, channel_id)): Path<(u64, u64)>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    match gate_json(&app, &session, guild_id).await {
        Ok(percy) => ok_or_err(percy.patch_temp_channel(guild_id, channel_id, &body).await),
        Err(response) => response,
    }
}

/// `DELETE /dashboard/guild/:guild_id/temp-channels/:channel_id`
pub async fn guild_temp_channel_delete(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, channel_id)): Path<(u64, u64)>,
) -> Response {
    match gate_json(&app, &session, guild_id).await {
        Ok(percy) => ok_or_err(percy.delete_temp_channel(guild_id, channel_id).await),
        Err(response) => response,
    }
}

/// `DELETE /dashboard/guild/:guild_id/highlights/:user_id`
pub async fn guild_highlight_delete(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, user_id)): Path<(u64, String)>,
) -> Response {
    match gate_json(&app, &session, guild_id).await {
        Ok(percy) => ok_or_err(percy.delete_highlight(guild_id, &user_id).await),
        Err(response) => response,
    }
}
