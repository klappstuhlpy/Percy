//! The read-only public **overview** page and the signed-in user's own
//! **self / history / data** pages — ported 1:1 from the monolith. These use the
//! weaker "is a member" gate ([`user_is_member`]) rather than manage access,
//! except the moderator-facing member **lookup**, which requires manage access.

use crate::{
    guilds::{user_can_manage, user_is_member},
    session::Session,
    templates::{OverviewTemplate, UserDashboardTemplate, UserLookupTemplate},
    view::Viewer,
    AppState,
};
use axum::{
    extract::{Path, State},
    http::{header, StatusCode},
    response::{IntoResponse, Redirect, Response},
    Json,
};
use percy_client::{
    BalancesResponse, BotStats, EconomyInfo, LeaderboardResponse, MusicFiltersState, MusicInfo, UserSettings,
    UserSettingsPatch,
};

/// `GET /dashboard/guild/:guild_id/me` — the signed-in member's own profile page.
pub async fn guild_user_self(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let Some(percy) = &app.percy else {
        return Redirect::to("/").into_response();
    };
    let discord_id = &session.discord_user_id;
    if !user_is_member(&app, discord_id, guild_id).await {
        return Redirect::to("/dashboard").into_response();
    }

    let (guild, profile, settings) = tokio::join!(
        percy.get_guild(guild_id),
        percy.get_member_self(guild_id, discord_id),
        percy.get_user_settings(discord_id),
    );

    let guild = match guild {
        Ok(g) => g,
        Err(_) => return Redirect::to("/dashboard").into_response(),
    };
    let profile = match profile {
        Ok(p) => p,
        Err(_) => return Redirect::to(&format!("/dashboard/guild/{guild_id}/overview")).into_response(),
    };
    let settings = settings.unwrap_or(UserSettings {
        timezone: None,
        track_presence: true,
        track_history: true,
    });

    UserDashboardTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        guild_icon: guild.icon_url,
        profile,
        settings,
    }
    .into_response()
}

/// `PATCH /dashboard/guild/:guild_id/me/settings`
pub async fn guild_user_settings_update(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let Some(percy) = &app.percy else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    let discord_id = &session.discord_user_id;
    if !user_is_member(&app, discord_id, guild_id).await {
        return StatusCode::FORBIDDEN.into_response();
    }

    let patch = UserSettingsPatch {
        timezone: if body.get("timezone").is_some() {
            Some(body["timezone"].as_str().map(|s| s.to_string()))
        } else {
            None
        },
        track_presence: body.get("track_presence").and_then(|v| v.as_bool()),
        track_history: body.get("track_history").and_then(|v| v.as_bool()),
    };

    match percy.patch_user_settings(discord_id, &patch).await {
        Ok(()) => Json(serde_json::json!({"ok": true})).into_response(),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"error": e.to_string()})),
        )
            .into_response(),
    }
}

/// `GET /dashboard/guild/:guild_id/me/history` — the user's consent-tracked
/// history as JSON for the page's client renderer.
pub async fn guild_user_history(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let Some(percy) = &app.percy else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    let discord_id = &session.discord_user_id;
    if !user_is_member(&app, discord_id, guild_id).await {
        return StatusCode::FORBIDDEN.into_response();
    }
    match percy.get_user_history(discord_id).await {
        Ok(history) => Json(history).into_response(),
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({"error": e.to_string()})),
        )
            .into_response(),
    }
}

/// `GET /dashboard/guild/:guild_id/me/data-export` — full GDPR-style export.
pub async fn guild_user_data_export(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
) -> Response {
    let Some(percy) = &app.percy else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    let discord_id = &session.discord_user_id;
    if !user_is_member(&app, discord_id, guild_id).await {
        return StatusCode::FORBIDDEN.into_response();
    }
    match percy.export_user_data(discord_id).await {
        Ok(data) => {
            let body = serde_json::to_vec_pretty(&data).unwrap_or_default();
            (
                [
                    (header::CONTENT_TYPE, "application/json".to_string()),
                    (
                        header::CONTENT_DISPOSITION,
                        format!("attachment; filename=\"percy-data-{discord_id}.json\""),
                    ),
                ],
                body,
            )
                .into_response()
        }
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({"error": e.to_string()})),
        )
            .into_response(),
    }
}

/// `POST /dashboard/guild/:guild_id/me/delete-data` — irreversibly erases the
/// user's stored presence/avatar/name history.
pub async fn guild_user_data_delete(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
) -> Response {
    let Some(percy) = &app.percy else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    let discord_id = &session.discord_user_id;
    if !user_is_member(&app, discord_id, guild_id).await {
        return StatusCode::FORBIDDEN.into_response();
    }
    match percy.delete_user_personal_data(discord_id).await {
        Ok(()) => Json(serde_json::json!({"ok": true})).into_response(),
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({"error": e.to_string()})),
        )
            .into_response(),
    }
}

/// `GET /dashboard/guild/:guild_id/members/:user_id` — a moderator's read of a
/// single member's detail page. Requires manage access.
pub async fn guild_user_lookup(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, user_id)): Path<(u64, String)>,
) -> Response {
    let Some(percy) = &app.percy else {
        return Redirect::to("/").into_response();
    };
    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return Redirect::to("/dashboard").into_response();
    }

    let (guild, member) = tokio::join!(percy.get_guild(guild_id), percy.get_member_detail(guild_id, &user_id));

    let guild = match guild {
        Ok(g) => g,
        Err(_) => return Redirect::to("/dashboard").into_response(),
    };
    let member = match member {
        Ok(m) => m,
        Err(_) => return Redirect::to(&format!("/dashboard/guild/{guild_id}/members")).into_response(),
    };
    let member_name = member.name.clone();

    UserLookupTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "members",
        page_title: member_name,
        member,
    }
    .into_response()
}

/// `GET /dashboard/guild/:guild_id/overview` — read-only public overview for
/// members who can't manage the guild.
pub async fn guild_overview(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
) -> Result<OverviewTemplate, StatusCode> {
    let Some(percy) = &app.percy else {
        return Err(StatusCode::SERVICE_UNAVAILABLE);
    };
    let discord_id = &session.discord_user_id;
    if !user_is_member(&app, discord_id, guild_id).await {
        return Err(StatusCode::FORBIDDEN);
    }

    let (guild, stats, bot_stats, music, leaderboard, polls, giveaways, economy, games, balances) = tokio::join!(
        percy.get_guild(guild_id),
        percy.get_guild_stats(guild_id),
        percy.get_bot_stats(),
        percy.get_music(guild_id),
        percy.get_leveling_leaderboard(guild_id, 10),
        percy.get_polls(guild_id),
        percy.get_giveaways(guild_id),
        percy.get_economy(guild_id),
        percy.get_games_stats(guild_id),
        percy.get_economy_balances(guild_id, 5),
    );

    let guild = match guild {
        Ok(g) => g,
        Err(_) => return Err(StatusCode::NOT_FOUND),
    };
    let stats = match stats {
        Ok(s) => s,
        Err(_) => return Err(StatusCode::BAD_GATEWAY),
    };

    let bot_stats = bot_stats.unwrap_or(BotStats {
        version: None,
        guild_count: 0,
        user_count: 0,
        channel_count: 0,
        total_commands_used: 0,
        cog_count: 0,
        command_count: 0,
        latency_ms: 0.0,
        uptime_seconds: 0.0,
    });
    let music = music.unwrap_or(MusicInfo {
        active: false,
        equalizer: vec![0.0; 15],
        filters: MusicFiltersState {
            nightcore: false,
            eight_d: false,
            lowpass: None,
        },
        presets: vec![],
        now_playing: None,
        queue: Vec::new(),
        history: Vec::new(),
        channel: None,
        channel_name: None,
        setup: None,
        always_on: Default::default(),
        listeners: Vec::new(),
    });
    let can_control_music = music.active && music.listeners.contains(discord_id);
    let leaderboard = leaderboard.unwrap_or(LeaderboardResponse {
        entries: Vec::new(),
        total: 0,
    });
    let active_polls = polls
        .map(|p| p.polls.into_iter().filter(|p| !p.ended).collect())
        .unwrap_or_default();
    let active_giveaways = giveaways
        .map(|g| g.giveaways.into_iter().filter(|g| !g.ended).collect())
        .unwrap_or_default();
    let economy = economy.unwrap_or(EconomyInfo {
        items: Vec::new(),
        lottery: None,
        settings: None,
    });
    let games = games.unwrap_or_default();
    let balances = balances.unwrap_or(BalancesResponse {
        entries: Vec::new(),
        total: 0,
    });

    Ok(OverviewTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        guild_icon: guild.icon_url,
        member_count: guild.member_count,
        stats,
        bot_stats,
        music,
        can_control_music,
        leaderboard,
        active_polls,
        active_giveaways,
        economy,
        games,
        balances,
    })
}

/// `GET /dashboard/guild/:guild_id/overview/music` — live now-playing state
/// for the overview's auto-refreshing music panel. Membership-gated.
pub async fn guild_overview_music_status(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
) -> Response {
    let Some(percy) = &app.percy else {
        return Json(serde_json::json!({"ok": false})).into_response();
    };
    let discord_id = &session.discord_user_id;
    if !user_is_member(&app, discord_id, guild_id).await {
        return Json(serde_json::json!({"ok": false, "error": "Access denied"})).into_response();
    }
    match percy.get_music(guild_id).await {
        Ok(music) => {
            let can_control = music.active && music.listeners.contains(discord_id);
            Json(serde_json::json!({
                "ok": true,
                "active": music.active,
                "channel": music.channel,
                "channel_name": music.channel_name,
                "now_playing": music.now_playing,
                "queue": music.queue,
                "history": music.history,
                "can_control": can_control,
            }))
            .into_response()
        }
        Err(e) => Json(serde_json::json!({"ok": false, "error": e.to_string()})).into_response(),
    }
}

/// `GET /dashboard/guild/:guild_id/overview/music/lyrics` — synced lyrics for
/// the overview's player. Membership-gated.
pub async fn guild_overview_music_lyrics(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
) -> Response {
    let Some(percy) = &app.percy else {
        return Json(serde_json::json!({"ok": false})).into_response();
    };
    let discord_id = &session.discord_user_id;
    if !user_is_member(&app, discord_id, guild_id).await {
        return Json(serde_json::json!({"ok": false, "error": "Access denied"})).into_response();
    }
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

/// `POST /dashboard/guild/:guild_id/overview/music/control` — drive the shared
/// live player from the public overview. Membership-gated; Percy enforces
/// voice-presence and DJ-mode.
pub async fn guild_overview_music_control(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(mut body): Json<serde_json::Value>,
) -> Response {
    let Some(percy) = &app.percy else {
        return Json(serde_json::json!({"ok": false, "error": "Not configured"})).into_response();
    };
    let discord_id = &session.discord_user_id;
    if !user_is_member(&app, discord_id, guild_id).await {
        return Json(serde_json::json!({"ok": false, "error": "Access denied"})).into_response();
    }
    // Inject the authenticated identity; never trust a client-supplied user_id.
    if let Some(obj) = body.as_object_mut() {
        obj.insert("user_id".into(), serde_json::Value::String(discord_id.clone()));
    }
    match percy.music_control(guild_id, &body).await {
        Ok(()) => Json(serde_json::json!({"ok": true})).into_response(),
        Err(e) => Json(serde_json::json!({"ok": false, "error": e.to_string()})).into_response(),
    }
}
