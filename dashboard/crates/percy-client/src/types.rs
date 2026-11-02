//! Typed response models for Percy's internal API.

use crate::generated::LevelingConfig;
#[cfg(test)]
use crate::generated::PlaylistDetail;
use crate::generated::{AiChannelOverride, AiConfig, ChannelRef}; // guild domain
use crate::generated::{MemberDetail, MemberRoleBadge, MemberSelf};
use crate::generated::{PlaylistSummary, PlaylistTrack};
use crate::MusicHistoryItem;
use serde::{Deserialize, Serialize};

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
    use crate::{MusicFiltersState, NowPlaying};

    #[test]
    fn generated_music_types_keep_the_js_keys() {
        let f: MusicFiltersState = serde_json::from_str(r#"{"nightcore":false,"8d":true,"lowpass":null}"#).unwrap();
        let v = serde_json::to_value(&f).unwrap();
        assert_eq!(v["8d"], true);
        assert!(v["lowpass"].is_null());
        let n: NowPlaying = serde_json::from_str(
            r#"{"title":"T","author":"A","uri":null,"artwork":null,"duration":1,"is_stream":false,"source":null,"requester":null,"artist_url":null,"album":null,"autoplay":0,"playlist":null,"recommended":false,"isrc":null,"position":0,"paused":false,"volume":70,"loop":2,"shuffle":false}"#,
        )
        .unwrap();
        assert_eq!(serde_json::to_value(&n).unwrap()["loop"], 2);
    }

    #[test]
    fn null_author_and_length_tolerated() {
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

// -- Custom Bot Profile types ------------------------------------------------

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
    use crate::generated::{
        ActionRequestList, NotificationList, NotificationSummary, RequestDecision, RequestSettings,
    };

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
