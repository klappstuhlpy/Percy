//! Community features that had no dashboard surface: **role menus**, the **starboard**,
//! creating/editing **tags**, and the member's own **reminders**. Every write is a JSON
//! forward to Percy (which enforces the same rules as the Discord commands); the pages
//! are server-rendered from Percy's read endpoints.

use crate::{
    errors::percy_message,
    guilds::{gate_and_load_guild, user_is_member},
    mutations::{gate_json, ok_or_err},
    session::Session,
    templates::{RoleMenusTemplate, StarboardTemplate},
    view::Viewer,
    AppState,
};
use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use percy_client::ActionMessage;

fn message_or_err(result: Result<ActionMessage, percy_client::PercyError>) -> Response {
    match result {
        Ok(m) => Json(serde_json::json!({"ok": true, "message": m.message})).into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

/// Snowflakes arrive from the browser as strings; Percy's models take integers.
fn snowflakes(mut body: serde_json::Value, keys: &[&str]) -> serde_json::Value {
    for key in keys {
        if let Some(raw) = body.get(*key).and_then(|v| v.as_str()).map(str::to_owned) {
            body[*key] = raw
                .parse::<u64>()
                .map(serde_json::Value::from)
                .unwrap_or(serde_json::Value::Null);
        }
    }
    body
}

// -- Role menus ----------------------------------------------------------------------

/// `GET /dashboard/guild/:guild_id/role-menus`
pub async fn guild_role_menus(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");
    let (menus, channels, roles) = tokio::join!(
        percy.get_role_menus(guild_id),
        percy.get_guild_channels(guild_id),
        percy.get_guild_roles(guild_id),
    );
    RoleMenusTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "role-menus",
        page_title: "Role Menus",
        data: menus.unwrap_or_default(),
        channels: channels
            .unwrap_or_default()
            .into_iter()
            .filter(|c| c.channel_type == "text")
            .collect(),
        roles: roles
            .unwrap_or_default()
            .into_iter()
            .filter(|r| r.name != "@everyone")
            .collect(),
    }
    .into_response()
}

/// `POST /dashboard/guild/:guild_id/role-menus` — `{channel_id, title, description?, unique?}`
pub async fn guild_role_menu_create(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    message_or_err(
        percy
            .create_role_menu(guild_id, &snowflakes(body, &["channel_id"]))
            .await,
    )
}

/// `POST /dashboard/guild/:guild_id/role-menus/:menu_id` — any of `{title, description, unique}`
pub async fn guild_role_menu_update(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, menu_id)): Path<(u64, i64)>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    ok_or_err(percy.patch_role_menu(guild_id, menu_id, &body).await)
}

/// `POST /dashboard/guild/:guild_id/role-menus/:menu_id/roles` — `{role_id, emoji?, label?}`
pub async fn guild_role_menu_add_role(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, menu_id)): Path<(u64, i64)>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    // Always the session user (never a browser value): Percy refuses roles at/above their top role.
    let mut body = snowflakes(body, &["role_id"]);
    body["user_id"] = session.discord_user_id.clone().into();
    ok_or_err(percy.add_role_menu_role(guild_id, menu_id, &body).await)
}

/// `POST /dashboard/guild/:guild_id/role-menus/:menu_id/roles/:role_id/delete`
pub async fn guild_role_menu_remove_role(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, menu_id, role_id)): Path<(u64, i64, u64)>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    ok_or_err(percy.remove_role_menu_role(guild_id, menu_id, role_id).await)
}

/// `POST /dashboard/guild/:guild_id/role-menus/:menu_id/delete`
pub async fn guild_role_menu_delete(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, menu_id)): Path<(u64, i64)>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    ok_or_err(percy.delete_role_menu(guild_id, menu_id).await)
}

// -- Starboard -----------------------------------------------------------------------

/// `GET /dashboard/guild/:guild_id/starboard`
pub async fn guild_starboard(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");
    let (starboard, channels) = tokio::join!(percy.get_starboard(guild_id), percy.get_guild_channels(guild_id));
    let starboard = starboard.unwrap_or_default();
    let ignored: Vec<String> = starboard
        .ignored_channels
        .iter()
        .flatten()
        .map(|c| c.id.clone())
        .collect();
    StarboardTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "starboard",
        page_title: "Starboard",
        selected_channel: starboard.channel.as_ref().map(|c| c.id.clone()).unwrap_or_default(),
        ignored,
        starboard,
        channels: channels
            .unwrap_or_default()
            .into_iter()
            .filter(|c| c.channel_type == "text")
            .collect(),
    }
    .into_response()
}

/// `POST /dashboard/guild/:guild_id/starboard` — the settings form (JSON).
pub async fn guild_starboard_update(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let mut body = snowflakes(body, &["channel_id"]);
    if let Some(ids) = body.get("ignored_channel_ids").and_then(|v| v.as_array()).cloned() {
        body["ignored_channel_ids"] = ids
            .iter()
            .filter_map(|v| v.as_str()?.parse::<u64>().ok())
            .map(serde_json::Value::from)
            .collect();
    }
    ok_or_err(percy.patch_starboard(guild_id, &body).await)
}

// -- Tags (write) ----------------------------------------------------------------------

/// `POST /dashboard/guild/:guild_id/tags/create` — `{name, content}`; the signed-in user owns it.
pub async fn guild_tag_create(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let name = body["name"].as_str().unwrap_or_default();
    let content = body["content"].as_str().unwrap_or_default();
    message_or_err(
        percy
            .create_tag(guild_id, name, content, &session.discord_user_id)
            .await,
    )
}

/// `POST /dashboard/guild/:guild_id/tags/:tag_id/edit` — `{content}`
pub async fn guild_tag_edit(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, tag_id)): Path<(u64, i64)>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    message_or_err(
        percy
            .edit_tag(guild_id, tag_id, body["content"].as_str().unwrap_or_default())
            .await,
    )
}

// -- Reminders (the member's own) --------------------------------------------------------

/// `POST /dashboard/guild/:guild_id/me/reminders/:reminder_id/delete`
pub async fn guild_user_reminder_delete(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, reminder_id)): Path<(u64, i64)>,
) -> Response {
    let Some(percy) = &app.percy else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    if !user_is_member(&app, &session.discord_user_id, guild_id).await {
        return StatusCode::FORBIDDEN.into_response();
    }
    ok_or_err(percy.delete_user_reminder(&session.discord_user_id, reminder_id).await)
}

#[cfg(test)]
mod tests {
    use super::snowflakes;

    #[test]
    fn string_snowflakes_become_integers_and_garbage_becomes_null() {
        let body = snowflakes(
            serde_json::json!({"channel_id": "1234567890123", "role_id": "nope", "title": "x"}),
            &["channel_id", "role_id"],
        );
        assert_eq!(body["channel_id"], 1234567890123u64);
        assert!(body["role_id"].is_null());
        assert_eq!(body["title"], "x");
    }
}
