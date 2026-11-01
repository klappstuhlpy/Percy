//! The **Developers** tab: create, list and revoke a server's API tokens.
//!
//! Manage Server only, through the same `user_can_manage` gate as every other management page.
//! These are guild-scoped mutations under `/dashboard/guild/:id/developers/…`, so the change-log
//! middleware records them automatically (field *names* only, so the token never reaches the log).

use askama::Template;
use axum::{
    extract::{Path, State},
    http::{header, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use serde::Deserialize;
use serde_json::json;

use super::tokens::{self, ApiToken, CreateError};
use crate::{guilds, session::Session, view::Viewer, AppState};

#[derive(Template)]
#[template(path = "guild/developers.html")]
pub(crate) struct DevelopersTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) tokens: Vec<ApiToken>,
    pub(crate) scopes: &'static [(&'static str, &'static str)],
    pub(crate) max_tokens: usize,
}

/// `GET /dashboard/guild/{id}/developers`
pub(crate) async fn developers_page(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
) -> Response {
    let guild = match guilds::gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let tokens = tokens::list(&app.db, &guild_id.to_string()).await.unwrap_or_default();
    DevelopersTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "developers",
        page_title: "Developers",
        tokens,
        scopes: tokens::SCOPES,
        max_tokens: tokens::MAX_ACTIVE_PER_GUILD,
    }
    .into_response()
}

fn refuse(status: StatusCode, message: &str) -> Response {
    (status, Json(json!({"ok": false, "error": message}))).into_response()
}

#[derive(Deserialize)]
pub(crate) struct CreateBody {
    name: String,
    scopes: Vec<String>,
}

/// `POST /dashboard/guild/{id}/developers/tokens` -> `{ok, id, token}`; the token appears here once.
pub(crate) async fn token_create(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<CreateBody>,
) -> Response {
    if !guilds::user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return refuse(StatusCode::FORBIDDEN, "You need Manage Server to create API tokens.");
    }
    let name = body.name.trim();
    if name.is_empty() || name.chars().count() > 50 {
        return refuse(StatusCode::BAD_REQUEST, "Give the token a name of 1 to 50 characters.");
    }
    let mut scopes: Vec<String> = body.scopes;
    scopes.sort();
    scopes.dedup();
    if scopes.is_empty() || !scopes.iter().all(|s| tokens::is_known_scope(s)) {
        return refuse(StatusCode::BAD_REQUEST, "Pick at least one valid scope.");
    }

    match tokens::create(&app.db, &guild_id.to_string(), name, &scopes, &session.discord_user_id).await {
        Ok(new) => (
            [(header::CACHE_CONTROL, "no-store")],
            Json(json!({"ok": true, "id": new.id, "token": new.plaintext})),
        )
            .into_response(),
        Err(CreateError::TooMany) => refuse(
            StatusCode::CONFLICT,
            &format!(
                "This server already has {} active tokens. Revoke one first.",
                tokens::MAX_ACTIVE_PER_GUILD
            ),
        ),
        Err(e) => {
            match e {
                CreateError::Db(e) => tracing::error!(error = %e, "could not create an API token"),
                _ => tracing::error!("could not create an API token (no randomness)"),
            }
            refuse(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Could not create the token. Try again.",
            )
        }
    }
}

/// `DELETE /dashboard/guild/{id}/developers/tokens/{token_id}` — instant: the gateway re-reads the table per request.
pub(crate) async fn token_revoke(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, token_id)): Path<(u64, i64)>,
) -> Response {
    if !guilds::user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return refuse(StatusCode::FORBIDDEN, "You need Manage Server to revoke API tokens.");
    }
    match tokens::revoke(&app.db, &guild_id.to_string(), token_id).await {
        Ok(true) => Json(json!({"ok": true})).into_response(),
        Ok(false) => refuse(
            StatusCode::NOT_FOUND,
            "That token does not exist or was already revoked.",
        ),
        Err(e) => {
            tracing::error!(error = %e, "could not revoke an API token");
            refuse(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Could not revoke the token. Try again.",
            )
        }
    }
}
