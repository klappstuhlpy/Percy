//! The music app: one Rythm-style page for the personal route (`/dashboard/music`) and the guild
//! route (`/dashboard/guild/:id/music`), plus the JSON the page's client script drives.
//!
//! What a viewer may press is Percy's call (`GET .../music/viewer` → role); the BFF only gates
//! *membership* (the session user shares the guild with Percy) and always sends the session
//! user's id, never a browser value. Manager forms keep posting to the guild-admin routes in
//! `actions.rs`, which keep their own gate.

#![allow(clippy::result_large_err)] // handlers return the early-exit Response, as gate_json does

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use percy_client::{MusicInfo, MusicViewer, PercyClient, PercyError, UserMusicLibrary};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::{
    errors::percy_message,
    guilds::fetch_mutual_guilds,
    session::Session,
    templates::{GuildPick, MusicAppTemplate},
    view::Viewer,
    AppState,
};

/// How many guild history rows the first frame carries.
const HISTORY_FIRST_FRAME: u32 = 20;

fn fail(status: StatusCode, error: impl Into<String>) -> Response {
    (status, Json(json!({"ok": false, "error": error.into()}))).into_response()
}

/// Percy's `detail` text with a status: a missing thing is 404, an unreachable Percy 502, and every
/// refusal 400 (`PercyError::Api` keeps the body but not the status).
fn percy_fail(e: &PercyError) -> Response {
    let status = match e {
        PercyError::NotFound => StatusCode::NOT_FOUND,
        PercyError::Http(_) => StatusCode::BAD_GATEWAY,
        _ => StatusCode::BAD_REQUEST,
    };
    fail(status, percy_message(e))
}

/// The session user's id as Percy wants it; a session that is not numeric cannot act.
fn user_id(session: &Session) -> Result<u64, Response> {
    session
        .discord_user_id
        .parse()
        .map_err(|_| fail(StatusCode::BAD_REQUEST, "Invalid session"))
}

/// Membership gate for `/dashboard/music/:guild_id/*`: Percy configured, user shares the guild.
/// A guild the user is not in is a 404, not a 403, so ids do not leak.
async fn member_gate<'a>(app: &'a AppState, session: &Session, guild_id: u64) -> Result<&'a PercyClient, Response> {
    let Some(percy) = &app.percy else {
        return Err(fail(StatusCode::SERVICE_UNAVAILABLE, "Percy is not configured"));
    };
    let gid = guild_id.to_string();
    let member = fetch_mutual_guilds(app, &session.discord_user_id)
        .await
        .iter()
        .any(|g| g.id == gid);
    if !member {
        return Err(fail(StatusCode::NOT_FOUND, "Not found"));
    }
    Ok(percy)
}

/// Overwrites `user_id` with the session user, whatever the browser sent.
pub(crate) fn with_session_user(mut body: Value, session_user: &str) -> Value {
    if let Some(obj) = body.as_object_mut() {
        obj.insert("user_id".into(), Value::String(session_user.to_string()));
    }
    body
}

// ── Pages ───────────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct MusicQuery {
    #[serde(default)]
    guild: Option<u64>,
}

/// `GET /dashboard/music` — the personal route; `?guild=` pre-picks a server.
pub async fn music_page(State(app): State<AppState>, session: Session, Query(q): Query<MusicQuery>) -> Response {
    render_app(&app, &session, q.guild, false).await
}

/// Builds the app for `guild_id` (or the picker alone). Every Percy read degrades to an empty
/// default so the page always renders.
pub(crate) async fn render_app(
    app: &AppState,
    session: &Session,
    guild_id: Option<u64>,
    manager_view: bool,
) -> Response {
    let mutual = fetch_mutual_guilds(app, &session.discord_user_id).await;
    // The guild-admin routes gate on `manageable`; the admin-only forms show under the same rule.
    let admin_ids: Vec<String> = mutual.iter().filter(|g| g.manageable).map(|g| g.id.clone()).collect();
    let mut guilds: Vec<GuildPick> = mutual
        .into_iter()
        .map(|g| GuildPick {
            id: g.id,
            name: g.name,
            icon: g.icon_url,
            playing: false,
        })
        .collect();

    let selected_idx = guild_id.and_then(|id| guilds.iter().position(|g| g.id == id.to_string()));
    let mut viewer = None;
    let mut music = None;
    let mut history = Vec::new();
    let mut channels = Vec::new();
    let mut is_admin = false;
    let mut library = UserMusicLibrary::default();

    if let (Some(percy), Some(i), Ok(uid)) = (&app.percy, selected_idx, user_id(session)) {
        let gid: u64 = guilds[i].id.parse().unwrap_or_default();
        let (v, m, h, lib) = tokio::join!(
            percy.get_music_viewer(gid, uid),
            percy.get_music(gid),
            percy.get_music_history(gid, HISTORY_FIRST_FRAME, None),
            percy.get_user_music(uid),
        );
        viewer = v.ok();
        music = m.ok();
        history = h.map(|h| h.items).unwrap_or_default();
        library = lib.unwrap_or_default();
        // ponytail: only the picked server's dot is live; one get_music per server is too many calls.
        guilds[i].playing = music.as_ref().is_some_and(|m| m.active);
        is_admin = admin_ids.contains(&guilds[i].id);
        if is_admin {
            channels = percy.get_guild_channels(gid).await.unwrap_or_default();
        }
    } else if let (Some(percy), Ok(uid)) = (&app.percy, user_id(session)) {
        library = percy.get_user_music(uid).await.unwrap_or_default();
    }

    let selected = selected_idx.map(|i| guilds[i].clone());
    MusicAppTemplate {
        account: Some(Viewer::from_session(session)),
        page_title: "Music",
        guilds,
        selected,
        viewer,
        music,
        history,
        library,
        channels,
        manager_view,
        is_admin,
    }
    .into_response()
}

// ── Member JSON ─────────────────────────────────────────────────────────────

/// `GET /dashboard/music/:guild_id/status` — live player poll.
pub async fn music_status(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let percy = match member_gate(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    match percy.get_music(guild_id).await {
        Ok(m) => Json(status_json(&m)).into_response(),
        Err(e) => percy_fail(&e),
    }
}

fn status_json(m: &MusicInfo) -> Value {
    json!({
        "ok": true,
        "active": m.active,
        "channel": m.channel,
        "channel_name": m.channel_name,
        "now_playing": m.now_playing,
        "queue": m.queue,
        "history": m.history,
        "listeners": m.listeners.len(),
    })
}

/// `GET /dashboard/music/:guild_id/viewer` — the caller's role in this guild.
pub async fn music_viewer(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let percy = match member_gate(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let uid = match user_id(&session) {
        Ok(u) => u,
        Err(r) => return r,
    };
    match percy.get_music_viewer(guild_id, uid).await {
        Ok(v) => Json(viewer_json(&v)).into_response(),
        Err(e) => percy_fail(&e),
    }
}

fn viewer_json(v: &MusicViewer) -> Value {
    let mut j = serde_json::to_value(v).unwrap_or_else(|_| json!({}));
    j["ok"] = json!(true);
    j
}

#[derive(Deserialize)]
pub struct HistoryQuery {
    #[serde(default)]
    limit: Option<u32>,
    #[serde(default)]
    before: Option<String>,
}

/// `GET /dashboard/music/:guild_id/history` — recently played in the guild.
pub async fn music_history(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Query(q): Query<HistoryQuery>,
) -> Response {
    let percy = match member_gate(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    match percy
        .get_music_history(
            guild_id,
            q.limit.unwrap_or(HISTORY_FIRST_FRAME).min(100),
            q.before.as_deref(),
        )
        .await
    {
        Ok(h) => Json(json!({"ok": true, "items": h.items})).into_response(),
        Err(e) => percy_fail(&e),
    }
}

#[derive(Deserialize)]
pub struct SearchQuery {
    #[serde(default)]
    q: String,
    #[serde(default)]
    limit: Option<u8>,
}

/// `GET /dashboard/music/:guild_id/search?q=` — track search for the search box.
pub async fn music_search(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Query(q): Query<SearchQuery>,
) -> Response {
    let percy = match member_gate(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    if q.q.trim().is_empty() {
        return Json(json!({"ok": true, "results": []})).into_response();
    }
    match percy
        .search_music(guild_id, q.q.trim(), q.limit.unwrap_or(8).min(25))
        .await
    {
        Ok(s) => Json(json!({"ok": true, "results": s.results})).into_response(),
        Err(e) => percy_fail(&e),
    }
}

/// `GET /dashboard/music/:guild_id/lyrics`
pub async fn music_lyrics(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let percy = match member_gate(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    match percy.get_music_lyrics(guild_id).await {
        Ok(l) => Json(json!({
            "ok": true,
            "has_synced": l.has_synced,
            "title": l.title,
            "source": l.source,
            "lines": l.lines,
            "plain": l.plain,
        }))
        .into_response(),
        Err(e) => percy_fail(&e),
    }
}

#[derive(Deserialize)]
pub struct PlayBody {
    query: String,
}

/// `POST /dashboard/music/:guild_id/play` — queue a query as the session user.
pub async fn music_play(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<PlayBody>,
) -> Response {
    let percy = match member_gate(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let uid = match user_id(&session) {
        Ok(u) => u,
        Err(r) => return r,
    };
    match percy.play_music(guild_id, uid, &body.query).await {
        Ok(r) => Json(json!({"ok": true, "queued": r.queued, "started": r.started})).into_response(),
        Err(e) => percy_fail(&e),
    }
}

/// `POST /dashboard/music/:guild_id/control` — drive the player; Percy enforces role and DJ mode.
pub async fn music_control(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<Value>,
) -> Response {
    let percy = match member_gate(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let body = with_session_user(body, &session.discord_user_id);
    match percy.music_control(guild_id, &body).await {
        Ok(()) => Json(json!({"ok": true})).into_response(),
        Err(e) => percy_fail(&e),
    }
}

// ── Account JSON (the user's own library) ───────────────────────────────────

async fn account_gate<'a>(app: &'a AppState, session: &Session) -> Result<(&'a PercyClient, u64), Response> {
    let Some(percy) = &app.percy else {
        return Err(fail(StatusCode::SERVICE_UNAVAILABLE, "Percy is not configured"));
    };
    Ok((percy, user_id(session)?))
}

/// `GET /account/music`
pub async fn account_music(State(app): State<AppState>, session: Session) -> Response {
    let (percy, uid) = match account_gate(&app, &session).await {
        Ok(x) => x,
        Err(r) => return r,
    };
    match percy.get_user_music(uid).await {
        Ok(l) => Json(json!({"ok": true, "library": l})).into_response(),
        Err(e) => percy_fail(&e),
    }
}

#[derive(Deserialize)]
pub struct LikeBody {
    #[serde(default)]
    name: String,
    url: String,
}

/// `POST /account/music/liked`
pub async fn account_music_like(State(app): State<AppState>, session: Session, Json(b): Json<LikeBody>) -> Response {
    let (percy, uid) = match account_gate(&app, &session).await {
        Ok(x) => x,
        Err(r) => return r,
    };
    match percy.like_track(uid, &b.name, &b.url).await {
        Ok(()) => Json(json!({"ok": true})).into_response(),
        Err(e) => percy_fail(&e),
    }
}

/// `POST /account/music/liked/remove`
pub async fn account_music_unlike(State(app): State<AppState>, session: Session, Json(b): Json<LikeBody>) -> Response {
    let (percy, uid) = match account_gate(&app, &session).await {
        Ok(x) => x,
        Err(r) => return r,
    };
    match percy.unlike_track(uid, &b.url).await {
        Ok(()) => Json(json!({"ok": true})).into_response(),
        Err(e) => percy_fail(&e),
    }
}

/// `GET /account/music/playlists/:playlist_id` — ids are numeric; anything else is a 404.
pub async fn account_music_playlist(
    State(app): State<AppState>,
    session: Session,
    Path(playlist_id): Path<String>,
) -> Response {
    let (percy, uid) = match account_gate(&app, &session).await {
        Ok(x) => x,
        Err(r) => return r,
    };
    let Ok(id) = playlist_id.parse::<u64>() else {
        return fail(StatusCode::NOT_FOUND, "Not found");
    };
    match percy.get_user_playlist(uid, &id.to_string()).await {
        Ok(p) => Json(json!({"ok": true, "playlist": p})).into_response(),
        Err(e) => percy_fail(&e),
    }
}

/// `MusicHistoryItem` length as `m:ss`, for the template.
pub(crate) fn fmt_ms(ms: u64) -> String {
    let s = ms / 1000;
    format!("{}:{:02}", s / 60, s % 60)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{build_state_with, config::Config};
    use std::path::Path as FsPath;

    async fn body_text(resp: Response) -> (StatusCode, String) {
        let status = resp.status();
        let bytes = axum::body::to_bytes(resp.into_body(), usize::MAX).await.unwrap();
        (status, String::from_utf8_lossy(&bytes).into_owned())
    }

    /// A Percy stand-in that answers every request with an empty JSON list: the user shares no guild.
    async fn state_with_empty_percy() -> AppState {
        let fake = axum::Router::new().fallback(|| async { Json(json!([])) });
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move { axum::serve(listener, fake).await });
        let mut config = Config::test_default();
        config.percy.api_url = Some(format!("http://{addr}"));
        build_state_with(config, FsPath::new(":memory:")).await.unwrap()
    }

    #[tokio::test]
    async fn music_app_renders_when_percy_down() {
        // Percy configured but nothing listens there: every read errors.
        let mut config = Config::test_default();
        config.percy.api_url = Some("http://127.0.0.1:1".into());
        let app = build_state_with(config, FsPath::new(":memory:")).await.unwrap();
        let (status, html) = body_text(render_app(&app, &Session::new("42", "ferris"), Some(5), false).await).await;
        assert_eq!(status, StatusCode::OK);
        assert!(html.contains("unreachable"), "empty state missing");
    }

    #[tokio::test]
    async fn music_app_rejects_non_member() {
        let app = state_with_empty_percy().await;
        let resp = music_viewer(State(app), Session::new("42", "ferris"), Path(5)).await;
        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn music_control_overwrites_user_id() {
        // Fake Percy: user 42 shares guild 5; the control call records the body it receives.
        let seen: std::sync::Arc<std::sync::Mutex<Option<Value>>> = Default::default();
        let sink = seen.clone();
        let fake = axum::Router::new()
            .route(
                "/api/v1/users/42/guilds",
                axum::routing::get(|| async { Json(json!([{"id": "5", "name": "Home", "owner": false}])) }),
            )
            .route(
                "/api/v1/guilds/5/music/control",
                axum::routing::post(move |Json(b): Json<Value>| async move {
                    *sink.lock().unwrap() = Some(b);
                    Json(json!({}))
                }),
            );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move { axum::serve(listener, fake).await });
        let mut config = Config::test_default();
        config.percy.api_url = Some(format!("http://{addr}"));
        let app = build_state_with(config, FsPath::new(":memory:")).await.unwrap();

        let resp = music_control(
            State(app),
            Session::new("42", "ferris"),
            Path(5),
            Json(json!({"action": "pause", "user_id": "666"})),
        )
        .await;
        assert_eq!(resp.status(), StatusCode::OK);
        let got = seen.lock().unwrap().clone().expect("Percy was called");
        assert_eq!(got["user_id"], "42");
        assert_eq!(got["action"], "pause");
    }

    #[test]
    fn durations_format_as_m_ss() {
        assert_eq!(fmt_ms(239_000), "3:59");
        assert_eq!(fmt_ms(0), "0:00");
    }
}

#[cfg(test)]
mod render_tests {
    use super::*;
    use askama::Template;

    fn template(is_manager: bool, is_admin: bool) -> MusicAppTemplate {
        let pick = GuildPick {
            id: "5".into(),
            name: "Home".into(),
            icon: None,
            playing: true,
        };
        let music: MusicInfo = serde_json::from_value(json!({
            "active": true, "equalizer": [0.0], "filters": {}, "presets": [],
            "now_playing": {"title": "Song", "author": "Band", "duration": 1000, "position": 0},
        }))
        .unwrap();
        MusicAppTemplate {
            account: None,
            page_title: "Music",
            guilds: vec![pick.clone()],
            selected: Some(pick),
            viewer: Some(MusicViewer {
                is_manager,
                role: "listener".into(),
                ..Default::default()
            }),
            music: Some(music),
            history: vec![],
            library: UserMusicLibrary::default(),
            channels: vec![],
            manager_view: false,
            is_admin,
        }
    }

    #[test]
    fn admin_forms_render_only_for_admins() {
        let plain = template(false, false).render().unwrap();
        assert!(plain.contains("Song") && plain.contains(r#"data-role="listener""#));
        assert!(!plain.contains("eq-section") && !plain.contains("setup-section"));

        // A DJ (manager) without Manage Server sees no admin form: those routes would refuse them.
        let dj = template(true, false).render().unwrap();
        assert!(!dj.contains("eq-section") && !dj.contains("setup-section") && !dj.contains("dj-mode-section"));

        let admin = template(true, true).render().unwrap();
        assert!(admin.contains("eq-section") && admin.contains("dj-mode-section") && admin.contains("setup-section"));
    }
}
