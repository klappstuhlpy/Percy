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
use std::{
    collections::HashMap,
    sync::{LazyLock, Mutex},
    time::{Duration, Instant},
};

use crate::{
    errors::percy_message,
    guilds::fetch_mutual_guilds,
    session::Session,
    templates::{GuildPick, MusicAppTemplate},
    view::Viewer,
    AppState,
};

type Key = (u64, u64, String);

/// Tiny in-process TTL map (no cache crate in the tree), keyed `(guild_id, user_id, extra)`.
/// Bounded: at the cap, expired entries go, then an arbitrary one. Callers store successes only.
struct TtlCache {
    ttl: Duration,
    max: usize,
    map: Mutex<HashMap<Key, (Instant, Value)>>,
}

impl TtlCache {
    fn new(ttl_secs: u64, max: usize) -> Self {
        Self {
            ttl: Duration::from_secs(ttl_secs),
            max,
            map: Mutex::new(HashMap::new()),
        }
    }

    fn get(&self, key: &Key) -> Option<Value> {
        let map = self.map.lock().ok()?;
        map.get(key)
            .filter(|(at, _)| at.elapsed() < self.ttl)
            .map(|(_, v)| v.clone())
    }

    fn put(&self, key: Key, v: Value) {
        let Ok(mut map) = self.map.lock() else { return };
        if map.len() >= self.max {
            let ttl = self.ttl;
            map.retain(|_, (at, _)| at.elapsed() < ttl);
            if map.len() >= self.max {
                if let Some(k) = map.keys().next().cloned() {
                    map.remove(&k);
                }
            }
        }
        map.insert(key, (Instant::now(), v));
    }

    /// Drops every entry of one user (their likes/plays change what is worth recommending).
    fn forget_user(&self, uid: u64) {
        if let Ok(mut map) = self.map.lock() {
            map.retain(|k, _| k.1 != uid);
        }
    }
}

/// Searches are shared across users (user 0), 2 minutes.
static SEARCH_CACHE: LazyLock<TtlCache> = LazyLock::new(|| TtlCache::new(120, 512));
/// Recommendations are per user and guild, 10 minutes.
static RECS_CACHE: LazyLock<TtlCache> = LazyLock::new(|| TtlCache::new(600, 512));

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

pub(crate) fn status_json(m: &MusicInfo) -> Value {
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
    let limit = q.limit.unwrap_or(8).min(25);
    let key = (guild_id, 0, format!("{}\0{limit}", q.q.trim().to_lowercase()));
    if let Some(hit) = SEARCH_CACHE.get(&key) {
        return Json(hit).into_response();
    }
    match percy.search_music(guild_id, q.q.trim(), limit).await {
        Ok(s) => {
            let out = json!({"ok": true, "results": s.results});
            SEARCH_CACHE.put(key, out.clone());
            Json(out).into_response()
        }
        Err(e) => percy_fail(&e),
    }
}

#[derive(Deserialize)]
pub struct DetailQuery {
    #[serde(default)]
    name: String,
    #[serde(default)]
    url: String,
}

/// A link Percy may open: http(s) only, bounded. Anything else is a 400 before it leaves the BFF.
fn clean_url(raw: &str) -> Result<Option<String>, Response> {
    let u = raw.trim();
    if u.is_empty() {
        return Ok(None);
    }
    let lower = u.to_ascii_lowercase();
    if u.len() > 500 || !(lower.starts_with("https://") || lower.starts_with("http://")) {
        return Err(fail(StatusCode::BAD_REQUEST, "Invalid link"));
    }
    Ok(Some(u.to_string()))
}

/// `GET /dashboard/music/:guild_id/artist?name=&url=` — artist page data (cached like search).
pub async fn music_artist(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Query(q): Query<DetailQuery>,
) -> Response {
    let percy = match member_gate(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let name = q.name.trim();
    let url = match clean_url(&q.url) {
        Ok(u) => u,
        Err(r) => return r,
    };
    if name.chars().count() > 120 || (name.is_empty() && url.is_none()) {
        return fail(StatusCode::BAD_REQUEST, "Name or link required");
    }
    let key = (
        guild_id,
        0,
        format!("artist {} {}", name.to_lowercase(), url.as_deref().unwrap_or("")),
    );
    if let Some(hit) = SEARCH_CACHE.get(&key) {
        return Json(hit).into_response();
    }
    match percy.get_music_artist(guild_id, name, url.as_deref()).await {
        Ok(a) => {
            let out = json!({"ok": true, "artist": a.artist, "top_tracks": a.top_tracks, "releases": a.releases});
            SEARCH_CACHE.put(key, out.clone());
            Json(out).into_response()
        }
        Err(e) => percy_fail(&e),
    }
}

/// `GET /dashboard/music/:guild_id/album?url=` — album, single or playlist link as a track list.
pub async fn music_album(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Query(q): Query<DetailQuery>,
) -> Response {
    let percy = match member_gate(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let url = match clean_url(&q.url) {
        Ok(Some(u)) => u,
        Ok(None) => return fail(StatusCode::BAD_REQUEST, "Link required"),
        Err(r) => return r,
    };
    let key = (guild_id, 0, format!("album {url}"));
    if let Some(hit) = SEARCH_CACHE.get(&key) {
        return Json(hit).into_response();
    }
    match percy.get_music_album(guild_id, &url).await {
        Ok(a) => {
            let out = json!({"ok": true, "album": a.album, "tracks": a.tracks});
            SEARCH_CACHE.put(key, out.clone());
            Json(out).into_response()
        }
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
    /// More than one queues a mix of the top results (vibe tiles); clamped to 1-25.
    #[serde(default)]
    limit: Option<u8>,
    /// Front of the queue and skip to it, keeping the rest (Percy gates it on skip rights).
    #[serde(default)]
    play_now: bool,
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
    let limit = body.limit.map(|n| n.clamp(1, 25));
    match percy.play_music(guild_id, uid, &body.query, limit, body.play_now).await {
        Ok(r) => {
            RECS_CACHE.forget_user(uid);
            Json(json!({"ok": true, "queued": r.queued, "started": r.started})).into_response()
        }
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

/// `GET /dashboard/music/:guild_id/audio` — equalizer and every filter of the live player.
pub async fn music_audio(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let percy = match member_gate(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    match percy.get_music_audio(guild_id).await {
        Ok(a) => {
            let mut j = serde_json::to_value(&a).unwrap_or_else(|_| json!({}));
            j["ok"] = json!(true);
            Json(j).into_response()
        }
        Err(e) => percy_fail(&e),
    }
}

/// `POST /dashboard/music/:guild_id/audio` — an effect, fine values or a reset; Percy gates on the session user.
pub async fn music_audio_set(
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
    match percy.set_music_audio(guild_id, &body).await {
        Ok(a) => {
            let mut j = serde_json::to_value(&a).unwrap_or_else(|_| json!({}));
            j["ok"] = json!(true);
            Json(j).into_response()
        }
        Err(e) => percy_fail(&e),
    }
}

/// `POST /dashboard/music/:guild_id/equalizer` — a preset or 15 band gains, as the session user.
pub async fn music_equalizer(
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
    match percy.post_music_equalizer(guild_id, &body).await {
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
        Ok(()) => {
            RECS_CACHE.forget_user(uid);
            Json(json!({"ok": true})).into_response()
        }
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
        Ok(()) => {
            RECS_CACHE.forget_user(uid);
            Json(json!({"ok": true})).into_response()
        }
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

// ── Recommendations ─────────────────────────────────────────────────────────

/// How many top artists seed recommendations, and how many tracks each contributes.
const REC_ARTISTS: usize = 3;
const REC_PER_ARTIST: u8 = 6;

/// The user's most-played artists, in play order, without repeats or blanks.
fn top_artists(top: &[percy_client::TopTrack]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for a in top.iter().filter_map(|t| t.author.as_deref()).map(str::trim) {
        if !a.is_empty() && !out.iter().any(|o| o.eq_ignore_ascii_case(a)) {
            out.push(a.to_string());
        }
        if out.len() == REC_ARTISTS {
            break;
        }
    }
    out
}

/// Groups search results per artist, dropping tracks the user already plays (by uri or title) and
/// repeats across groups; empty groups are left out.
fn build_recommendations(
    top: &[percy_client::TopTrack],
    results: Vec<(String, Vec<percy_client::MusicTrack>)>,
) -> Value {
    let mut seen: Vec<String> = top
        .iter()
        .flat_map(|t| [t.uri.clone().unwrap_or_default(), t.title.to_lowercase()])
        .filter(|s| !s.is_empty())
        .collect();
    let groups: Vec<Value> = results
        .into_iter()
        .filter_map(|(artist, tracks)| {
            let kept: Vec<_> = tracks
                .into_iter()
                .filter(|t| {
                    let keys = [t.uri.clone().unwrap_or_default(), t.title.to_lowercase()];
                    if keys.iter().any(|k| !k.is_empty() && seen.contains(k)) {
                        return false;
                    }
                    seen.extend(keys.into_iter().filter(|k| !k.is_empty()));
                    true
                })
                .collect();
            (!kept.is_empty()).then(|| json!({"artist": artist, "tracks": kept}))
        })
        .collect();
    json!({"ok": true, "groups": groups})
}

/// `GET /dashboard/music/:guild_id/recommendations` — tracks by the user's top artists, searched
/// through the guild's Lavalink node. Empty (not an error) when the user has no history.
pub async fn music_recommendations(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
) -> Response {
    let percy = match member_gate(&app, &session, guild_id).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let uid = match user_id(&session) {
        Ok(u) => u,
        Err(r) => return r,
    };
    let key = (guild_id, uid, String::new());
    if let Some(hit) = RECS_CACHE.get(&key) {
        return Json(hit).into_response();
    }
    let library = match percy.get_user_music(uid).await {
        Ok(l) => l,
        Err(e) => return percy_fail(&e),
    };
    let artists = top_artists(&library.top_tracks);
    let search = |i: usize| {
        let artist = artists.get(i).cloned();
        async move {
            match artist {
                Some(a) => {
                    let found = percy.search_music(guild_id, &a, REC_PER_ARTIST).await;
                    Some((a, found.map(|s| s.results)))
                }
                None => None,
            }
        }
    };
    let (a, b, c) = tokio::join!(search(0), search(1), search(2));
    // A failed per-artist search just drops that group, but then the answer is not cached.
    let mut all_ok = true;
    let results = [a, b, c]
        .into_iter()
        .flatten()
        .map(|(artist, r)| {
            all_ok &= r.is_ok();
            (artist, r.unwrap_or_default())
        })
        .collect();
    let out = build_recommendations(&library.top_tracks, results);
    if all_ok {
        RECS_CACHE.put(key, out.clone());
    }
    Json(out).into_response()
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

    #[tokio::test]
    async fn music_audio_writes_carry_the_session_user() {
        let seen: std::sync::Arc<std::sync::Mutex<Vec<Value>>> = Default::default();
        let (a, b) = (seen.clone(), seen.clone());
        let fake = axum::Router::new()
            .route(
                "/api/v1/users/42/guilds",
                axum::routing::get(|| async { Json(json!([{"id": "5", "name": "Home", "owner": false}])) }),
            )
            .route(
                "/api/v1/guilds/5/music/audio",
                axum::routing::post(move |Json(v): Json<Value>| async move {
                    a.lock().unwrap().push(v);
                    Json(json!({"active": true, "effects": ["8d"]}))
                }),
            )
            .route(
                "/api/v1/guilds/5/music/equalizer",
                axum::routing::post(move |Json(v): Json<Value>| async move {
                    b.lock().unwrap().push(v);
                    Json(json!({}))
                }),
            );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move { axum::serve(listener, fake).await });
        let mut config = Config::test_default();
        config.percy.api_url = Some(format!("http://{addr}"));
        let app = build_state_with(config, FsPath::new(":memory:")).await.unwrap();

        let r = music_audio_set(
            State(app.clone()),
            Session::new("42", "ferris"),
            Path(5),
            Json(json!({"effect": "8d", "user_id": "666"})),
        )
        .await;
        assert_eq!(r.status(), StatusCode::OK);
        let r = music_equalizer(
            State(app),
            Session::new("42", "ferris"),
            Path(5),
            Json(json!({"preset": "pop"})),
        )
        .await;
        assert_eq!(r.status(), StatusCode::OK);
        let got = seen.lock().unwrap().clone();
        assert_eq!(got.len(), 2);
        assert!(got.iter().all(|b| b["user_id"] == "42"));
    }

    /// Fake Percy where user 42 shares guild 7001; `hits` counts search calls and `fail` flips them to 500.
    async fn counting_percy(
        hits: std::sync::Arc<std::sync::atomic::AtomicUsize>,
        fail: std::sync::Arc<std::sync::atomic::AtomicBool>,
    ) -> AppState {
        use std::sync::atomic::Ordering::SeqCst;
        let album_hits = hits.clone();
        let fake = axum::Router::new()
            .route(
                "/api/v1/guilds/7001/music/album",
                axum::routing::get(move || async move {
                    album_hits.fetch_add(1, SeqCst);
                    Json(json!({"album": {"title": "Glass Tides", "artists": ["Nova Quill"]}, "tracks": []}))
                }),
            )
            .route(
                "/api/v1/users/42/guilds",
                axum::routing::get(|| async { Json(json!([{"id": "7001", "name": "Home", "owner": false}])) }),
            )
            .route(
                "/api/v1/users/42/music",
                axum::routing::get(|| async {
                    Json(json!({"top_tracks": [{"title": "Old", "author": "Band", "uri": "u0"}]}))
                }),
            )
            .route(
                "/api/v1/guilds/7001/music/search",
                axum::routing::get(move || async move {
                    hits.fetch_add(1, SeqCst);
                    if fail.load(SeqCst) {
                        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"detail": "down"})));
                    }
                    (
                        StatusCode::OK,
                        Json(json!({"results": [{"title": "New", "author": "Band", "uri": "u1"}]})),
                    )
                }),
            );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move { axum::serve(listener, fake).await });
        let mut config = Config::test_default();
        config.percy.api_url = Some(format!("http://{addr}"));
        build_state_with(config, FsPath::new(":memory:")).await.unwrap()
    }

    #[tokio::test]
    async fn album_and_artist_validate_links_and_album_is_cached() {
        use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering::SeqCst};
        let hits = std::sync::Arc::new(AtomicUsize::new(0));
        let app = counting_percy(hits.clone(), std::sync::Arc::new(AtomicBool::new(false))).await;
        let album = |url: &str| {
            music_album(
                State(app.clone()),
                Session::new("42", "ferris"),
                Path(7001),
                Query(DetailQuery {
                    name: String::new(),
                    url: url.into(),
                }),
            )
        };
        let artist = |name: &str, url: &str| {
            music_artist(
                State(app.clone()),
                Session::new("42", "ferris"),
                Path(7001),
                Query(DetailQuery {
                    name: name.into(),
                    url: url.into(),
                }),
            )
        };
        assert_eq!(album("").await.status(), StatusCode::BAD_REQUEST);
        assert_eq!(album("javascript:alert(1)").await.status(), StatusCode::BAD_REQUEST);
        assert_eq!(artist("", "").await.status(), StatusCode::BAD_REQUEST);
        assert_eq!(artist(&"x".repeat(121), "").await.status(), StatusCode::BAD_REQUEST);
        assert_eq!(artist("A", "ftp://x.test/a").await.status(), StatusCode::BAD_REQUEST);
        assert_eq!(hits.load(SeqCst), 0);
        assert_eq!(album("https://x.test/album/1").await.status(), StatusCode::OK);
        assert_eq!(album("https://x.test/album/1").await.status(), StatusCode::OK);
        assert_eq!(hits.load(SeqCst), 1);
    }

    #[tokio::test]
    async fn search_and_recommendations_are_cached_but_errors_are_not() {
        use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering::SeqCst};
        let hits = std::sync::Arc::new(AtomicUsize::new(0));
        let fail = std::sync::Arc::new(AtomicBool::new(true));
        let app = counting_percy(hits.clone(), fail.clone()).await;
        let session = || Session::new("42", "ferris");
        let search = |q: &str| {
            music_search(
                State(app.clone()),
                session(),
                Path(7001),
                Query(SearchQuery {
                    q: q.into(),
                    limit: None,
                }),
            )
        };

        // Errors are not cached: the retry reaches Percy again.
        assert_eq!(search("cache-me").await.status(), StatusCode::BAD_REQUEST);
        assert_eq!(search("cache-me").await.status(), StatusCode::BAD_REQUEST);
        assert_eq!(hits.load(SeqCst), 2);

        fail.store(false, SeqCst);
        assert_eq!(search("cache-me").await.status(), StatusCode::OK);
        assert_eq!(search(" Cache-Me ").await.status(), StatusCode::OK);
        assert_eq!(hits.load(SeqCst), 3, "second call served from cache");

        // Recommendations: one artist search the first time, none the second; a like clears them.
        let recs = || music_recommendations(State(app.clone()), session(), Path(7001));
        assert_eq!(recs().await.status(), StatusCode::OK);
        let after_first = hits.load(SeqCst);
        assert_eq!(recs().await.status(), StatusCode::OK);
        assert_eq!(hits.load(SeqCst), after_first, "recommendations cached");
        RECS_CACHE.forget_user(42);
        assert_eq!(recs().await.status(), StatusCode::OK);
        assert!(hits.load(SeqCst) > after_first, "invalidated entry refetches");
    }

    #[test]
    fn recommendations_skip_known_and_repeated_tracks() {
        let top: Vec<percy_client::TopTrack> = serde_json::from_value(json!([
            {"title": "Paper Lanterns", "author": "The Quiet Hours", "uri": "u1"},
            {"title": "Glass Harbour", "author": "the quiet hours"},
            {"title": "Copper Skies", "author": "Lumen & Vale"},
        ]))
        .unwrap();
        assert_eq!(top_artists(&top), vec!["The Quiet Hours", "Lumen & Vale"]);
        let t = |title: &str, uri: &str| -> percy_client::MusicTrack {
            serde_json::from_value(json!({"title": title, "author": "x", "uri": uri})).unwrap()
        };
        let out = build_recommendations(
            &top,
            vec![
                (
                    "The Quiet Hours".into(),
                    vec![t("Paper Lanterns", "u1"), t("Small Weather", "u2")],
                ),
                (
                    "Lumen & Vale".into(),
                    vec![t("small weather", "u2"), t("glass harbour", "u3")],
                ),
            ],
        );
        let groups = out["groups"].as_array().unwrap();
        assert_eq!(groups.len(), 1, "the second group only held known or repeated tracks");
        assert_eq!(groups[0]["tracks"][0]["title"], "Small Weather");
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
    use percy_client::{MusicChannelRef, MusicHistoryItem, PlaylistSummary, PlaylistTrack, TopTrack};

    // Made-up sample data only: invented titles, no real artwork URLs (gradients render instead).
    const TRACKS: [(&str, &str); 10] = [
        ("Paper Lanterns", "The Quiet Hours"),
        ("Glass Harbour", "Mira Okafor"),
        ("Night Bus to Nowhere", "Static Orchard"),
        ("Copper Skies", "Lumen & Vale"),
        ("Small Weather", "June Arbor"),
        ("Rooftop Static", "Neon Pasture"),
        ("Saltwater Letters", "Hollow Pines"),
        ("Velvet Engine", "Kite Theory"),
        ("Second Sunrise", "Amber Lowlands"),
        ("Low Tide Radio", "The Foxglove Set"),
    ];

    /// A thumbnail URL in YouTube's shape with an invented id; the screenshot harness answers these
    /// with generated placeholder images, so no real artwork is ever referenced.
    fn fake_art(i: usize) -> String {
        format!("https://i.ytimg.com/vi/placeholder{i:02}/hqdefault.jpg")
    }

    #[test]
    fn covers_prefer_unletterboxed_thumbnails() {
        use crate::templates::{cover_chain, CoverSize};
        let url = "https://i.ytimg.com/vi/abc/hqdefault.jpg";
        let large = cover_chain(url, CoverSize::Large);
        assert_eq!(large[0], ("https://i.ytimg.com/vi/abc/maxresdefault.jpg".into(), false));
        assert!(large.iter().any(|(u, lb)| u.ends_with("sddefault.jpg") && *lb));
        assert_eq!(
            large.iter().filter(|(u, _)| u == url).count(),
            1,
            "the original is tried once"
        );
        assert_eq!(
            cover_chain(url, CoverSize::Small)[0].0,
            "https://i.ytimg.com/vi/abc/mqdefault.jpg"
        );
        let other = "https://cdn.example.com/a.png";
        assert_eq!(cover_chain(other, CoverSize::Large), vec![(other.to_string(), false)]);
    }

    fn chan(name: &str) -> Option<MusicChannelRef> {
        Some(MusicChannelRef {
            id: "9".into(),
            name: name.into(),
        })
    }

    fn music(playing: bool) -> MusicInfo {
        let queue: Vec<Value> = TRACKS[2..9]
            .iter()
            .enumerate()
            .map(|(i, (t, a))| json!({"title": t, "author": a, "duration": 150_000 + i as u64 * 17_000, "autoplay": i >= 5, "artwork": fake_art(i + 2), "artist_url": format!("https://example.com/artist/{a}")}))
            .collect();
        let mut m = json!({
            "active": playing, "equalizer": vec![0.0f64; 15], "filters": {}, "presets": ["Flat", "Bass boost", "Vocal"],
            "queue": if playing { queue } else { vec![] },
            "channel_name": "Lounge", "listeners": ["1", "2", "3", "4", "5"],
            "setup": {"channel_id": null, "message_id": null, "use_panel": true, "dj_mode": 2},
        });
        if playing {
            m["now_playing"] = json!({
                "title": TRACKS[0].0, "author": TRACKS[0].1, "duration": 214_000, "position": 81_000,
                "uri": "https://example.com/track/paper-lanterns", "volume": 64, "loop": 0, "shuffle": false,
                "requester": {"id": "7", "name": "Ferris"}, "artwork": fake_art(0),
                "artist_url": "https://example.com/artist/the-quiet-hours", "album": {"name": "Glass Tides", "url": "https://example.com/album/glass-tides"},
            });
        }
        serde_json::from_value(m).unwrap()
    }

    fn history() -> Vec<MusicHistoryItem> {
        TRACKS
            .iter()
            .enumerate()
            .map(|(i, (t, a))| MusicHistoryItem {
                title: (*t).into(),
                author: Some((*a).into()),
                uri: Some(format!("https://example.com/track/{i}")),
                artwork: (i % 3 != 2).then(|| fake_art(i)),
                length_ms: Some(170_000 + i as u64 * 9_000),
                played_at: format!("2026-10-25T1{}:00:00", i % 10),
                ..Default::default()
            })
            .collect()
    }

    fn library() -> UserMusicLibrary {
        UserMusicLibrary {
            top_tracks: TRACKS[3..10]
                .iter()
                .enumerate()
                .map(|(i, (t, a))| TopTrack {
                    title: (*t).into(),
                    author: Some((*a).into()),
                    uri: Some(format!("https://example.com/top/{i}")),
                    artwork: (i % 2 == 0).then(|| fake_art(i + 3)),
                    length_ms: Some(190_000 + i as u64 * 11_000),
                    plays: 42 - i as u64 * 5,
                })
                .collect(),
            liked: vec![
                PlaylistTrack {
                    name: "Glass Harbour".into(),
                    url: "https://example.com/track/1".into(),
                },
                PlaylistTrack {
                    name: "Copper Skies".into(),
                    url: "https://example.com/top/0".into(),
                },
            ],
            playlists: vec![
                PlaylistSummary {
                    id: "11".into(),
                    name: "Late night drive".into(),
                    track_count: 24,
                },
                PlaylistSummary {
                    id: "12".into(),
                    name: "Focus mode".into(),
                    track_count: 9,
                },
            ],
            ..Default::default()
        }
    }

    fn guilds(playing: bool) -> (Vec<GuildPick>, GuildPick) {
        let home = GuildPick {
            id: "5".into(),
            name: "Klappstuhl Lounge".into(),
            icon: None,
            playing,
        };
        let other = GuildPick {
            id: "6".into(),
            name: "Study Hall".into(),
            icon: None,
            playing: false,
        };
        (vec![home.clone(), other], home)
    }

    fn template(is_manager: bool, is_admin: bool) -> MusicAppTemplate {
        let (guilds, selected) = guilds(true);
        MusicAppTemplate {
            account: None,
            page_title: "Music",
            guilds,
            selected: Some(selected),
            viewer: Some(MusicViewer {
                is_manager,
                role: "listener".into(),
                ..Default::default()
            }),
            music: Some(music(true)),
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
        assert!(plain.contains("Paper Lanterns") && plain.contains(r#"data-role="listener""#));
        assert!(!plain.contains("setup-section"));
        // The sound panel is for every member, not an admin form.
        assert!(plain.contains("id=\"mapp-sound\"") && plain.contains(r#"data-act="sound""#));

        // A DJ (manager) without Manage Server sees no admin form: those routes would refuse them.
        let dj = template(true, false).render().unwrap();
        assert!(!dj.contains("setup-section") && !dj.contains("dj-mode-section"));

        let admin = template(true, true).render().unwrap();
        assert!(admin.contains("dj-mode-section") && admin.contains("setup-section"));
    }

    #[test]
    fn visitor_controls_are_locked_with_a_reason() {
        let mut t = template(false, false);
        t.viewer = Some(MusicViewer {
            role: "visitor".into(),
            bot_channel: chan("Lounge"),
            ..Default::default()
        });
        let html = t.render().unwrap();
        assert!(html.contains(r#"title="Join #Lounge to control" disabled"#));
        assert!(html.contains("You are listening in"));
    }

    /// Renders three representative states to `target/music-preview/` for visual review.
    #[test]
    fn write_music_previews() {
        let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("target/music-preview");
        std::fs::create_dir_all(&dir).unwrap();
        let account = || Some(Viewer { name: "Ferris".into() });
        let page = |viewer: MusicViewer, playing: bool, lib: UserMusicLibrary, admin: bool| {
            let (guilds, selected) = guilds(playing);
            MusicAppTemplate {
                account: account(),
                page_title: "Music",
                guilds,
                selected: Some(selected),
                viewer: Some(viewer),
                music: Some(music(playing)),
                history: history(),
                library: lib,
                channels: vec![],
                manager_view: admin,
                is_admin: admin,
            }
        };

        // Visitor: a server picked, nothing playing, not in voice.
        let visitor = page(
            MusicViewer {
                role: "visitor".into(),
                ..Default::default()
            },
            false,
            UserMusicLibrary::default(),
            false,
        );
        // Listener: in voice with Percy, a track playing, queue + history + library.
        let listener = page(
            MusicViewer {
                role: "listener".into(),
                can_control: true,
                dj_mode: 2,
                voice_channel: chan("Lounge"),
                bot_channel: chan("Lounge"),
                ..Default::default()
            },
            true,
            library(),
            false,
        );
        // Admin: DJ + Manage Server on the guild route.
        let admin = page(
            MusicViewer {
                role: "dj".into(),
                can_start: true,
                can_control: true,
                can_destroy: true,
                is_manager: true,
                dj_mode: 2,
                voice_channel: chan("Lounge"),
                bot_channel: chan("Lounge"),
            },
            true,
            library(),
            true,
        );

        // New user: in voice, the server has history, but the user has none of their own yet.
        let newuser = page(
            MusicViewer {
                role: "listener".into(),
                can_start: true,
                can_control: true,
                voice_channel: chan("Lounge"),
                bot_channel: chan("Lounge"),
                ..Default::default()
            },
            true,
            UserMusicLibrary::default(),
            false,
        );

        for (name, t) in [
            ("visitor", visitor),
            ("listener", listener),
            ("admin", admin),
            ("newuser", newuser),
        ] {
            let html = t.render().unwrap();
            assert!(html.contains("mapp-bar"));
            std::fs::write(dir.join(format!("{name}.html")), html).unwrap();
        }
    }
}
