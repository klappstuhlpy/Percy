//! The public, no-login-required leaderboard page (`/lb/:target`) and its vanity
//! slug management — ported 1:1 from the monolith. `:target` is either a raw
//! guild id or a claimed vanity slug. Vanity claims are stored in the dashboard's
//! own `percy_leaderboard_vanity` table (see [`crate::db`]).

use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Redirect, Response},
    Json,
};
use percy_client::{BalancesResponse, LeaderboardResponse};
use serde::Deserialize;

use crate::{
    db, forms::json_or_redirect, guilds::user_can_manage, session::Session, templates::LeaderboardTemplate,
    view::Viewer, AppState,
};

/// Resolves a `:target` (raw guild id or vanity slug) to a guild id.
async fn resolve_target(app: &AppState, target: &str) -> Option<u64> {
    if let Ok(id) = target.parse::<u64>() {
        return Some(id);
    }
    let slug = target.to_lowercase();
    db::vanity_by_slug(&app.db, &slug)
        .await
        .ok()
        .flatten()
        .and_then(|v| v.guild_id.parse::<u64>().ok())
}

/// `GET /lb/:target` — public leaderboard page (no login required).
pub async fn public_leaderboard(
    State(app): State<AppState>,
    session: Option<Session>,
    Path(target): Path<String>,
) -> Response {
    let Some(percy) = &app.percy else {
        return Redirect::to("/dashboard").into_response();
    };
    let Some(guild_id) = resolve_target(&app, &target).await else {
        return (StatusCode::NOT_FOUND, "Leaderboard not found").into_response();
    };

    let (guild, leaderboard, balances) = tokio::join!(
        percy.get_guild(guild_id),
        percy.get_leveling_leaderboard(guild_id, 100),
        percy.get_economy_balances(guild_id, 100),
    );

    let guild = match guild {
        Ok(g) => g,
        Err(_) => return (StatusCode::NOT_FOUND, "Guild not found").into_response(),
    };
    let leaderboard = leaderboard.unwrap_or(LeaderboardResponse {
        entries: Vec::new(),
        total: 0,
    });
    let balances = balances.unwrap_or(BalancesResponse {
        entries: Vec::new(),
        total: 0,
    });
    let vanity = db::vanity_for_guild(&app.db, &guild_id.to_string())
        .await
        .ok()
        .flatten()
        .map(|v| v.slug);

    // A signed-in viewer who manages the guild may edit its vanity slug.
    let can_manage = match &session {
        Some(s) => user_can_manage(&app, &s.discord_user_id, guild_id).await,
        None => false,
    };

    LeaderboardTemplate {
        account: session.as_ref().map(Viewer::from_session),
        guild_id,
        guild_name: guild.name,
        guild_icon: guild.icon_url,
        leaderboard,
        balances,
        vanity,
        can_manage,
    }
    .into_response()
}

#[derive(Deserialize)]
pub struct VanityClaimBody {
    slug: String,
}

/// `POST /lb/:guild_id/vanity` — claim or update a vanity slug.
pub async fn public_leaderboard_vanity_claim(
    State(app): State<AppState>,
    session: Session,
    headers: HeaderMap,
    Path(guild_id): Path<u64>,
    Json(body): Json<VanityClaimBody>,
) -> Response {
    let back = format!("/lb/{guild_id}");
    if app.percy.is_none() {
        return json_or_redirect(&headers, false, "Not configured", &back);
    }
    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return json_or_redirect(
            &headers,
            false,
            "You don't have permission to manage this server",
            &back,
        );
    }

    let slug = body.slug.trim().to_lowercase();
    if slug.is_empty() || slug.len() > 32 {
        return json_or_redirect(&headers, false, "Vanity URL must be 1-32 characters", &back);
    }
    if !slug.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_') {
        return json_or_redirect(
            &headers,
            false,
            "Vanity URL can only contain letters, numbers, hyphens, and underscores",
            &back,
        );
    }
    if slug.parse::<u64>().is_ok() {
        return json_or_redirect(&headers, false, "Vanity URL cannot be a number", &back);
    }

    match db::set_vanity(&app.db, &slug, &guild_id.to_string(), &session.discord_user_id).await {
        Ok(Ok(())) => json_or_redirect(&headers, true, &format!("Vanity URL set to /lb/{slug}"), &back),
        Ok(Err(msg)) => json_or_redirect(&headers, false, msg, &back),
        Err(_) => json_or_redirect(&headers, false, "Database error", &back),
    }
}

/// `DELETE /lb/:guild_id/vanity` — remove a vanity slug.
pub async fn public_leaderboard_vanity_delete(
    State(app): State<AppState>,
    session: Session,
    headers: HeaderMap,
    Path(guild_id): Path<u64>,
) -> Response {
    let back = format!("/lb/{guild_id}");
    if app.percy.is_none() {
        return json_or_redirect(&headers, false, "Not configured", &back);
    }
    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return json_or_redirect(
            &headers,
            false,
            "You don't have permission to manage this server",
            &back,
        );
    }

    let _ = db::delete_vanity(&app.db, &guild_id.to_string()).await;
    json_or_redirect(&headers, true, "Vanity URL removed", &back)
}
