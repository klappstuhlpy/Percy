//! Typed response models for Percy's internal API.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Deserialize, Serialize)]
pub struct UserGuild {
    pub id: String,
    pub name: String,
    pub icon_url: Option<String>,
    pub member_count: Option<u32>,
    pub owner: bool,
    /// True when the user has Administrator or Manage Server in this guild. The
    /// dashboard shows manageable guilds as full admin cards and the rest as
    /// read-only public overviews. Defaults to `false` for backward compat with
    /// an older Percy that only returned manageable guilds.
    #[serde(default)]
    pub manageable: bool,
    /// Percy has a track loaded in this guild (playing or paused).
    #[serde(default)]
    pub music_playing: bool,
}

/// A user's *current* avatar, resolved live from the bot (never persisted, so it
/// can't go stale).
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct UserAvatar {
    pub avatar_url: String,
    #[serde(default)]
    pub username: String,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ChannelRef {
    pub id: String,
    pub name: String,
    #[serde(rename = "type")]
    pub channel_type: String,
}

/// A real command the AI assistant named in its answer, surfaced as a chip in the
/// command palette. `command` is the bare qualified name; `label` includes the prefix.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct AiSuggestion {
    pub label: String,
    pub command: String,
}

/// Answer from Percy's dashboard AI assistant (`POST /guilds/{id}/ai/ask`).
///
/// `available` is `false` whenever the guild has the AI assistant disabled or the engine is
/// unreachable/timed out — in that case `answer` is `None` and `reason` carries a short, user-safe
/// explanation. The palette degrades to its navigation/action results and shows `reason`.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct AiAskResponse {
    pub available: bool,
    #[serde(default)]
    pub reason: Option<String>,
    #[serde(default)]
    pub answer: Option<String>,
    #[serde(default)]
    pub suggestions: Vec<AiSuggestion>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct RoleRef {
    pub id: String,
    pub name: String,
    pub color: u32,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct GuildInfo {
    pub id: u64,
    pub name: String,
    pub icon_url: Option<String>,
    pub member_count: Option<u32>,
    pub flags: GuildFlags,
    pub audit_log_channel: Option<ChannelRef>,
    #[serde(default)]
    pub mod_log_channel: Option<ChannelRef>,
    #[serde(default)]
    pub message_log_channel: Option<ChannelRef>,
    #[serde(default)]
    pub voice_log_channel: Option<ChannelRef>,
    pub poll_channel: Option<ChannelRef>,
    pub poll_ping_role: Option<RoleRef>,
    pub poll_reason_channel: Option<ChannelRef>,
    pub mention_count: Option<u32>,
    #[serde(default)]
    pub ignored_entities: Vec<IgnoredEntity>,
    pub mute_role: Option<RoleRef>,
    pub alert_channel: Option<ChannelRef>,
    #[serde(default)]
    pub audit_log_flags: std::collections::BTreeMap<String, bool>,
    pub music_panel_channel: Option<ChannelRef>,
    pub use_music_panel: bool,
    pub prefixes: Vec<String>,
    #[serde(default)]
    pub is_new_config: bool,
    #[serde(default)]
    pub ai: AiConfig,
}

/// Per-guild AI feature flags plus per-channel overrides (the AI-native rewrite).
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct AiConfig {
    #[serde(default)]
    pub flags: AiFlags,
    #[serde(default)]
    pub overrides: Vec<AiChannelOverride>,
}

/// The AI feature toggles. Mirrors Percy's `GuildConfig.AIFlags`; each field defaults off.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct AiFlags {
    #[serde(default)]
    pub assistant: bool,
    #[serde(default)]
    pub router: bool,
    #[serde(default)]
    pub moderation: bool,
    #[serde(default)]
    pub sentinel: bool,
    #[serde(default)]
    pub music: bool,
    #[serde(default)]
    pub polls: bool,
    #[serde(default)]
    pub giveaways: bool,
    #[serde(default)]
    pub tags: bool,
    #[serde(default)]
    pub reminders: bool,
}

/// A per-channel override: `controlled` marks which features the channel overrides,
/// and `enabled` holds their on/off value for those controlled features.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct AiChannelOverride {
    #[serde(default)]
    pub channel: Option<ChannelRef>,
    pub channel_id: String,
    #[serde(default)]
    pub controlled: AiFlags,
    #[serde(default)]
    pub enabled: AiFlags,
}

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

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct IgnoredEntity {
    pub id: String,
    #[serde(rename = "type")]
    pub entity_type: String,
    pub name: String,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct GuildFlags {
    pub audit_log: bool,
    pub raid: bool,
    pub alerts: bool,
    pub sentinel: bool,
    #[serde(default)]
    pub mentions: bool,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Role {
    pub id: String,
    pub name: String,
    pub color: u32,
    pub position: i32,
    pub permissions: u64,
    pub mentionable: bool,
    pub managed: bool,
    pub hoist: bool,
    pub icon_url: Option<String>,
    /// Percy can give this role out (below its top role, not integration-managed).
    #[serde(default = "default_true")]
    pub assignable: bool,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Channel {
    pub id: String,
    pub name: String,
    #[serde(rename = "type")]
    pub channel_type: String,
    pub position: i32,
    pub category_id: Option<String>,
    /// Percy can post an embed in this channel right now.
    #[serde(default = "default_true")]
    pub can_post: bool,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct Member {
    pub id: String,
    pub name: String,
    pub display_name: String,
    pub avatar_url: String,
    pub joined_at: Option<String>,
    pub roles: Vec<String>,
    pub bot: bool,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct MembersResponse {
    pub members: Vec<Member>,
    pub total: u32,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct SentinelInfo {
    pub channel: Option<ChannelRef>,
    pub role: Option<RoleRef>,
    pub message: Option<u64>,
    pub starter_role: Option<RoleRef>,
    pub bypass_action: String,
    pub rate: Option<String>,
    pub started_at: Option<String>,
    pub member_count: u32,
    pub needs_setup: bool,
}

// -- Leveling types ----------------------------------------------------------

#[derive(Debug, Deserialize, Serialize)]
pub struct LevelingConfig {
    pub enabled: bool,
    #[serde(default)]
    pub configured: bool,
    /// 0 = don't send, 1 = source channel, 2 = DM, else channel id.
    #[serde(default = "default_level_up_channel")]
    pub level_up_channel: i64,
    pub level_up_message: Option<String>,
    /// Map of level (as string) -> custom message.
    #[serde(default)]
    pub special_level_up_messages: HashMap<String, String>,
    #[serde(default)]
    pub blacklisted_roles: Vec<i64>,
    #[serde(default)]
    pub blacklisted_channels: Vec<i64>,
    #[serde(default)]
    pub blacklisted_users: Vec<i64>,
    /// Map of role id (as string) -> level threshold.
    #[serde(default)]
    pub level_roles: HashMap<String, i64>,
    /// Map of role id (as string) -> XP multiplier.
    #[serde(default)]
    pub multiplier_roles: HashMap<String, f64>,
    /// Map of channel id (as string) -> XP multiplier.
    #[serde(default)]
    pub multiplier_channels: HashMap<String, f64>,
    #[serde(default)]
    pub role_stack: bool,
    #[serde(default)]
    pub voice_enabled: bool,
    #[serde(default)]
    pub delete_after_leave: bool,
    #[serde(default = "default_factor")]
    pub factor: f64,
    #[serde(default = "default_base")]
    pub base: i64,
    #[serde(default = "default_min_gain")]
    pub min_gain: i64,
    #[serde(default = "default_max_gain")]
    pub max_gain: i64,
    #[serde(default = "default_cooldown_per")]
    pub cooldown_per: i64,
    /// The server's default rank card style: a partial map of preset keys.
    #[serde(default)]
    pub card_style: HashMap<String, String>,
    /// Members may not override the server's card style (the editor is hidden).
    #[serde(default)]
    pub card_locked: bool,
    /// Every preset per field (`theme`, `accent`, `bar`, `font`).
    #[serde(default)]
    pub card_options: HashMap<String, Vec<CardOption>>,
    /// The `{placeholders}` a level-up message may use, in display order.
    #[serde(default)]
    pub level_up_variables: Vec<TemplateVariable>,
}

/// One `{placeholder}` a message template accepts.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct TemplateVariable {
    pub name: String,
    pub description: String,
}

/// `{ok, message}` answer of an action that reports what it did in words.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ActionMessage {
    #[serde(default)]
    pub ok: bool,
    #[serde(default)]
    pub message: String,
}

/// One rank card preset as offered by a picker.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct CardOption {
    pub value: String,
    pub label: String,
}

/// `GET /users/{id}/rank-card?guild_id=` — a member's rank card style on one server.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct RankCard {
    /// The member's own partial style (`{}` = follow the server).
    #[serde(default)]
    pub style: HashMap<String, String>,
    #[serde(default)]
    pub guild_default: HashMap<String, String>,
    /// The server fixes the style; clients hide the editor.
    #[serde(default)]
    pub locked: bool,
    /// The fully resolved style the card renders with.
    #[serde(default)]
    pub effective: HashMap<String, String>,
    #[serde(default)]
    pub options: HashMap<String, Vec<CardOption>>,
}

fn default_factor() -> f64 {
    1.0
}
fn default_level_up_channel() -> i64 {
    1
}
fn default_base() -> i64 {
    100
}
fn default_min_gain() -> i64 {
    8
}
fn default_max_gain() -> i64 {
    15
}
fn default_cooldown_per() -> i64 {
    40
}

impl Default for LevelingConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            configured: false,
            level_up_channel: default_level_up_channel(),
            level_up_message: None,
            special_level_up_messages: HashMap::new(),
            blacklisted_roles: Vec::new(),
            blacklisted_channels: Vec::new(),
            blacklisted_users: Vec::new(),
            level_roles: HashMap::new(),
            multiplier_roles: HashMap::new(),
            multiplier_channels: HashMap::new(),
            role_stack: false,
            voice_enabled: false,
            delete_after_leave: false,
            factor: default_factor(),
            base: default_base(),
            min_gain: default_min_gain(),
            max_gain: default_max_gain(),
            cooldown_per: default_cooldown_per(),
            card_style: HashMap::new(),
            card_locked: false,
            card_options: HashMap::new(),
            level_up_variables: Vec::new(),
        }
    }
}

#[derive(Debug, Deserialize, Serialize)]
pub struct LeaderboardEntry {
    pub user_id: String,
    pub username: String,
    pub avatar_url: Option<String>,
    pub level: u32,
    pub xp: u64,
    pub total_xp: u64,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct LeaderboardResponse {
    pub entries: Vec<LeaderboardEntry>,
    pub total: u32,
}

/// One day of the cumulative-XP time series for a guild.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct XpHistoryPoint {
    /// ISO date (YYYY-MM-DD).
    pub day: String,
    pub total_xp: i64,
    pub gainers: i64,
}

#[derive(Debug, Default, Deserialize, Serialize)]
pub struct XpHistoryResponse {
    #[serde(default)]
    pub points: Vec<XpHistoryPoint>,
    #[serde(default)]
    pub days: u32,
}

// -- Member detail (user lookup) ---------------------------------------------

#[derive(Debug, Deserialize, Serialize)]
pub struct MemberRoleBadge {
    pub id: String,
    pub name: String,
    pub color: u32,
}

impl MemberRoleBadge {
    pub fn color_hex(&self) -> String {
        format!("#{:06x}", self.color)
    }
}

#[derive(Debug, Deserialize, Serialize)]
pub struct MemberLeveling {
    pub level: i64,
    pub xp: i64,
    pub messages: i64,
    pub rank: i64,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct MemberCase {
    pub case_index: i64,
    pub action: String,
    pub reason: Option<String>,
    pub moderator_id: Option<String>,
    pub moderator_name: Option<String>,
    pub created_at: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct MemberCommandStats {
    #[serde(default)]
    pub total_commands: i64,
    pub first_command_at: Option<String>,
    #[serde(default)]
    pub top_commands: Vec<CommandUsageEntry>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct CommandUsageEntry {
    pub command: String,
    pub uses: i64,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct NameHistoryEntry {
    pub name: String,
    pub changed_at: String,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct MemberNameHistory {
    #[serde(default)]
    pub usernames: Vec<NameHistoryEntry>,
    #[serde(default)]
    pub nicknames: Vec<NameHistoryEntry>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct MemberDetail {
    pub id: String,
    pub name: String,
    pub display_name: String,
    pub avatar_url: String,
    pub joined_at: Option<String>,
    pub created_at: String,
    #[serde(default)]
    pub roles: Vec<MemberRoleBadge>,
    pub bot: bool,
    #[serde(default)]
    pub in_guild: bool,
    pub leveling: Option<MemberLeveling>,
    #[serde(default)]
    pub cases: Vec<MemberCase>,
    #[serde(default)]
    pub case_count: u32,
    #[serde(default)]
    pub warning_count: u32,
    // Extended user info
    pub top_role: Option<String>,
    #[serde(default)]
    pub top_role_color: u32,
    pub join_position: Option<u32>,
    #[serde(default)]
    pub member_count: u32,
    #[serde(default)]
    pub mutual_guilds: u32,
    #[serde(default)]
    pub permissions: u64,
    pub boosting_since: Option<String>,
    #[serde(default)]
    pub public_flags: Vec<String>,
    pub status: Option<String>,
    pub command_stats: Option<MemberCommandStats>,
    pub last_seen: Option<String>,
    pub names: Option<MemberNameHistory>,
    #[serde(default)]
    pub avatar_count: u32,
    #[serde(default)]
    pub owned_tags: Vec<OwnedTag>,
    /// Whether the member currently holds the guild's configured mute role.
    #[serde(default)]
    pub muted: bool,
    /// Whether the guild has a mute role at all — without one, `mute` is not offerable.
    #[serde(default)]
    pub mute_role_configured: bool,
    /// End of an active Discord timeout, if any.
    pub timed_out_until: Option<String>,
}

impl MemberDetail {
    pub fn top_role_color_hex(&self) -> String {
        format!("#{:06x}", self.top_role_color)
    }
}

/// One message from the live-history scan behind the member message viewer.
#[derive(Debug, Deserialize, Serialize)]
pub struct MemberMessage {
    pub id: String,
    pub channel_id: String,
    pub channel_name: String,
    pub content: String,
    pub created_at: String,
    pub edited_at: Option<String>,
    pub jump_url: String,
    #[serde(default)]
    pub attachments: Vec<String>,
    #[serde(default)]
    pub embed_count: u32,
}

/// A member's recent messages. Percy holds no message archive, so this is a bounded
/// scan of the guild's most recently active channels — `partial` reports that some
/// channels were skipped, i.e. the list may not be exhaustive.
#[derive(Debug, Deserialize, Serialize)]
pub struct MemberMessages {
    #[serde(default)]
    pub messages: Vec<MemberMessage>,
    #[serde(default)]
    pub scanned_channels: u32,
    #[serde(default)]
    pub partial: bool,
}

/// Personal profile for a non-admin member (leveling + economy + command stats).
#[derive(Debug, Deserialize, Serialize)]
pub struct MemberSelf {
    pub id: String,
    pub name: String,
    pub display_name: String,
    pub avatar_url: String,
    pub joined_at: Option<String>,
    pub created_at: String,
    #[serde(default)]
    pub roles: Vec<MemberRoleBadge>,
    pub top_role: Option<String>,
    #[serde(default)]
    pub top_role_color: u32,
    pub join_position: Option<u32>,
    #[serde(default)]
    pub member_count: u32,
    pub boosting_since: Option<String>,
    pub leveling: Option<MemberLeveling>,
    pub economy: Option<MemberEconomy>,
    pub command_stats: Option<MemberCommandStats>,
    #[serde(default)]
    pub owned_tags: Vec<OwnedTag>,
}

impl MemberSelf {
    pub fn top_role_color_hex(&self) -> String {
        format!("#{:06x}", self.top_role_color)
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct MemberEconomy {
    #[serde(default)]
    pub cash: i64,
    #[serde(default)]
    pub bank: i64,
    #[serde(default)]
    pub total: i64,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct UserSettings {
    pub timezone: Option<String>,
    #[serde(default = "default_true")]
    pub track_presence: bool,
    #[serde(default = "default_true")]
    pub track_history: bool,
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
pub struct AvatarEntry {
    pub image: String,
    pub changed_at: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct AvatarHistoryResponse {
    pub avatars: Vec<AvatarEntry>,
    #[serde(default)]
    pub total: u32,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct PresenceEntry {
    pub status: Option<String>,
    pub status_before: Option<String>,
    pub changed_at: Option<String>,
}

/// Consent-tracked history for a user (names, avatars, presence) — the payload
/// behind the personal dashboard's "Your History" view.
#[derive(Debug, Deserialize, Serialize)]
pub struct UserHistory {
    #[serde(default)]
    pub usernames: Vec<NameHistoryEntry>,
    #[serde(default)]
    pub nicknames: Vec<NameHistoryEntry>,
    #[serde(default)]
    pub avatars: Vec<AvatarEntry>,
    #[serde(default)]
    pub presence: Vec<PresenceEntry>,
}

// -- Polls types -------------------------------------------------------------

#[derive(Debug, Deserialize, Serialize)]
pub struct PollInfo {
    pub id: i64,
    pub channel_id: String,
    pub message_id: String,
    pub question: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub options: Vec<String>,
    #[serde(default)]
    pub image_url: String,
    #[serde(default)]
    pub color: String,
    pub published: Option<String>,
    pub expires: Option<String>,
    #[serde(default)]
    pub ended: bool,
    #[serde(default)]
    pub total_votes: u32,
    #[serde(default)]
    pub hide_results: bool,
    #[serde(default)]
    pub role_id: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct PollsResponse {
    pub polls: Vec<PollInfo>,
    #[serde(default)]
    pub total: u32,
}

// -- Giveaways types ---------------------------------------------------------

#[derive(Debug, Deserialize, Serialize)]
pub struct GiveawayInfo {
    pub id: i64,
    pub channel_id: String,
    pub message_id: String,
    pub author_id: String,
    pub title: String,
    pub description: Option<String>,
    pub winners_count: u32,
    pub entries: u32,
    #[serde(default)]
    pub ended: bool,
    pub ends_at: Option<String>,
    #[serde(default)]
    pub required_role_id: Option<String>,
    #[serde(default)]
    pub min_account_days: u32,
    #[serde(default)]
    pub min_join_days: u32,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct GiveawaysResponse {
    pub giveaways: Vec<GiveawayInfo>,
    #[serde(default)]
    pub total: u32,
}

// -- Tags types --------------------------------------------------------------

#[derive(Debug, Deserialize, Serialize)]
pub struct TagInfo {
    pub id: i64,
    pub name: String,
    pub owner_id: Option<String>,
    pub owner_name: Option<String>,
    pub uses: u32,
    pub created_at: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct TagCreator {
    pub user_id: Option<String>,
    pub username: String,
    pub count: u32,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct TagsResponse {
    pub total: u32,
    pub total_uses: u64,
    pub tags: Vec<TagInfo>,
    pub top_creators: Vec<TagCreator>,
}

/// Full content of a single tag, fetched on demand for the markdown preview.
#[derive(Debug, Deserialize, Serialize)]
pub struct TagDetail {
    pub id: i64,
    pub name: String,
    pub content: String,
    pub owner_id: Option<String>,
    pub owner_name: Option<String>,
    #[serde(default)]
    pub uses: u32,
    pub created_at: Option<String>,
}

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

#[derive(Debug, Deserialize, Serialize)]
pub struct CommandUsage {
    pub command: String,
    pub uses: u64,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct GuildStats {
    pub member_count: Option<u32>,
    pub online_count: u32,
    pub bot_count: u32,
    pub human_count: u32,
    pub channel_count: u32,
    pub role_count: u32,
    pub emoji_count: u32,
    pub boost_count: u32,
    pub boost_tier: u32,
    pub total_commands: u64,
    pub top_commands: Vec<CommandUsage>,
    pub created_at: String,
    pub owner_id: String,
    pub owner_name: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct BotStats {
    #[serde(default)]
    pub version: Option<String>,
    pub guild_count: u32,
    pub user_count: u32,
    pub channel_count: u32,
    pub total_commands_used: u64,
    pub cog_count: u32,
    pub command_count: u32,
    pub latency_ms: f64,
    #[serde(default)]
    pub uptime_seconds: f64,
}

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

#[derive(Debug, Deserialize, Serialize)]
pub struct ChangelogResponse {
    pub entries: Vec<ChangelogEntry>,
    #[serde(default)]
    pub current_version: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ChangelogEntry {
    pub version: String,
    pub date: String,
    #[serde(default)]
    pub sections: Vec<ChangelogSection>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ChangelogSection {
    pub name: String,
    pub slug: String,
    pub entries: Vec<String>,
}

// -- Composite endpoint types ------------------------------------------------

#[derive(Debug, Deserialize, Serialize)]
pub struct GuildOverviewGuild {
    pub id: u64,
    pub name: String,
    pub icon_url: Option<String>,
    pub member_count: Option<u32>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct GuildOverviewStats {
    pub online_count: u32,
    pub bot_count: u32,
    pub channel_count: u32,
    pub role_count: u32,
    pub emoji_count: u32,
    pub boost_count: u32,
    pub boost_tier: u32,
    pub total_commands: u64,
    #[serde(default)]
    pub recent_cases: u32,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct GuildOverviewBot {
    #[serde(default)]
    pub version: Option<String>,
    pub guild_count: u32,
    pub user_count: u32,
    pub command_count: u32,
    pub latency_ms: f64,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct GuildOverviewFeatures {
    #[serde(default)]
    pub leveling: bool,
    #[serde(default)]
    pub economy: bool,
    #[serde(default)]
    pub music: bool,
    #[serde(default)]
    pub sentinel: bool,
    #[serde(default)]
    pub audit_log: bool,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct GuildOverview {
    pub guild: GuildOverviewGuild,
    pub stats: GuildOverviewStats,
    pub bot: GuildOverviewBot,
    pub features: GuildOverviewFeatures,
}

// -- Economy types -----------------------------------------------------------

#[derive(Debug, Deserialize, Serialize)]
pub struct ShopItem {
    pub id: u64,
    pub name: String,
    pub description: Option<String>,
    pub price: u64,
    #[serde(default)]
    pub effect: Option<String>,
    #[serde(default)]
    pub effect_value: Option<i64>,
    #[serde(default)]
    pub duration_minutes: Option<i64>,
    // -- Item overhaul fields (absent on older Percy builds) --
    #[serde(default)]
    pub emoji: Option<String>,
    #[serde(default)]
    pub rarity: Option<String>,
    #[serde(default)]
    pub category: Option<String>,
    #[serde(default)]
    pub stock: Option<i64>,
    #[serde(default)]
    pub max_per_user: Option<i64>,
    #[serde(default)]
    pub required_role_id: Option<String>,
    #[serde(default)]
    pub available_until: Option<String>,
    #[serde(default)]
    pub remove_role_id: Option<String>,
    #[serde(default)]
    pub reply_message: Option<String>,
    /// https link shown as the item card's thumbnail in Discord (absent on older Percy builds).
    #[serde(default)]
    pub image_url: Option<String>,
    /// Set for Percy's own catalogue items; those are not editable or deletable from the dashboard.
    #[serde(default)]
    pub builtin_key: Option<String>,
    /// Absent on older Percy builds, where every item is for sale.
    #[serde(default = "default_true")]
    pub for_sale: bool,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct LotteryInfo {
    pub ticket_price: u64,
    pub jackpot: u64,
    pub channel_id: String,
    pub ends_at: Option<String>,
}

/// Per-guild economy tuning; Percy answers defaults when never configured.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct EconomySettings {
    pub payout_multiplier: f64,
    pub rob_enabled: bool,
    pub daily_base: i64,
    /// `None` = uncapped.
    pub max_bet: Option<i64>,
}

impl Default for EconomySettings {
    /// Mirrors Percy's server-side defaults for guilds that never configured anything.
    fn default() -> Self {
        Self {
            payout_multiplier: 1.0,
            rob_enabled: true,
            daily_base: 250,
            max_bet: None,
        }
    }
}

#[derive(Debug, Deserialize, Serialize)]
pub struct EconomyInfo {
    pub items: Vec<ShopItem>,
    pub lottery: Option<LotteryInfo>,
    /// Absent on Percy versions predating the economy expansion.
    #[serde(default)]
    pub settings: Option<EconomySettings>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct BalanceEntry {
    pub user_id: String,
    pub username: String,
    pub avatar_url: Option<String>,
    pub cash: i64,
    pub bank: i64,
    pub total: i64,
}

/// One game's guild-wide totals from `GET /guilds/{id}/games`.
#[derive(Debug, Deserialize, Serialize)]
pub struct GameOverviewEntry {
    pub game: String,
    pub label: String,
    #[serde(default)]
    pub icon: Option<String>,
    pub played: i64,
    pub won: i64,
    pub players: i64,
    pub profit: i64,
}

/// One member on the guild's games leaderboard.
#[derive(Debug, Deserialize, Serialize)]
pub struct GamePlayerEntry {
    pub user_id: String,
    pub username: String,
    #[serde(default)]
    pub avatar_url: Option<String>,
    pub played: i64,
    pub won: i64,
    pub winrate: f64,
    pub profit: i64,
}

/// Guild-wide game statistics (per-game totals + top players).
#[derive(Debug, Default, Deserialize, Serialize)]
pub struct GamesStats {
    #[serde(default)]
    pub games: Vec<GameOverviewEntry>,
    #[serde(default)]
    pub top_players: Vec<GamePlayerEntry>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct BalancesResponse {
    pub entries: Vec<BalanceEntry>,
    #[serde(default)]
    pub total: u32,
}

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

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct PlaylistTrack {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub url: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub author: Option<String>,
    /// Saved cover, or one Percy recovered (last play's artwork, the YouTube thumbnail).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub artwork: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct PlaylistSummary {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub track_count: u32,
    /// The owner's custom cover, if they set one.
    #[serde(default)]
    pub thumbnail: Option<String>,
    /// What to show: the custom cover, else the first song's.
    #[serde(default)]
    pub cover: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct PlaylistDetail {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub thumbnail: Option<String>,
    #[serde(default)]
    pub cover: Option<String>,
    #[serde(default)]
    pub tracks: Vec<PlaylistTrack>,
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
        let l: UserMusicLibrary = serde_json::from_str(r#"{"top_tracks":[{"title":"T","author":"A","uri":"u","artwork":null,"length_ms":5,"plays":3}],"recent":[],"liked":[{"name":"n","url":"u"}],"playlists":[{"id":"7","name":"P","track_count":2}]}"#).unwrap();
        assert_eq!(
            (l.top_tracks[0].plays, l.liked.len(), l.playlists[0].track_count),
            (3, 1, 2)
        );
        assert!(serde_json::from_str::<UserMusicLibrary>("{}")
            .unwrap()
            .playlists
            .is_empty());
        let d: PlaylistDetail =
            serde_json::from_str(r#"{"id":"7","name":"P","tracks":[{"name":"n","url":"u"}]}"#).unwrap();
        assert_eq!(d.tracks[0].url, "u");
        assert!(serde_json::from_str::<PlaylistDetail>("{}").unwrap().tracks.is_empty());
    }
}

// -- Comics types ------------------------------------------------------------

#[derive(Debug, Deserialize, Serialize)]
pub struct ComicFeedEntry {
    pub id: u64,
    pub brand: String,
    pub channel_id: String,
    pub format: String,
    pub day: u8,
    pub ping: Option<String>,
    pub pin: bool,
    pub next_pull: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct ComicsResponse {
    pub feeds: Vec<ComicFeedEntry>,
}

// -- Temp Channels types -----------------------------------------------------

#[derive(Debug, Deserialize, Serialize)]
pub struct ActiveTempChannel {
    pub channel_id: String,
    pub channel_name: String,
    pub user_count: u32,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct TempChannelEntry {
    pub channel_id: String,
    pub channel_name: String,
    pub format: String,
    /// Live spawned channels for this hub (resolved by shared category). Empty
    /// when nobody is using the hub. `#[serde(default)]` keeps it backward-safe.
    #[serde(default)]
    pub active_channels: Vec<ActiveTempChannel>,
    #[serde(default)]
    pub total_users: u32,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct TempChannelsResponse {
    pub entries: Vec<TempChannelEntry>,
}

// -- Status Feed types -------------------------------------------------------

#[derive(Debug, Deserialize, Serialize)]
pub struct StatusFeedInfo {
    pub subscribed: bool,
    pub channel: Option<ChannelRef>,
}

// -- Lockdowns types ---------------------------------------------------------

#[derive(Debug, Deserialize, Serialize)]
pub struct LockdownEntry {
    pub channel_id: String,
    pub channel_name: String,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct LockdownsResponse {
    pub entries: Vec<LockdownEntry>,
}

// -- Emoji Stats types -------------------------------------------------------

#[derive(Debug, Deserialize, Serialize)]
pub struct EmojiStatEntry {
    pub emoji_id: String,
    pub emoji_name: String,
    pub emoji_url: Option<String>,
    pub total: u64,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct EmojiStatsResponse {
    pub total_uses: u64,
    pub distinct_emojis: u64,
    pub entries: Vec<EmojiStatEntry>,
    #[serde(default)]
    pub total: u32,
}

// -- Audit log (cases) types -------------------------------------------------

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct CaseEntry {
    pub case_index: i64,
    pub action: String,
    pub target_id: String,
    pub target_name: String,
    pub moderator_id: Option<String>,
    pub moderator_name: Option<String>,
    pub reason: Option<String>,
    pub created_at: Option<String>,
    #[serde(default)]
    pub pardoned: bool,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct CasesResponse {
    pub cases: Vec<CaseEntry>,
    pub total: u64,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct RecentCasesResponse {
    pub cases: Vec<CaseEntry>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct CaseActionResponse {
    pub ok: bool,
    pub case: CaseEntry,
}

// -- Activity heatmap types --------------------------------------------------

#[derive(Debug, Deserialize, Serialize)]
pub struct ActivityDay {
    pub day: String,
    pub count: u64,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct ActivityResponse {
    pub activity: Vec<ActivityDay>,
    pub days: u32,
}

// -- Bulk action types -------------------------------------------------------

#[derive(Debug, Deserialize, Serialize)]
pub struct BulkActionFailure {
    pub user_id: String,
    pub error: String,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct BulkActionResponse {
    pub ok: bool,
    pub successes: u32,
    pub failures: Vec<BulkActionFailure>,
}

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

/// A single outbound-webhook subscription. `secret` is only ever populated on the
/// create response (Percy masks it to `None` on every subsequent read), so store
/// it the moment a subscription is created.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct WebhookSubscription {
    #[serde(default)]
    pub id: i64,
    #[serde(default)]
    pub url: String,
    #[serde(default)]
    pub events: Vec<String>,
    #[serde(default)]
    pub label: Option<String>,
    #[serde(default)]
    pub enabled: bool,
    /// The HMAC signing secret — present only on the create response.
    #[serde(default)]
    pub secret: Option<String>,
    #[serde(default)]
    pub created_at: Option<String>,
    #[serde(default)]
    pub last_delivery_at: Option<String>,
    #[serde(default)]
    pub last_status: Option<i64>,
    #[serde(default)]
    pub failure_count: i64,
}

/// The `GET /webhooks` listing (secrets always masked here).
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct WebhookSubscriptionsResponse {
    #[serde(default)]
    pub subscriptions: Vec<WebhookSubscription>,
}

/// The `POST /webhooks` create response, carrying the newly-minted subscription
/// (with its `secret` revealed exactly once).
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct WebhookCreateResponse {
    #[serde(default)]
    pub ok: bool,
    #[serde(default)]
    pub subscription: WebhookSubscription,
}

/// The catalogue of events a subscription may listen for.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct WebhookEventsResponse {
    #[serde(default)]
    pub events: Vec<String>,
}

/// A single delivery attempt in a subscription's health log.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct WebhookDelivery {
    #[serde(default)]
    pub event: String,
    #[serde(default)]
    pub success: bool,
    #[serde(default)]
    pub status_code: Option<i64>,
    #[serde(default)]
    pub attempts: i64,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub created_at: Option<String>,
}

/// The `GET /webhooks/{id}/deliveries` response.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct WebhookDeliveriesResponse {
    #[serde(default)]
    pub deliveries: Vec<WebhookDelivery>,
}

/// The synchronous result of a `POST /webhooks/{id}/test`.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct WebhookTestResponse {
    #[serde(default)]
    pub ok: bool,
    /// The raw per-attempt result Percy reports (status code, error, attempts…).
    #[serde(default)]
    pub result: serde_json::Value,
}

// -- Backup & template types -------------------------------------------------

/// A public template card in the gallery.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct TemplateEntry {
    #[serde(default)]
    pub slug: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub downloads: i64,
    #[serde(default)]
    pub created_at: Option<String>,
    /// Only present on the caller's own templates.
    #[serde(default)]
    pub public: Option<bool>,
}

/// The `GET /templates` response: the public gallery plus the caller's own.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct TemplatesResponse {
    #[serde(default)]
    pub public: Vec<TemplateEntry>,
    #[serde(default)]
    pub own: Vec<TemplateEntry>,
}

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

/// `GET /guilds/{id}/health` — whether Percy can do what the server configured.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct GuildHealth {
    /// Every feature, those needing attention first.
    #[serde(default)]
    pub features: Vec<HealthFeature>,
    /// How many features need attention.
    #[serde(default)]
    pub attention: u32,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct HealthFeature {
    pub key: String,
    pub label: String,
    /// `ok` | `attention` | `off`.
    pub status: String,
    /// Plain-language problems naming the channel/role and permission.
    #[serde(default)]
    pub problems: Vec<String>,
}

// -- Role menus ----------------------------------------------------------------

/// `GET /guilds/{id}/rolemenus`.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct RoleMenusResponse {
    #[serde(default)]
    pub menus: Vec<RoleMenu>,
    #[serde(default)]
    pub max_roles: u32,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct RoleMenu {
    pub id: i64,
    pub title: String,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub unique: bool,
    /// `None` when the channel was deleted.
    #[serde(default)]
    pub channel: Option<ChannelRef>,
    #[serde(default)]
    pub jump_url: Option<String>,
    #[serde(default)]
    pub roles: Vec<RoleMenuRole>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct RoleMenuRole {
    pub role_id: String,
    /// `None` when the role was deleted.
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub color: u32,
    #[serde(default)]
    pub emoji: Option<String>,
    #[serde(default)]
    pub label: Option<String>,
    #[serde(default = "default_true")]
    pub assignable: bool,
}

// -- Starboard -----------------------------------------------------------------

/// `GET /guilds/{id}/starboard` — settings plus the ten most-starred messages.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct StarboardInfo {
    #[serde(default)]
    pub configured: bool,
    #[serde(default)]
    pub enabled: bool,
    #[serde(default)]
    pub channel: Option<ChannelRef>,
    #[serde(default)]
    pub threshold: u32,
    #[serde(default)]
    pub emoji: String,
    #[serde(default)]
    pub self_star: bool,
    #[serde(default)]
    pub ignored_channels: Vec<Option<ChannelRef>>,
    #[serde(default)]
    pub max_age_hours: u32,
    #[serde(default)]
    pub allow_nsfw: bool,
    #[serde(default)]
    pub top: Vec<StarboardTop>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct StarboardTop {
    pub stars: u32,
    #[serde(default)]
    pub author: Option<String>,
    #[serde(default)]
    pub channel: Option<ChannelRef>,
    pub jump_url: String,
}

// -- Reminders -----------------------------------------------------------------

/// `GET /users/{id}/reminders`.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct RemindersResponse {
    #[serde(default)]
    pub reminders: Vec<Reminder>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Reminder {
    pub id: i64,
    /// ISO-8601 UTC.
    pub expires: String,
    #[serde(default)]
    pub message: String,
    /// e.g. "every day"; `None` for a one-off.
    #[serde(default)]
    pub repeats: Option<String>,
    #[serde(default)]
    pub shared_with: u32,
}

// -- Notifications and action requests -------------------------------------------

/// Percy emits notification ids as JSON integers, but the dashboard treats them as opaque
/// strings (cursors); accept either.
fn string_or_int<'de, D: serde::Deserializer<'de>>(d: D) -> Result<String, D::Error> {
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum V {
        S(String),
        I(i64),
    }
    Ok(match V::deserialize(d)? {
        V::S(s) => s,
        V::I(i) => i.to_string(),
    })
}

/// One row of `GET /guilds/{id}/notifications`.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Notification {
    #[serde(deserialize_with = "string_or_int")]
    pub id: String,
    pub kind: String,
    pub severity: String,
    pub title: String,
    #[serde(default)]
    pub body: String,
    #[serde(default)]
    pub link: Option<String>,
    /// ISO-8601 UTC.
    pub created_at: String,
    /// Guild-wide (not addressed to one user).
    #[serde(default)]
    pub global: bool,
    #[serde(default)]
    pub unread: bool,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct NotificationList {
    #[serde(default)]
    pub items: Vec<Notification>,
    #[serde(default)]
    pub unread: u32,
    #[serde(default, deserialize_with = "string_or_int")]
    pub last_read_id: String,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct NotificationSummary {
    #[serde(default)]
    pub unread: u32,
    #[serde(default)]
    pub pending_requests: u32,
}

/// A moderator's request that someone with more authority carries out an action.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ActionRequest {
    pub id: String,
    pub action: String,
    /// `pending`, `approved`, `denied`, `expired`, `failed`, ...
    pub status: String,
    pub requester_id: String,
    #[serde(default)]
    pub requester_name: Option<String>,
    pub target_id: String,
    #[serde(default)]
    pub target_name: Option<String>,
    #[serde(default)]
    pub reason: String,
    /// Seconds, for timeouts.
    #[serde(default)]
    pub duration: Option<i64>,
    #[serde(default)]
    pub decided_by: Option<String>,
    #[serde(default)]
    pub decided_by_name: Option<String>,
    #[serde(default)]
    pub decision_note: Option<String>,
    #[serde(default)]
    pub result: Option<String>,
    pub created_at: String,
    #[serde(default)]
    pub decided_at: Option<String>,
    pub expires_at: String,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct ActionRequestList {
    #[serde(default)]
    pub items: Vec<ActionRequest>,
}

/// Outcome of `POST /requests/{id}/decide`. Also built from 400/403/404/409 bodies, where
/// `ok` is `false` and `message` says why; `request` is `None` when Percy found none.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct RequestDecision {
    pub ok: bool,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub message: String,
    #[serde(default)]
    pub request: Option<ActionRequest>,
}

/// `GET`/`PATCH /requests/settings`: where requests are posted and which roles may file them.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct RequestSettings {
    #[serde(default)]
    pub channel: Option<ChannelRef>,
    #[serde(default)]
    pub role_ids: Vec<String>,
    #[serde(default)]
    pub roles: Vec<RoleRef>,
}

#[cfg(test)]
mod notification_tests {
    use super::*;

    #[test]
    fn notification_list_accepts_integer_ids() {
        let l: NotificationList = serde_json::from_str(r##"{"items":[{"id":42,"kind":"missing_permission","severity":"warning","title":"Missing permission in #logs","body":"x","link":"/dashboard/guild/1/logs","meta":{"channel_id":"555"},"created_at":"2026-10-21T12:30:00.123456Z","global":false,"unread":true}],"unread":1,"last_read_id":41}"##).unwrap();
        assert_eq!(l.items[0].id, "42");
        assert_eq!(l.last_read_id, "41");
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

/// One member's wallet (`GET`/`POST .../economy/balances/{uid}[/adjust]`).
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct MemberBalance {
    #[serde(default)]
    pub user_id: String,
    #[serde(default)]
    pub cash: i64,
    #[serde(default)]
    pub bank: i64,
    #[serde(default)]
    pub total: i64,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct InventoryItem {
    pub item_id: u64,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub quantity: i64,
    #[serde(default)]
    pub emoji: Option<String>,
    #[serde(default)]
    pub rarity: Option<String>,
    #[serde(default)]
    pub effect: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct MemberInventory {
    #[serde(default)]
    pub user_id: String,
    #[serde(default)]
    pub items: Vec<InventoryItem>,
}

/// Result of giving or taking an item: the member's new quantity.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct InventoryChange {
    #[serde(default)]
    pub item_id: u64,
    #[serde(default)]
    pub quantity: i64,
}

/// `GET .../leveling/users/{uid}`.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct MemberLevel {
    #[serde(default)]
    pub user_id: String,
    #[serde(default)]
    pub level: i64,
    #[serde(default)]
    pub xp: i64,
    #[serde(default)]
    pub messages: i64,
    #[serde(default)]
    pub rank: i64,
}

// -- Warn policy types ---------------------------------------------------------

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct WarnThreshold {
    pub warn_count: u32,
    pub action: String,
    #[serde(default)]
    pub duration_seconds: Option<u64>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct WarnPolicy {
    #[serde(default)]
    pub expiry_days: Option<u32>,
    #[serde(default)]
    pub thresholds: Vec<WarnThreshold>,
    #[serde(default)]
    pub actions: Vec<String>,
}
