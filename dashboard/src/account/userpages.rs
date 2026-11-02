//! The read-only public **overview** page and the signed-in user's own
//! **self / history / data** pages — ported 1:1 from the monolith. These use the
//! weaker "is a member" gate ([`user_is_member`]) rather than manage access,
//! except the moderator-facing member **lookup**, which requires manage access.

use crate::{
    errors::percy_message,
    guilds::{user_can_manage, user_is_member},
    session::Session,
    templates::{AccountTemplate, CardField, OverviewTemplate, UserDashboardTemplate, UserLookupTemplate, CARD_FIELDS},
    view::Viewer,
    AppState,
};
use axum::{
    extract::{Path, Query, State},
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

    let (guild, profile, settings, rank_card, reminders) = tokio::join!(
        percy.get_guild(guild_id),
        percy.get_member_self(guild_id, discord_id),
        percy.get_user_settings(discord_id),
        percy.get_rank_card(discord_id, guild_id),
        percy.get_user_reminders(discord_id),
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
        card_fields: rank_card.map(|c| CardField::from_rank_card(&c)).unwrap_or_default(),
        reminders: reminders.map(|r| r.reminders).unwrap_or_default(),
    }
    .into_response()
}

/// Only the known card fields with a non-empty value survive; Percy validates the values.
fn card_style(input: &std::collections::HashMap<String, String>) -> std::collections::HashMap<String, String> {
    CARD_FIELDS
        .iter()
        .filter_map(|(key, _)| {
            input
                .get(*key)
                .filter(|v| !v.is_empty())
                .map(|v| (key.to_string(), v.clone()))
        })
        .collect()
}

/// `POST /dashboard/guild/:guild_id/me/rank-card` — store the member's card style (JSON map).
pub async fn guild_user_rank_card_update(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<std::collections::HashMap<String, String>>,
) -> Response {
    let Some(percy) = &app.percy else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    let discord_id = &session.discord_user_id;
    if !user_is_member(&app, discord_id, guild_id).await {
        return StatusCode::FORBIDDEN.into_response();
    }
    match percy.put_rank_card(discord_id, &card_style(&body)).await {
        Ok(()) => {
            Json(serde_json::json!({"ok": true, "message": "Rank card saved. /level now shows it."})).into_response()
        }
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"error": percy_message(&e)})),
        )
            .into_response(),
    }
}

/// `GET /dashboard/guild/:guild_id/me/rank-card.png?theme=&accent=&bar=&font=` — the member's
/// real card rendered in a draft style (nothing is stored). Empty values follow the server.
pub async fn guild_user_rank_card_preview(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Query(query): Query<std::collections::HashMap<String, String>>,
) -> Response {
    let Some(percy) = &app.percy else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    let discord_id = &session.discord_user_id;
    if !user_is_member(&app, discord_id, guild_id).await {
        return StatusCode::FORBIDDEN.into_response();
    }
    match percy.preview_rank_card(discord_id, guild_id, &card_style(&query)).await {
        Ok(png) => (
            [(header::CONTENT_TYPE, "image/png"), (header::CACHE_CONTROL, "no-store")],
            png,
        )
            .into_response(),
        Err(e) => (StatusCode::BAD_GATEWAY, percy_message(&e)).into_response(),
    }
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
            Json(serde_json::json!({"error": percy_message(&e)})),
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
            Json(serde_json::json!({"error": percy_message(&e)})),
        )
            .into_response(),
    }
}

/// `GET /dashboard/guild/:guild_id/me/data-export` — redirects to the
/// account-level export (these operations are global, not guild-scoped).
pub async fn guild_user_data_export(
    _state: State<AppState>,
    _session: Session,
    Path(_guild_id): Path<u64>,
) -> Redirect {
    Redirect::to("/account/data-export")
}

/// `POST /dashboard/guild/:guild_id/me/delete-data` — redirects to the account
/// page (deletion is now handled globally at `/account`).
pub async fn guild_user_data_delete(
    _state: State<AppState>,
    _session: Session,
    Path(_guild_id): Path<u64>,
) -> Redirect {
    Redirect::to("/account")
}

// ─── Account-level pages (no guild scope) ─────────────────────────────────

/// `GET /account` — the signed-in user's account page. Shows global settings
/// and data management (export/delete) without guild scoping.
pub async fn account_page(State(app): State<AppState>, session: Session) -> Response {
    let Some(percy) = &app.percy else {
        return Redirect::to("/").into_response();
    };
    let discord_id = &session.discord_user_id;

    let settings = percy.get_user_settings(discord_id).await.unwrap_or(UserSettings {
        timezone: None,
        track_presence: true,
        track_history: true,
    });

    AccountTemplate {
        account: Some(Viewer::from_session(&session)),
        settings,
        discord_id: discord_id.clone(),
        username: session.username.clone(),
    }
    .into_response()
}

/// `POST /account/settings` — update the user's global settings.
pub async fn account_settings_update(
    State(app): State<AppState>,
    session: Session,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let Some(percy) = &app.percy else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    let discord_id = &session.discord_user_id;

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
            Json(serde_json::json!({"error": percy_message(&e)})),
        )
            .into_response(),
    }
}

/// `GET /account/data-export` — full GDPR-style export, not guild-scoped.
pub async fn account_data_export(State(app): State<AppState>, session: Session) -> Response {
    let Some(percy) = &app.percy else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    let discord_id = &session.discord_user_id;
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
            Json(serde_json::json!({"error": percy_message(&e)})),
        )
            .into_response(),
    }
}

/// `POST /account/delete-data` — irreversibly erases the user's stored
/// personal history across all guilds.
pub async fn account_data_delete(State(app): State<AppState>, session: Session) -> Response {
    let Some(percy) = &app.percy else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    let discord_id = &session.discord_user_id;
    match percy.delete_user_personal_data(discord_id).await {
        Ok(()) => Json(serde_json::json!({"ok": true})).into_response(),
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({"error": percy_message(&e)})),
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

/// Percy's own answer to "may this member drive the player" (`resolve_viewer`): listeners
/// in the bot's channel, plus DJs and Manage Server from anywhere, with DJ-only mode
/// applied. `false` when Percy can't say.
async fn viewer_can_control_music(percy: &percy_client::PercyClient, guild_id: u64, discord_id: &str) -> bool {
    let Ok(user_id) = discord_id.parse::<u64>() else {
        return false;
    };
    percy
        .get_music_viewer(guild_id, user_id)
        .await
        .is_ok_and(|v| v.can_control)
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
    let viewer_can_control = viewer_can_control_music(percy, guild_id, discord_id).await;

    let guild = match guild {
        Ok(g) => g,
        Err(_) => return Err(StatusCode::NOT_FOUND),
    };
    let stats = match stats {
        Ok(s) => s,
        Err(_) => return Err(StatusCode::BAD_GATEWAY),
    };

    let bot_stats = bot_stats.unwrap_or(BotStats {
        ai: None,
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
            x8d: false,
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
    let can_control_music = music.active && viewer_can_control;
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
        settings: percy_client::EconomySettings::default(),
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
            let can_control = music.active && viewer_can_control_music(percy, guild_id, discord_id).await;
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
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
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
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
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
        Err(e) => Json(serde_json::json!({"ok": false, "error": percy_message(&e)})).into_response(),
    }
}

#[cfg(test)]
mod rank_card_tests {
    use super::card_style;
    use crate::templates::CardField;
    use std::collections::HashMap;

    #[test]
    fn only_known_non_empty_card_fields_are_forwarded() {
        let input: HashMap<String, String> = [("theme", "ink"), ("accent", ""), ("background_url", "x")]
            .into_iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        assert_eq!(
            card_style(&input),
            HashMap::from([("theme".to_string(), "ink".to_string())])
        );
    }

    #[test]
    fn a_locked_server_hides_the_member_editor() {
        let card: percy_client::RankCard = serde_json::from_value(serde_json::json!({
            "style": {}, "guild_default": {}, "effective": {},
            "locked": true,
            "options": {"theme": [{"value": "panel", "label": "Panel"}]},
        }))
        .unwrap();
        assert!(CardField::from_rank_card(&card).is_empty());

        let open: percy_client::RankCard = serde_json::from_value(serde_json::json!({
            "style": {}, "effective": {}, "locked": false,
            "guild_default": {"theme": "ink"},
            "options": {"theme": [{"value": "panel", "label": "Panel"}, {"value": "ink", "label": "Ink"}]},
        }))
        .unwrap();
        let fields = CardField::from_rank_card(&open);
        assert_eq!(fields[0].default_label, "Server default (Ink)");
        assert_eq!(fields[0].current, "");
    }
}
