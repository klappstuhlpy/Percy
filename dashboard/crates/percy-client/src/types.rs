//! Typed response models for Percy's internal API.

use serde::{Deserialize, Serialize};
use crate::generated::{CardOption, LevelingConfig};
use std::collections::HashMap;
use crate::generated::{AiChannelOverride, AiConfig, ChannelRef, RoleRef}; // guild domain
use crate::generated::{PlaylistSummary, PlaylistTrack};
#[cfg(test)]
use crate::generated::PlaylistDetail;
use crate::generated::{MemberDetail, MemberRoleBadge, MemberSelf};

/// One controlled feature in an override's summary (for the dashboard table).
pub struct AiFeatureState {
    pub label: &'static str,
    pub on: bool,
}

impl AiChannelOverride {
    /// The features this channel actually overrides, with their on/off value, for display.
    pub fn summary(&self) -> Vec<AiFeatureState> {
        let pairs = [
            ("Assistant", self.controlled.assistant, self.enabled.assistant),
            ("Router", self.controlled.router, self.enabled.router),
            ("Moderation", self.controlled.moderation, self.enabled.moderation),
            ("Sentinel", self.controlled.sentinel, self.enabled.sentinel),
            ("Music", self.controlled.music, self.enabled.music),
            ("Polls", self.controlled.polls, self.enabled.polls),
            ("Giveaways", self.controlled.giveaways, self.enabled.giveaways),
            ("Tags", self.controlled.tags, self.enabled.tags),
            ("Reminders", self.controlled.reminders, self.enabled.reminders),
        ];
        pairs
            .into_iter()
            .filter(|&(_, controlled, _)| controlled)
            .map(|(label, _, on)| AiFeatureState { label, on })
            .collect()
    }
}

impl AiConfig {
    /// The per-channel overrides serialized as JSON, for the editor's `window.AI_OVERRIDES`.
    pub fn overrides_json(&self) -> String {
        serde_json::to_string(&self.overrides).unwrap_or_else(|_| "[]".to_string())
    }
}


// -- Leveling types ----------------------------------------------------------

/// `{ok, message}` answer of an action that reports what it did in words.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ActionMessage {
    #[serde(default)]
    pub ok: bool,
    #[serde(default)]
    pub message: String,
}


// -- Member detail (user lookup) ---------------------------------------------

impl MemberRoleBadge {
    pub fn color_hex(&self) -> String {
        format!("#{:06x}", self.color)
    }
}

#[derive(Debug, Deserialize, Serialize)]
pub struct CommandUsageEntry {
    pub command: String,
    pub uses: i64,
}

impl MemberDetail {
    pub fn top_role_color_hex(&self) -> String {
        format!("#{:06x}", self.top_role_color)
    }
}

impl MemberSelf {
    pub fn top_role_color_hex(&self) -> String {
        format!("#{:06x}", self.top_role_color)
    }
}

impl LevelingConfig {
    /// What the pages show when Percy is unreachable: the server-side defaults of an unconfigured guild.
    pub fn unconfigured() -> Self {
        serde_json::from_value(serde_json::json!({ "enabled": false })).unwrap_or_default()
    }
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Serialize)]
pub struct UserSettingsPatch {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timezone: Option<Option<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub track_presence: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub track_history: Option<bool>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct PresenceEntry {
    pub status: Option<String>,
    pub status_before: Option<String>,
    pub changed_at: Option<String>,
}

// -- Polls types -------------------------------------------------------------

// -- Giveaways types ---------------------------------------------------------

// -- Tags types --------------------------------------------------------------

/// One `(name, content)` row in a tag export/import payload.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct TagExportRow {
    pub name: String,
    pub content: String,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct TagExport {
    pub tags: Vec<TagExportRow>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct TagImportFailure {
    pub name: String,
    pub error: String,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct TagImportResult {
    #[serde(default)]
    pub created: u32,
    #[serde(default)]
    pub skipped: u32,
    #[serde(default)]
    pub failed: Vec<TagImportFailure>,
}

/// A tag owned by a user, shown on their profile page.
#[derive(Debug, Deserialize, Serialize)]
pub struct OwnedTag {
    pub id: i64,
    pub name: String,
    #[serde(default)]
    pub uses: u32,
}

// -- Commands types ----------------------------------------------------------

#[derive(Debug, Deserialize, Serialize)]
pub struct CommandInfo {
    pub name: String,
    #[serde(default)]
    pub category: String,
    #[serde(default)]
    pub cog: Option<String>,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub signature: Option<String>,
    #[serde(default)]
    pub disabled_in: Vec<String>,
    #[serde(default)]
    pub globally_disabled: bool,
    /// Canonical Discord permission flag names the command requires out of the box
    /// (its built-in `PermissionTemplate` gate). Empty for ungated / owner-only commands.
    #[serde(default)]
    pub default_permissions: Vec<String>,
    /// The per-guild permission override, if an admin has set one.
    #[serde(default)]
    pub permission_override: Option<PermissionOverride>,
    // -- Fields of `GET /commands/public` (the website's commands page) --
    #[serde(default)]
    pub aliases: Vec<String>,
    /// Hybrid command: also available as a slash command.
    #[serde(default)]
    pub slash: bool,
    #[serde(default)]
    pub user_permissions: Vec<String>,
    #[serde(default)]
    pub bot_permissions: Vec<String>,
    #[serde(default)]
    pub cooldown: Option<Cooldown>,
    #[serde(default)]
    pub example: Option<String>,
    /// Full docstring (`description` is only its first line).
    #[serde(default)]
    pub help: Option<String>,
    /// Qualified name of the parent group, if this is a subcommand.
    #[serde(default)]
    pub parent: Option<String>,
}

/// `rate` uses per `per` seconds.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Cooldown {
    pub rate: u32,
    pub per: f64,
}

/// A per-guild override of a command's permission gate. `permissions` is a Discord
/// permission bitmask that *replaces* the required user permissions (`None` keeps the
/// command's default gate); members holding any `allowed_roles` may always run it.
#[derive(Debug, Deserialize, Serialize)]
pub struct PermissionOverride {
    #[serde(default)]
    pub permissions: Option<i64>,
    #[serde(default)]
    pub allowed_roles: Vec<String>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct PublicCommandsResponse {
    pub commands: Vec<CommandInfo>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct PlonkEntry {
    pub entity_id: String,
    #[serde(rename = "type")]
    pub entity_type: String,
    pub name: String,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct CommandsResponse {
    pub commands: Vec<CommandInfo>,
    pub plonks: Vec<PlonkEntry>,
}

// -- Stats types -------------------------------------------------------------

// -- Batch operations --------------------------------------------------------

#[derive(Debug, Serialize)]
pub struct BatchOperation {
    #[serde(rename = "type")]
    pub op_type: String,
    pub data: serde_json::Value,
}

#[derive(Debug, Deserialize)]
pub struct BatchResponse {
    pub ok: bool,
    pub results: Vec<BatchResult>,
}

#[derive(Debug, Deserialize)]
pub struct BatchResult {
    #[serde(rename = "type")]
    pub op_type: String,
    pub ok: bool,
    #[serde(default)]
    pub error: Option<String>,
    /// Advice about a saved `sentinel` op (absent from older Percy builds and other ops).
    #[serde(default)]
    pub warnings: Vec<String>,
}

/// What Percy answers a sentinel save or verification-message send with.
#[derive(Debug, Deserialize)]
pub struct SentinelSaved {
    /// Set by `/sentinel/message` only.
    #[serde(default)]
    pub message_id: u64,
    #[serde(default)]
    pub warnings: Vec<String>,
}

// -- Changelog ---------------------------------------------------------------

// -- Composite endpoint types ------------------------------------------------

// -- Economy types -----------------------------------------------------------


// -- Music types -------------------------------------------------------------

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct TrackLink {
    pub name: String,
    #[serde(default)]
    pub url: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct TrackRequester {
    pub id: String,
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub avatar: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct NowPlaying {
    pub title: String,
    pub author: String,
    pub duration: u64,
    pub position: u64,
    #[serde(default)]
    pub uri: Option<String>,
    #[serde(default)]
    pub artwork: Option<String>,
    #[serde(default)]
    pub source: Option<String>,
    #[serde(default)]
    pub is_stream: bool,
    #[serde(default)]
    pub paused: bool,
    #[serde(default)]
    pub volume: u32,
    /// 0 = off, 1 = loop track, 2 = loop queue.
    /// Percy sends/expects the JSON key `loop`; rename so it both deserialises
    /// from Percy and re-serialises as `loop` for the dashboard player JS.
    #[serde(default, rename = "loop")]
    pub loop_mode: u8,
    #[serde(default)]
    pub shuffle: bool,
    #[serde(default)]
    pub recommended: bool,
    #[serde(default)]
    pub album: Option<TrackLink>,
    #[serde(default)]
    pub playlist: Option<TrackLink>,
    #[serde(default)]
    pub artist_url: Option<String>,
    #[serde(default)]
    pub requester: Option<TrackRequester>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct QueueTrack {
    pub title: String,
    pub author: String,
    #[serde(default)]
    pub uri: Option<String>,
    #[serde(default)]
    pub artwork: Option<String>,
    #[serde(default)]
    pub duration: u64,
    #[serde(default)]
    pub is_stream: bool,
    #[serde(default)]
    pub requester: Option<TrackRequester>,
    /// True for wavelink autoplay recommendations queued after the manual queue.
    #[serde(default)]
    pub autoplay: bool,
    /// Link to the (first) artist's page at the source; None for sources without one.
    #[serde(default)]
    pub artist_url: Option<String>,
    /// The track's album, None when the source (e.g. YouTube) has none.
    #[serde(default)]
    pub album: Option<TrackLink>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct LyricLine {
    pub time: u64,
    pub text: String,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct MusicLyrics {
    #[serde(default)]
    pub has_synced: bool,
    #[serde(default)]
    pub title: Option<String>,
    #[serde(default)]
    pub source: Option<String>,
    #[serde(default)]
    pub lines: Vec<LyricLine>,
    #[serde(default)]
    pub plain: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct MusicFiltersState {
    #[serde(default)]
    pub nightcore: bool,
    #[serde(rename = "8d", default)]
    pub eight_d: bool,
    #[serde(default)]
    pub lowpass: Option<f64>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct MusicSetup {
    /// The dedicated panel channel, if one is configured. `None` means the panel
    /// (when enabled) is created temporarily where playback starts.
    #[serde(default)]
    pub channel_id: Option<String>,
    pub message_id: Option<String>,
    pub use_panel: bool,
    #[serde(default)]
    pub dj_mode: u8,
}

#[derive(Debug, Default, Deserialize, Serialize)]
pub struct AlwaysOnState {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default)]
    pub mode: Option<String>,
    #[serde(default)]
    pub source: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct MusicInfo {
    pub active: bool,
    pub equalizer: Vec<f64>,
    pub filters: MusicFiltersState,
    pub presets: Vec<String>,
    #[serde(default)]
    pub now_playing: Option<NowPlaying>,
    #[serde(default)]
    pub queue: Vec<QueueTrack>,
    /// Recently played tracks (most-recent-first) for the History tab.
    #[serde(default)]
    pub history: Vec<QueueTrack>,
    #[serde(default)]
    pub channel: Option<String>,
    #[serde(default)]
    pub channel_name: Option<String>,
    #[serde(default)]
    pub setup: Option<MusicSetup>,
    #[serde(default)]
    pub always_on: AlwaysOnState,
    /// Discord IDs of the (non-bot) members currently sharing the bot's voice
    /// channel. The public overview uses this to decide whether a viewer may
    /// control playback; Percy re-verifies on every control request.
    #[serde(default)]
    pub listeners: Vec<String>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct MusicSetupResponse {
    pub ok: bool,
    pub channel_id: String,
    pub channel_name: String,
}

/// A track as Percy's `_serialize_track` sends it; the live queue uses the same shape.
pub type MusicTrack = QueueTrack;

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct MusicChannelRef {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub name: String,
}

/// Equalizer + filters (`GET`/`POST /music/audio`). `filters` stays free-form JSON: the panel reads it directly.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct MusicAudio {
    #[serde(default)]
    pub active: bool,
    #[serde(default)]
    pub equalizer: MusicEqualizer,
    #[serde(default)]
    pub filters: serde_json::Value,
    #[serde(default)]
    pub effects: Vec<String>,
    #[serde(default)]
    pub presets: serde_json::Value,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct MusicEqualizer {
    #[serde(default)]
    pub preset: Option<String>,
    #[serde(default)]
    pub bands: Vec<f64>,
}

/// What one viewer may do with a guild's player (`GET /music/viewer`).
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct MusicViewer {
    #[serde(default)]
    pub role: String,
    #[serde(default)]
    pub can_start: bool,
    #[serde(default)]
    pub can_control: bool,
    #[serde(default)]
    pub can_destroy: bool,
    #[serde(default)]
    pub is_manager: bool,
    #[serde(default)]
    pub dj_mode: u8,
    #[serde(default)]
    pub voice_channel: Option<MusicChannelRef>,
    #[serde(default)]
    pub bot_channel: Option<MusicChannelRef>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct MusicSearch {
    #[serde(default)]
    pub results: Vec<MusicTrack>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct MusicArtistInfo {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub artwork: Option<String>,
    #[serde(default)]
    pub url: Option<String>,
}

/// An album or single in an artist's releases; `year`/`track_count` are None when Lavalink gives none.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct MusicRelease {
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub year: Option<u32>,
    #[serde(default)]
    pub artwork: Option<String>,
    #[serde(default)]
    pub url: Option<String>,
    #[serde(default)]
    pub track_count: Option<u32>,
}

/// `GET /music/artist`.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct MusicArtist {
    #[serde(default)]
    pub artist: MusicArtistInfo,
    #[serde(default)]
    pub top_tracks: Vec<MusicTrack>,
    #[serde(default)]
    pub releases: Vec<MusicRelease>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct MusicAlbumInfo {
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub year: Option<u32>,
    #[serde(default)]
    pub artists: Vec<String>,
    #[serde(default)]
    pub artwork: Option<String>,
    #[serde(default)]
    pub url: Option<String>,
    #[serde(default)]
    pub total_ms: u64,
}

/// `GET /music/album`.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct MusicAlbum {
    #[serde(default)]
    pub album: MusicAlbumInfo,
    #[serde(default)]
    pub tracks: Vec<MusicTrack>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct MusicPlayResult {
    #[serde(default)]
    pub queued: Vec<MusicTrack>,
    #[serde(default)]
    pub started: bool,
}

/// One `music_plays` row (guild history and a user's recent plays).
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct MusicHistoryItem {
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub author: Option<String>,
    #[serde(default)]
    pub uri: Option<String>,
    #[serde(default)]
    pub artwork: Option<String>,
    #[serde(default)]
    pub length_ms: Option<u64>,
    #[serde(default)]
    pub user_id: Option<String>,
    #[serde(default)]
    pub played_at: String,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct MusicHistory {
    #[serde(default)]
    pub items: Vec<MusicHistoryItem>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct TopTrack {
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub author: Option<String>,
    #[serde(default)]
    pub uri: Option<String>,
    #[serde(default)]
    pub artwork: Option<String>,
    #[serde(default)]
    pub length_ms: Option<u64>,
    #[serde(default)]
    pub plays: u64,
}

/// One chart entry: a track and how many different people picked it (and how often).
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct TrendingTrack {
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub author: Option<String>,
    #[serde(default)]
    pub uri: Option<String>,
    #[serde(default)]
    pub artwork: Option<String>,
    #[serde(default)]
    pub length_ms: Option<u64>,
    #[serde(default)]
    pub listeners: u32,
    #[serde(default)]
    pub plays: u32,
}

/// `GET .../music/trending`: this guild's chart and the one across every guild.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct MusicTrending {
    #[serde(default)]
    pub server: Vec<TrendingTrack>,
    #[serde(default)]
    pub global: Vec<TrendingTrack>,
}

/// A track to save: what the dashboard knows about it at the moment it is liked or added.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct SaveTrack {
    pub name: String,
    pub url: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub author: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub artwork: Option<String>,
}

/// `PATCH .../music/playlists/{id}`: `None` leaves a field alone; an empty thumbnail clears it.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct PlaylistPatch {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub thumbnail: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct UserMusicLibrary {
    #[serde(default)]
    pub top_tracks: Vec<TopTrack>,
    #[serde(default)]
    pub recent: Vec<MusicHistoryItem>,
    #[serde(default)]
    pub liked: Vec<PlaylistTrack>,
    #[serde(default)]
    pub playlists: Vec<PlaylistSummary>,
}

#[cfg(test)]
mod music_app_tests {
    use super::*;

    #[test]
    fn audio_parses_and_defaults() {
        let a: MusicAudio = serde_json::from_str(r#"{"active":true,"equalizer":{"preset":"pop","bands":[0.1,0.2]},"filters":{"timescale":{"speed":1.25}},"effects":["nightcore"],"presets":{"equalizer":["flat"],"filters":["8d"]}}"#).unwrap();
        assert!(a.active && a.equalizer.preset.as_deref() == Some("pop") && a.effects == ["nightcore"]);
        let d: MusicAudio = serde_json::from_str("{}").unwrap();
        assert!(!d.active && d.equalizer.bands.is_empty() && d.effects.is_empty());
    }

    #[test]
    fn viewer_parses_and_defaults() {
        let v: MusicViewer = serde_json::from_str(r#"{"role":"dj","can_start":true,"can_control":true,"can_destroy":false,"is_manager":false,"dj_mode":1,"voice_channel":{"id":"5","name":"Music"},"bot_channel":null}"#).unwrap();
        assert!(v.can_start && v.voice_channel.unwrap().name == "Music" && v.bot_channel.is_none());
        let d: MusicViewer = serde_json::from_str("{}").unwrap();
        assert!(!d.can_control && d.role.is_empty());
    }

    #[test]
    fn search_play_history_parse() {
        let t = r#"{"title":"T","author":"A","uri":"u","artwork":null,"duration":1000,"is_stream":false,"source":"youtube","requester":null,"autoplay":false}"#;
        let s: MusicSearch = serde_json::from_str(&format!(r#"{{"results":[{t}]}}"#)).unwrap();
        assert_eq!(s.results[0].title, "T");
        let p: MusicPlayResult = serde_json::from_str(&format!(r#"{{"queued":[{t}],"started":true}}"#)).unwrap();
        assert!(p.started && p.queued.len() == 1);
        let h: MusicHistory = serde_json::from_str(r#"{"items":[{"title":"T","author":"A","uri":null,"artwork":null,"length_ms":5,"user_id":"9","played_at":"2026-10-25T10:00:00"}]}"#).unwrap();
        assert_eq!(h.items[0].user_id.as_deref(), Some("9"));
        assert!(serde_json::from_str::<MusicSearch>("{}").unwrap().results.is_empty());
        assert!(!serde_json::from_str::<MusicPlayResult>("{}").unwrap().started);
        assert!(serde_json::from_str::<MusicHistory>("{}").unwrap().items.is_empty());
    }

    #[test]
    fn artist_and_album_parse() {
        let t = r#"{"title":"T","author":"A","artist_url":"https://x.test/a","album":{"name":"Al","url":null}}"#;
        let a: MusicArtist = serde_json::from_str(&format!(
            r#"{{"artist":{{"name":"A","artwork":null,"url":null}},"top_tracks":[{t}],"releases":[{{"title":"Al","kind":"single","year":null,"artwork":"a","url":"u","track_count":null}}]}}"#
        ))
        .unwrap();
        assert_eq!(a.top_tracks[0].album.as_ref().unwrap().name, "Al");
        assert_eq!(a.top_tracks[0].artist_url.as_deref(), Some("https://x.test/a"));
        assert_eq!(a.releases[0].kind, "single");
        let b: MusicAlbum = serde_json::from_str(&format!(
            r#"{{"album":{{"title":"Al","artists":["A"],"total_ms":5}},"tracks":[{t}]}}"#
        ))
        .unwrap();
        assert_eq!(b.album.total_ms, 5);
        assert!(serde_json::from_str::<MusicArtist>("{}").unwrap().releases.is_empty());
        assert!(serde_json::from_str::<MusicAlbum>("{}").unwrap().tracks.is_empty());
    }

    #[test]
    fn null_author_and_length_tolerated() {
        let h: MusicHistory =
            serde_json::from_str(r#"{"items":[{"title":"T","author":null,"length_ms":null}]}"#).unwrap();
        assert!(h.items[0].author.is_none() && h.items[0].length_ms.is_none());
        let l: UserMusicLibrary =
            serde_json::from_str(r#"{"top_tracks":[{"title":"T","author":null,"length_ms":null,"plays":1}]}"#).unwrap();
        assert!(l.top_tracks[0].author.is_none() && l.top_tracks[0].length_ms.is_none());
    }

    #[test]
    fn library_and_playlist_parse() {
        let l: UserMusicLibrary = serde_json::from_str(r#"{"top_tracks":[{"title":"T","author":"A","uri":"u","artwork":null,"length_ms":5,"plays":3}],"recent":[],"liked":[{"name":"n","url":"u","author":null,"artwork":null}],"playlists":[{"id":"7","name":"P","track_count":2,"thumbnail":null,"cover":null}]}"#).unwrap();
        assert_eq!(
            (l.top_tracks[0].plays, l.liked.len(), l.playlists[0].track_count),
            (3, 1, 2)
        );
        assert!(serde_json::from_str::<UserMusicLibrary>("{}")
            .unwrap()
            .playlists
            .is_empty());
        let d: PlaylistDetail =
            serde_json::from_str(r#"{"id":"7","name":"P","thumbnail":null,"cover":null,"tracks":[{"name":"n","url":"u","author":null,"artwork":null}]}"#).unwrap();
        assert_eq!(d.tracks[0].url, "u");
    }
}

// -- Comics types ------------------------------------------------------------

// -- Temp Channels types -----------------------------------------------------

// -- Status Feed types -------------------------------------------------------

#[derive(Debug, Deserialize, Serialize)]
pub struct StatusFeedInfo {
    pub subscribed: bool,
    pub channel: Option<ChannelRef>,
}

// -- Lockdowns types ---------------------------------------------------------

// -- Emoji Stats types -------------------------------------------------------

// -- Audit log (cases) types -------------------------------------------------

// -- Activity heatmap types --------------------------------------------------

// -- Bulk action types -------------------------------------------------------

// -- Guild image gallery types -----------------------------------------------

/// A single image in a guild's shared gallery (metadata only; the bytes live at
/// `url` / `raw_url` on the klappstuhl.me hoster).
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct GalleryImage {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub ext: String,
    #[serde(default)]
    pub mimetype: String,
    #[serde(default)]
    pub size: i64,
    #[serde(default)]
    pub original_name: Option<String>,
    #[serde(default)]
    pub uploaded_at: String,
    /// Canonical landing-page URL.
    #[serde(default)]
    pub url: String,
    /// Raw-bytes URL (used for `<img>` thumbnails).
    #[serde(default)]
    pub raw_url: String,
}

/// The listing of a guild's gallery.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct GalleryResponse {
    #[serde(default)]
    pub images: Vec<GalleryImage>,
    #[serde(default)]
    pub total: usize,
}

/// The result of a gallery upload (mirrors the hoster's `UploadResult`).
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct GalleryUploadResult {
    #[serde(default)]
    pub total: usize,
    #[serde(default)]
    pub errors: usize,
    #[serde(default)]
    pub skipped: usize,
    #[serde(default)]
    pub infected: usize,
    #[serde(default)]
    pub links: Vec<String>,
    #[serde(default)]
    pub raw_links: Vec<String>,
}

// -- Custom Bot Profile types ------------------------------------------------

#[derive(Debug, Default, Deserialize, Serialize)]
pub struct CustomBotProfile {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub avatar_url: Option<String>,
    #[serde(default)]
    pub banner_url: Option<String>,
    #[serde(default)]
    pub about_me: Option<String>,
    #[serde(default)]
    pub accent_color: Option<String>,
}

// -- Outbound webhook (event subscription) types -----------------------------

// -- Backup & template types -------------------------------------------------

// -- Watchlist types ---------------------------------------------------------

// -- Comic catalogue types -----------------------------------------------------
//
// The public comic-release catalogue behind percy-dashboard's comic pages --
// distinct from `ComicFeedEntry`/`ComicsResponse` above, which are the
// guild-scoped feed *subscriptions* a server can push new releases to.

/// Every filter `GET /comics` accepts, as one borrowed struct.
///
/// Seven of these are `Option<&str>`, so passing them positionally lets a caller swap
/// `creator` and `character` and still compile. Build it from `Default` and set only what
/// you mean:
///
/// ```ignore
/// let page = percy
///     .list_comics(&ComicBrowseQuery { brand: Some("MARVEL"), page: Some(2), ..Default::default() })
///     .await?;
/// ```
#[derive(Debug, Clone, Default)]
pub struct ComicBrowseQuery<'a> {
    pub brand: Option<&'a str>,
    pub series: Option<&'a str>,
    pub creator: Option<&'a str>,
    pub character: Option<&'a str>,
    /// ISO date (`YYYY-MM-DD`); releases on or after it.
    pub since: Option<&'a str>,
    /// ISO date; releases on or before it.
    pub until: Option<&'a str>,
    /// Substring match on the title.
    pub search: Option<&'a str>,
    /// 1-based.
    pub page: Option<i64>,
    /// Percy caps this at 100.
    pub per_page: Option<i64>,
}

// -- Release subscription types -------------------------------------------------

/// A follow, as `PUT /releases/users/{id}/subscriptions` takes it.
///
/// One borrowed struct rather than seven positional arguments: `subject_type`,
/// `subject_value` and `subject_label` are all strings, so a positional signature lets a
/// caller transpose them and still compile — and sending the *label* as the value creates a
/// subscription the fan-out can never match. The two `Option`s are omitted when unset so
/// Percy applies its own defaults (`streams = releases`, `delivery = dm`).
#[derive(Debug, Clone, Default, Serialize)]
pub struct NewSubscription<'a> {
    pub media: &'a str,
    pub subject_type: &'a str,
    /// The opaque key — a picker `subject_value`, not a label.
    pub subject_value: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub subject_label: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub streams: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub delivery: Option<&'a str>,
    /// The guild the follow was created from, if any.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub guild_id: Option<u64>,
}

/// The identifying triple plus whichever mutable field is changing.
///
/// At least one of `streams` / `delivery` must be set, or Percy answers `400`.
#[derive(Debug, Clone, Default, Serialize)]
pub struct SubscriptionPatch<'a> {
    pub media: &'a str,
    pub subject_type: &'a str,
    pub subject_value: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub streams: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub delivery: Option<&'a str>,
}

// -- Release overview -----------------------------------------------------------

// -- Progress ------------------------------------------------------------------
//
// `release_progress` (V42) — one table for both media, keyed by the same
// `(media, release_key)` identity the cards and the DM delivery claim use. V39's
// `watch_progress` is a different table behind `get_watch_progress` above, keyed by
// title id, and the two are deliberately not merged here.

/// `{media: {release_key: mark}}`. Percy always sends **both** media keys, so a renderer
/// can index by a card's `media` without a guard — but `get` is still the safe read here,
/// because a `#[serde(default)]` on an absent field yields an empty map.
pub type ProgressMap =
    std::collections::HashMap<String, std::collections::HashMap<String, crate::generated::ProgressMark>>;

/// One mark, as `PUT /releases/users/{id}/progress` takes it.
///
/// A borrowed struct rather than positional arguments for the same reason
/// [`NewSubscription`] is one: `media`, `release_key` and `status` are all strings, and
/// Percy **rejects** a status that does not belong to its medium (400) instead of
/// translating it — a `watched` on a comic means the key is probably wrong too.
///
/// `release_key` is the card's `key` verbatim (`{brand}/{slug}` / `{universe}/{slug}`),
/// never composed from parts by the caller.
#[derive(Debug, Clone, Default, Serialize)]
pub struct ProgressEntry<'a> {
    pub media: &'a str,
    pub release_key: &'a str,
    /// `read` (comic) / `watched` (title) / `skipped` (either).
    pub status: &'a str,
    /// The client's own timestamp, which is what makes the merge last-write-wins.
    /// Omitted when unset, and Percy stamps it itself.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub updated_at: Option<&'a str>,
}

// -- News types ------------------------------------------------------------------
//
// The news stream behind `/news`, the ticker and the detail pages' related strip.
// Every item is deliberately thin: Percy caps the excerpt on the way in and never
// stores the article body, so a client physically cannot render more than the plan's
// §9 allows (headline, short excerpt, link, source).

/// Every filter `GET /news` accepts, as one borrowed struct.
///
/// Same reasoning as [`ComicBrowseQuery`]: six of these are `Option<&'a str>`, so a
/// positional signature lets a caller swap `source` and `search` and still compile.
///
/// `subject_type` and `subject_value` are a **pair** — Percy ignores a lone one
/// rather than erroring, so a page that clears its filter by blanking one input
/// shows everything instead of a `400`.
///
/// ```ignore
/// let page = percy
///     .news(&NewsQuery { media: Some("comic"), per_page: Some(20), ..Default::default() })
///     .await?;
/// ```
#[derive(Debug, Clone, Default)]
pub struct NewsQuery<'a> {
    /// `comic` or `title`.
    pub media: Option<&'a str>,
    pub subject_type: Option<&'a str>,
    /// The opaque subject key, as the picker or a previous response gave it.
    pub subject_value: Option<&'a str>,
    /// A source slug.
    pub source: Option<&'a str>,
    /// ISO date or timestamp; stories published on or after it.
    pub since: Option<&'a str>,
    /// Substring match on the headline.
    pub search: Option<&'a str>,
    /// 1-based.
    pub page: Option<i64>,
    /// Percy caps this at 100.
    pub per_page: Option<i64>,
    /// Lead the list with what this Discord user follows. Without it the list is
    /// plain latest and `personalised` is `0` — never a `401`, so the same call
    /// serves the signed-out page.
    pub discord_id: Option<&'a str>,
}

// -- Guild health ------------------------------------------------------------

// -- Role menus ----------------------------------------------------------------

// -- Starboard -----------------------------------------------------------------

// -- Reminders -----------------------------------------------------------------

// -- Notifications and action requests -------------------------------------------

#[cfg(test)]
mod notification_tests {
    use crate::generated::{ActionRequestList, NotificationList, NotificationSummary, RequestDecision, RequestSettings};

    #[test]
    fn notification_list_reads_integer_ids() {
        let l: NotificationList = serde_json::from_str(r##"{"items":[{"id":42,"kind":"missing_permission","severity":"warning","title":"Missing permission in #logs","body":"x","link":"/dashboard/guild/1/logs","meta":{"channel_id":"555"},"created_at":"2026-10-21T12:30:00.123456Z","global":false,"unread":true}],"unread":1,"last_read_id":41}"##).unwrap();
        assert_eq!(l.items[0].id, 42);
        assert_eq!(l.last_read_id, 41);
        assert!(l.items[0].unread && !l.items[0].global);
    }

    #[test]
    fn summary_parses() {
        let s: NotificationSummary = serde_json::from_str(r#"{"unread":3,"pending_requests":2}"#).unwrap();
        assert_eq!((s.unread, s.pending_requests), (3, 2));
    }

    const REQ: &str = r#"{"id":"7","guild_id":"1","requester_id":"11","target_id":"20","action":"timeout","reason":"Spamming invites","duration":3600,"status":"pending","decided_by":null,"decision_note":null,"result":null,"channel_id":"555","message_id":"999","created_at":"2026-10-21T12:00:00Z","decided_at":null,"expires_at":"2026-10-22T12:00:00Z","requester_name":"Mod Mia","target_name":null,"decided_by_name":null}"#;

    #[test]
    fn request_list_parses() {
        let l: ActionRequestList = serde_json::from_str(&format!(r#"{{"items":[{REQ}]}}"#)).unwrap();
        let r = &l.items[0];
        assert_eq!((r.id.as_str(), r.duration), ("7", Some(3600)));
        assert_eq!(r.requester_name.as_deref(), Some("Mod Mia"));
        assert!(r.target_name.is_none());
    }

    #[test]
    fn decision_parses_with_and_without_request() {
        let d: RequestDecision = serde_json::from_str(&format!(
            r#"{{"ok":true,"status":"approved","message":"Approved and carried out.","request":{REQ}}}"#
        ))
        .unwrap();
        assert!(d.ok && d.request.is_some());
        let d: RequestDecision =
            serde_json::from_str(r#"{"ok":false,"status":"unknown","message":"Request not found.","request":null}"#)
                .unwrap();
        assert!(!d.ok && d.request.is_none());
    }

    #[test]
    fn settings_parse_set_and_unset() {
        let s: RequestSettings = serde_json::from_str(
            r#"{"channel":{"id":"555","name":"mod-requests","type":"text"},"role_ids":["300"],"roles":[{"id":"300","name":"Admin","color":15158332}]}"#,
        )
        .unwrap();
        assert_eq!(s.channel.unwrap().name, "mod-requests");
        assert_eq!(s.roles[0].color, 15158332);
        let s: RequestSettings = serde_json::from_str(r#"{"channel":null,"role_ids":[],"roles":[]}"#).unwrap();
        assert!(s.channel.is_none() && s.roles.is_empty());
    }
}

// -- Public-API support types --------------------------------------------------

// -- Warn policy types ---------------------------------------------------------
