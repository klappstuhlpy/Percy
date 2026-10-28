//! Server-dashboard page templates and their row structs.

#![allow(dead_code)]

use super::account::CardField;
use crate::view::Viewer;
use askama::Template;
use percy_client::*;

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/guild.html")]
pub(crate) struct GuildTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild: GuildInfo,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) channels: Vec<Channel>,
    pub(crate) roles: Vec<Role>,
    pub(crate) sentinel: Option<SentinelInfo>,
    pub(crate) lockdowns: LockdownsResponse,
    pub(crate) status_feed: StatusFeedInfo,
    pub(crate) bot_profile: CustomBotProfile,
    /// Per-feature setup status; `None` when Percy could not be asked (the card is hidden).
    pub(crate) health: Option<percy_client::GuildHealth>,
    /// True when the guild loaded but a sub-resource (roles/channels) failed to
    /// fetch — the page renders, but with empty pickers, so saving could clobber
    /// config. Drives a warning banner instead of a silently hollow page.
    pub(crate) degraded: bool,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/members.html")]
pub(crate) struct MembersTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) members: Vec<Member>,
    pub(crate) roles: Vec<Role>,
    /// Whether the guild has a mute role — without one the `mute` action is not offered.
    pub(crate) mute_role_configured: bool,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/leveling.html")]
pub(crate) struct LevelingTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) config: LevelingConfig,
    pub(crate) card_fields: Vec<CardField>,
    pub(crate) leaderboard: LeaderboardResponse,
    pub(crate) roles: Vec<Role>,
    pub(crate) text_channels: Vec<Channel>,
    pub(crate) all_channels: Vec<Channel>,
    pub(crate) level_roles: Vec<LevelRoleRow>,
    pub(crate) multiplier_roles: Vec<MultiplierRow>,
    pub(crate) multiplier_channels: Vec<MultiplierRow>,
    pub(crate) blacklisted_roles: Vec<BlacklistRow>,
    pub(crate) blacklisted_channels: Vec<BlacklistRow>,
    pub(crate) blacklisted_users: Vec<BlacklistRow>,
    pub(crate) special_messages: Vec<SpecialMsgRow>,
    /// Pre-serialized JSON array of `{day,total_xp,gainers}` for the uPlot chart.
    pub(crate) xp_history_json: String,
}

/// Read-only public server overview shown to members who can't manage the guild.
/// Surfaces the same information a member could already see in Discord: server
/// stats, top leveling ranks, active polls/giveaways, the shop, the live music
/// player, and a short "powered by Percy" blurb.
#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/overview.html")]
pub(crate) struct OverviewTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) guild_icon: Option<String>,
    pub(crate) member_count: Option<u32>,
    pub(crate) stats: GuildStats,
    pub(crate) bot_stats: BotStats,
    pub(crate) music: MusicInfo,
    /// Whether the viewer currently shares the bot's voice channel (initial
    /// state; the live poll refreshes it). Gates the playback controls.
    pub(crate) can_control_music: bool,
    pub(crate) leaderboard: LeaderboardResponse,
    pub(crate) active_polls: Vec<PollInfo>,
    pub(crate) active_giveaways: Vec<GiveawayInfo>,
    pub(crate) economy: EconomyInfo,
    pub(crate) games: GamesStats,
    /// Top 5 wallets for the "Richest Members" card.
    pub(crate) balances: BalancesResponse,
}

/// A resolved level-reward row (role granted at a level threshold).
pub(crate) struct LevelRoleRow {
    pub(crate) role_id: String,
    pub(crate) role_name: String,
    pub(crate) color_hex: String,
    pub(crate) level: i64,
}

/// A resolved XP-multiplier row for a role or channel.
pub(crate) struct MultiplierRow {
    pub(crate) id: String,
    pub(crate) name: String,
    pub(crate) multiplier: f64,
}

/// A resolved blacklist entry (role/channel/user excluded from XP).
pub(crate) struct BlacklistRow {
    pub(crate) id: String,
    pub(crate) name: String,
}

/// A custom level-up message keyed by level.
pub(crate) struct SpecialMsgRow {
    pub(crate) level: String,
    pub(crate) message: String,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/polls.html")]
pub(crate) struct PollsTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) guild: GuildInfo,
    pub(crate) channels: Vec<Channel>,
    pub(crate) roles: Vec<Role>,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) polls: PollsResponse,
    pub(crate) active_count: usize,
    pub(crate) ended_count: usize,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/gallery.html")]
pub(crate) struct GalleryTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) gallery: GalleryResponse,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/giveaways.html")]
pub(crate) struct GiveawaysTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) giveaways: GiveawaysResponse,
    pub(crate) channels: Vec<Channel>,
    pub(crate) roles: Vec<Role>,
    pub(crate) active_count: usize,
    pub(crate) ended_count: usize,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/tags.html")]
pub(crate) struct TagsTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) tags: TagsResponse,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/commands.html")]
pub(crate) struct CommandsTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) commands: CommandsResponse,
    pub(crate) channels: Vec<Channel>,
    pub(crate) roles: Vec<Role>,
    pub(crate) disabled_count: usize,
    pub(crate) partial_count: usize,
    pub(crate) override_count: usize,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/stats.html")]
pub(crate) struct StatsTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) stats: GuildStats,
    pub(crate) bot_stats: BotStats,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/autoresponders.html")]
pub(crate) struct AutorespondersTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) data: AutorespondersResponse,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/economy.html")]
pub(crate) struct EconomyTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) economy: EconomyInfo,
    /// The guild's tuning knobs, materialized with defaults when Percy has no row.
    pub(crate) eco_settings: EconomySettings,
    pub(crate) balances: BalancesResponse,
    pub(crate) channels: Vec<Channel>,
    pub(crate) roles: Vec<Role>,
}

impl EconomyTemplate {
    /// Stock as the shop shows it: unlimited, remaining, or gone.
    fn stock_label(&self, item: &ShopItem) -> String {
        match item.stock {
            None => "\u{221e}".to_string(),
            Some(n) if n <= 0 => "sold out".to_string(),
            Some(n) => format!("{n} left"),
        }
    }

    /// A role gate by name; a deleted role keeps its slot visible rather than vanishing.
    fn role_name(&self, id: &str) -> String {
        self.roles
            .iter()
            .find(|r| r.id == id)
            .map_or_else(|| "deleted role".to_string(), |r| format!("@{}", r.name))
    }
}

/// One server in the music app's picker.
#[derive(Debug, Clone)]
pub(crate) struct GuildPick {
    pub(crate) id: String,
    pub(crate) name: String,
    pub(crate) icon: Option<String>,
    pub(crate) playing: bool,
}

/// The Rythm-style music app, for both the personal and the guild route.
#[derive(Template)]
#[template(path = "music/app.html")]
pub(crate) struct MusicAppTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) page_title: &'static str,
    pub(crate) guilds: Vec<GuildPick>,
    pub(crate) selected: Option<GuildPick>,
    pub(crate) viewer: Option<MusicViewer>,
    pub(crate) music: Option<MusicInfo>,
    pub(crate) history: Vec<MusicHistoryItem>,
    pub(crate) library: UserMusicLibrary,
    /// Channels for the admin forms; empty unless `is_admin`.
    pub(crate) channels: Vec<Channel>,
    /// True on `/dashboard/guild/:id/music`, where the guild-admin gate has already passed.
    pub(crate) manager_view: bool,
    /// The viewer passes the guild-admin gate (Manage Server); the admin-only forms need it.
    pub(crate) is_admin: bool,
}

impl MusicAppTemplate {
    /// "Jump back in": the newest play of each track, so a song on repeat shows once.
    pub(crate) fn recents(&self) -> Vec<&MusicHistoryItem> {
        recent_unique(&self.history, 12)
    }
    pub(crate) fn is_manager(&self) -> bool {
        self.viewer.as_ref().is_some_and(|v| v.is_manager)
    }
    pub(crate) fn can_control(&self) -> bool {
        self.viewer.as_ref().is_some_and(|v| v.can_control)
    }
    pub(crate) fn can_destroy(&self) -> bool {
        self.viewer.as_ref().is_some_and(|v| v.can_destroy)
    }
    pub(crate) fn can_start(&self) -> bool {
        self.viewer.as_ref().is_some_and(|v| v.can_start)
    }
    pub(crate) fn role(&self) -> &str {
        self.viewer.as_ref().map_or("visitor", |v| v.role.as_str())
    }
    /// `m:ss` for a millisecond length.
    pub(crate) fn dur(&self, ms: &Option<u64>) -> String {
        ms.map_or_else(String::new, crate::music_app::fmt_ms)
    }
    pub(crate) fn guild_id(&self) -> &str {
        self.selected.as_ref().map_or("", |g| g.id.as_str())
    }
    /// The role chip text; `app.js` `roleLabel()` mirrors it.
    pub(crate) fn role_label(&self) -> String {
        match self.role() {
            "dj" => "DJ".into(),
            "" => "Visitor".into(),
            r => {
                let mut c = r.chars();
                c.next()
                    .map_or_else(String::new, |f| f.to_uppercase().chain(c).collect())
            }
        }
    }
    /// `m:ss` for a plain millisecond count.
    pub(crate) fn ms(&self, ms: &u64) -> String {
        crate::music_app::fmt_ms(*ms)
    }
    /// Gradient slot (0-7) for artwork fallbacks; `static/js/music/app.js` `grad()` mirrors it.
    pub(crate) fn grad(&self, seed: &str) -> u32 {
        seed.chars().map(|c| c as u32).sum::<u32>() % 8
    }
    /// First letter of a server name, for picker tiles without an icon.
    pub(crate) fn initial(&self, name: &str) -> String {
        name.chars()
            .next()
            .map_or_else(|| "?".into(), |c| c.to_uppercase().to_string())
    }
    pub(crate) fn now_playing(&self) -> Option<&NowPlaying> {
        self.music.as_ref().and_then(|m| m.now_playing.as_ref())
    }
    pub(crate) fn listeners(&self) -> usize {
        self.music.as_ref().map_or(0, |m| m.listeners.len())
    }
    /// Why the transport is locked for this viewer, worded as `app.js` `lockHint()` words it.
    pub(crate) fn lock_hint(&self) -> String {
        let Some(v) = &self.viewer else {
            return "Pick a server first".into();
        };
        if v.dj_mode == 1 && !v.is_manager {
            return "Only DJs can control the player".into();
        }
        match &v.bot_channel {
            Some(c) => format!("Join #{} to control", c.name),
            None => "Join Percy's voice channel to control".into(),
        }
    }
    /// Title attribute for a transport button: empty when it may be pressed.
    pub(crate) fn ctl_title(&self, dj_only: bool) -> String {
        if !self.can_control() {
            self.lock_hint()
        } else if dj_only && !self.viewer.as_ref().is_some_and(|v| v.can_destroy) {
            "Needs the DJ role".into()
        } else {
            String::new()
        }
    }
    /// The context banner above Home; `app.js` `banner()` computes the same from live data.
    pub(crate) fn banner(&self) -> Option<MusicBanner> {
        self.selected.as_ref()?;
        let Some(m) = &self.music else {
            return Some(MusicBanner::new(
                "danger",
                "wifi-off",
                "Percy is offline",
                "The player for this server cannot be reached right now. We will keep retrying.",
                "",
            ));
        };
        let v = self.viewer.clone().unwrap_or_default();
        let playing = m.active && m.now_playing.is_some();
        if !playing {
            if v.can_start {
                let ch = v
                    .voice_channel
                    .map_or_else(|| "a voice channel".into(), |c| format!("#{}", c.name));
                return Some(MusicBanner::new(
                    "accent",
                    "headphones",
                    &format!("You are in {ch}"),
                    "Nothing is playing. Search for a song to start a session here.",
                    "Start session",
                ));
            }
            return Some(MusicBanner::new(
                "info",
                "voice",
                "Nothing is playing",
                "Join a voice channel in this server, then start a session from here.",
                "",
            ));
        }
        if !v.can_control {
            let text = if v.dj_mode == 1 && !v.is_manager {
                "This server is in DJ-only mode, so only DJs can steer the player.".to_string()
            } else {
                match &v.bot_channel {
                    Some(c) => format!("Join #{} in Discord to control playback and add songs.", c.name),
                    None => "Join Percy's voice channel to control playback.".into(),
                }
            };
            return Some(MusicBanner::new(
                "info",
                "headphones",
                "You are listening in",
                &text,
                "",
            ));
        }
        None
    }
    /// Initial viewer + status for `app.js`, so progress interpolation starts before the first poll.
    pub(crate) fn boot_json(&self) -> String {
        serde_json::json!({
            "viewer": self.viewer,
            "status": self.music.as_ref().map(crate::music_app::status_json),
            "vibes": VIBES,
        })
        .to_string()
    }
    pub(crate) fn vibes(&self) -> &'static [Vibe] {
        &VIBES
    }
    /// First cover candidate for an artwork URL at the size implied by the art class.
    pub(crate) fn cover(&self, url: &str, cls: &str) -> String {
        cover_chain(url, CoverSize::of(cls)).swap_remove(0).0
    }
    pub(crate) fn cover_lb(&self, url: &str, cls: &str) -> bool {
        cover_chain(url, CoverSize::of(cls))[0].1
    }
    /// The remaining candidates for `app.js`'s error fallback: space-separated, `lb:` = letterboxed.
    pub(crate) fn cover_rest(&self, url: &str, cls: &str) -> String {
        cover_chain(url, CoverSize::of(cls))
            .into_iter()
            .skip(1)
            .map(|(u, lb)| if lb { format!("lb:{u}") } else { u })
            .collect::<Vec<_>>()
            .join(" ")
    }
    /// The server's most-played tracks from its recent history (for users without their own).
    pub(crate) fn popular(&self) -> Vec<&MusicHistoryItem> {
        let mut counts: Vec<(&MusicHistoryItem, usize)> = Vec::new();
        for h in &self.history {
            match counts.iter_mut().find(|(c, _)| c.title.eq_ignore_ascii_case(&h.title)) {
                Some((_, n)) => *n += 1,
                None => counts.push((h, 1)),
            }
        }
        counts.sort_by_key(|c| std::cmp::Reverse(c.1));
        counts.into_iter().take(10).map(|(h, _)| h).collect()
    }
}

/// A "Choose a vibe" preset: clicking it queues `query` (a plain search the play route resolves).
#[derive(serde::Serialize)]
pub(crate) struct Vibe {
    pub(crate) key: &'static str,
    pub(crate) label: &'static str,
    pub(crate) query: &'static str,
    pub(crate) icon: &'static str,
}

const fn vibe(key: &'static str, label: &'static str, query: &'static str, icon: &'static str) -> Vibe {
    Vibe {
        key,
        label,
        query,
        icon,
    }
}

/// The curated vibe list; `app.js` reads it from the boot JSON for search suggestions.
pub(crate) const VIBES: [Vibe; 10] = [
    vibe("lofi", "Lofi", "lofi hip hop mix", "headphones"),
    vibe("phonk", "Phonk", "phonk mix", "sparkles"),
    vibe("chillhop", "Chillhop", "chillhop mix", "disc"),
    vibe("synthwave", "Synthwave", "synthwave retro mix", "trend"),
    vibe("hiphop", "Hip-Hop", "hip hop hits mix", "voice"),
    vibe("rock", "Rock", "rock classics mix", "star"),
    vibe("jazz", "Jazz", "jazz cafe mix", "music"),
    vibe("classical", "Classical", "classical music mix", "book"),
    vibe("kpop", "K-Pop", "kpop hits mix", "heart"),
    vibe("workout", "Workout", "workout music mix", "trophy"),
];

/// Artwork size class: rows/queue/bar, cards, now playing.
#[derive(Clone, Copy, PartialEq)]
pub(crate) enum CoverSize {
    Small,
    Card,
    Large,
}

impl CoverSize {
    fn of(cls: &str) -> Self {
        if cls.contains("np") {
            Self::Large
        } else if cls.contains("fill") {
            Self::Card
        } else {
            Self::Small
        }
    }
}

/// The video id of a YouTube thumbnail URL (`i.ytimg.com/vi/<id>/…`), if it is one.
fn yt_id(url: &str) -> Option<&str> {
    if !(url.contains("ytimg.com/") || url.contains("img.youtube.com/")) {
        return None;
    }
    let rest = url.split_once("/vi/").or_else(|| url.split_once("/vi_webp/"))?.1;
    let id = rest.split('/').next()?;
    (!id.is_empty()).then_some(id)
}

/// Cover candidates, best first, as `(url, letterboxed)`. YouTube's `hq`/`sd` thumbnails are 4:3
/// with black bars (letterboxed: the CSS zooms them past the bars); `maxres`/`mq` are 16:9.
/// `app.js` `coverChain()` mirrors this.
pub(crate) fn cover_chain(url: &str, size: CoverSize) -> Vec<(String, bool)> {
    let Some(id) = yt_id(url) else {
        return vec![(url.to_string(), false)];
    };
    let base = format!("https://i.ytimg.com/vi/{id}/");
    let names: &[(&str, bool)] = match size {
        CoverSize::Large => &[
            ("maxresdefault", false),
            ("sddefault", true),
            ("hqdefault", true),
            ("mqdefault", false),
        ],
        CoverSize::Card => &[("hqdefault", true), ("mqdefault", false)],
        CoverSize::Small => &[("mqdefault", false), ("hqdefault", true)],
    };
    let mut out: Vec<(String, bool)> = names.iter().map(|(n, lb)| (format!("{base}{n}.jpg"), *lb)).collect();
    if !out.iter().any(|(u, _)| u == url) {
        out.push((url.to_string(), url.contains("hqdefault") || url.contains("sddefault")));
    }
    out
}

/// The context card at the top of Home (start a session / listening only / Percy offline).
pub(crate) struct MusicBanner {
    pub(crate) kind: &'static str,
    pub(crate) icon: &'static str,
    pub(crate) title: String,
    pub(crate) text: String,
    pub(crate) cta: &'static str,
}

impl MusicBanner {
    fn new(kind: &'static str, icon: &'static str, title: &str, text: &str, cta: &'static str) -> Self {
        Self {
            kind,
            icon,
            title: title.into(),
            text: text.into(),
            cta,
        }
    }
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/comics.html")]
pub(crate) struct ComicsTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) data: ComicsResponse,
    pub(crate) channels: Vec<Channel>,
    pub(crate) roles: Vec<Role>,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/temp_channels.html")]
pub(crate) struct TempChannelsTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) data: TempChannelsResponse,
    pub(crate) channels: Vec<Channel>,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/highlights.html")]
pub(crate) struct HighlightsTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) data: HighlightsResponse,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/emoji_stats.html")]
pub(crate) struct EmojiStatsTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) data: EmojiStatsResponse,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/analytics.html")]
pub(crate) struct AnalyticsTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    /// Headline numbers (server-rendered for the default 30d window).
    pub(crate) summary: AnalyticsSummary,
    /// The default range the page opens on (drives the initial chart + tiles).
    pub(crate) range: &'static str,
    /// Pre-serialized `[{bucket,value}]` for the initial `commands` chart, so it
    /// paints before the JS re-fetch; later metric/range switches hit the JSON API.
    pub(crate) initial_series_json: String,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/webhooks.html")]
pub(crate) struct WebhooksTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) subscriptions: Vec<WebhookSubscription>,
    /// The catalogue of events a subscription can listen for.
    pub(crate) events: Vec<String>,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/backup.html")]
pub(crate) struct BackupTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    /// Public template gallery.
    pub(crate) public_templates: Vec<TemplateEntry>,
    /// Templates this guild has published.
    pub(crate) own_templates: Vec<TemplateEntry>,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/audit_log.html")]
pub(crate) struct AuditLogTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) cases: CasesResponse,
    pub(crate) warn_policy: WarnPolicy,
    pub(crate) filter_action: String,
    pub(crate) filter_moderator: String,
    pub(crate) filter_after: String,
    pub(crate) filter_before: String,
}

impl GuildTemplate {
    /// Where a health item is fixed: a tab on this page or the feature's own page.
    pub(crate) fn health_fix_url(&self, key: &str) -> String {
        let id = self.guild_id;
        match key {
            "leveling" => format!("/dashboard/guild/{id}/leveling"),
            "music_panel" => format!("/dashboard/guild/{id}/music"),
            "polls" => format!("/dashboard/guild/{id}/polls"),
            "sentinel" => format!("/dashboard/guild/{id}#sentinel"),
            _ => format!("/dashboard/guild/{id}#moderation"),
        }
    }
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/role_menus.html")]
pub(crate) struct RoleMenusTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) data: percy_client::RoleMenusResponse,
    pub(crate) channels: Vec<Channel>,
    pub(crate) roles: Vec<Role>,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/starboard.html")]
pub(crate) struct StarboardTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) starboard: percy_client::StarboardInfo,
    pub(crate) selected_channel: String,
    pub(crate) ignored: Vec<String>,
    pub(crate) channels: Vec<Channel>,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild/notifications.html")]
pub(crate) struct NotificationsTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) unread: u32,
    pub(crate) newest_id: String,
    pub(crate) feed: Vec<crate::notifications::FeedRow>,
    pub(crate) pending: Vec<crate::notifications::RequestRow>,
    pub(crate) history: Vec<crate::notifications::RequestRow>,
    pub(crate) cases: Vec<crate::notifications::CaseRow>,
    pub(crate) selected_channel: String,
    pub(crate) selected_roles: Vec<String>,
    pub(crate) settings_ok: bool,
    pub(crate) channels: Vec<Channel>,
    pub(crate) roles: Vec<Role>,
    pub(crate) notice: String,
    pub(crate) notice_ok: bool,
    pub(crate) changes: Vec<crate::db::Change>,
    pub(crate) retention_days: u32,
}
/// The first (newest) play of each track: same link, or same title and artist when a play has
/// no link. History arrives newest first.
pub(crate) fn recent_unique(history: &[MusicHistoryItem], limit: usize) -> Vec<&MusicHistoryItem> {
    let mut seen = std::collections::HashSet::new();
    history
        .iter()
        .filter(|t| {
            let key = match t.uri.as_deref().filter(|u| !u.is_empty()) {
                Some(uri) => (uri.to_string(), String::new()),
                None => (t.title.to_lowercase(), t.author.as_deref().unwrap_or("").to_lowercase()),
            };
            seen.insert(key)
        })
        .take(limit)
        .collect()
}

#[cfg(test)]
mod recent_tests {
    use super::*;

    fn play(title: &str, uri: Option<&str>) -> MusicHistoryItem {
        MusicHistoryItem {
            title: title.into(),
            uri: uri.map(Into::into),
            ..Default::default()
        }
    }

    #[test]
    fn recent_unique_keeps_the_newest_play_of_each_track() {
        let h = vec![
            play("A", Some("u1")),
            play("B", Some("u2")),
            play("A", Some("u1")),
            play("C", None),
            play("c", None),
        ];
        let titles: Vec<_> = recent_unique(&h, 12).iter().map(|t| t.title.as_str()).collect();
        assert_eq!(titles, ["A", "B", "C"]);
        assert_eq!(recent_unique(&h, 2).len(), 2);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The analytics page renders its headline tiles, chart container, and top-command rows.
    #[test]
    fn analytics_template_renders() {
        let html = AnalyticsTemplate {
            account: Some(Viewer { name: "ferris".into() }),
            guild_id: 42,
            guild_name: "Test".into(),
            nav_active: "analytics",
            page_title: "Analytics",
            summary: AnalyticsSummary {
                range: "30d".into(),
                commands: AnalyticsMetric { value: 120, delta: 15 },
                new_members: AnalyticsMetric { value: 8, delta: -2 },
                member_count: Some(500),
                top_commands: vec![AnalyticsTopCommand {
                    command: "ping".into(),
                    uses: 40,
                }],
            },
            range: "30d",
            initial_series_json: "[{\"bucket\":\"2026-07-01T00:00:00+00:00\",\"value\":3}]".into(),
        }
        .render()
        .expect("render analytics");
        assert!(html.contains("Top commands"));
        assert!(html.contains("ping"));
        assert!(html.contains("analytics/series") || html.contains("analytics.js"));
    }

    /// The shop section lists custom items with edit controls and Percy's own items without.
    #[test]
    fn economy_shop_renders_items() {
        let item = |id, name: &str, builtin: Option<&str>, stock| ShopItem {
            id,
            name: name.into(),
            description: Some("desc".into()),
            price: 500,
            effect: Some("none".into()),
            effect_value: None,
            duration_minutes: None,
            emoji: Some("\u{1f381}".into()),
            rarity: Some("epic".into()),
            category: Some("collectible".into()),
            stock,
            max_per_user: Some(3),
            required_role_id: Some("7".into()),
            available_until: Some("2026-12-01T00:00:00".into()),
            remove_role_id: None,
            reply_message: None,
            builtin_key: builtin.map(Into::into),
            for_sale: true,
        };
        let html = EconomyTemplate {
            account: Some(Viewer { name: "ferris".into() }),
            guild_id: 42,
            guild_name: "Test".into(),
            nav_active: "economy",
            page_title: "Economy",
            economy: EconomyInfo {
                items: vec![
                    item(1, "Custom Crate", None, Some(0)),
                    item(2, "Padlock", Some("padlock"), None),
                ],
                lottery: None,
                settings: None,
            },
            eco_settings: EconomySettings::default(),
            balances: BalancesResponse {
                entries: Vec::new(),
                total: 0,
            },
            channels: Vec::new(),
            roles: Vec::new(),
        }
        .render()
        .expect("render economy");
        assert!(html.contains("Custom Crate") && html.contains("Padlock"));
        assert!(html.contains("sold out") && html.contains("\u{221e}"));
        assert!(html.contains("Percy item"));
        // Exactly one row (the custom item) carries edit/delete controls.
        assert_eq!(html.matches("edit-item-btn\"").count(), 1);
        assert_eq!(html.matches("delete-item-btn\"").count(), 1);
    }

    /// The webhooks page renders a subscription row + the event checkboxes.
    #[test]
    fn webhooks_template_renders() {
        let html = WebhooksTemplate {
            account: Some(Viewer { name: "ferris".into() }),
            guild_id: 42,
            guild_name: "Test".into(),
            nav_active: "webhooks",
            page_title: "Webhooks",
            subscriptions: vec![WebhookSubscription {
                id: 1,
                url: "https://example.com/hook".into(),
                events: vec!["member.join".into()],
                label: Some("My hook".into()),
                enabled: true,
                failure_count: 0,
                ..Default::default()
            }],
            events: vec!["member.join".into(), "level.up".into()],
        }
        .render()
        .expect("render webhooks");
        assert!(html.contains("https://example.com/hook"));
        assert!(html.contains("member.join"));
        assert!(html.contains("level.up")); // the event picker checkbox
    }

    /// The backup page renders the export/import controls and both template lists.
    #[test]
    fn backup_template_renders() {
        let html = BackupTemplate {
            account: Some(Viewer { name: "ferris".into() }),
            guild_id: 42,
            guild_name: "Test".into(),
            nav_active: "backup",
            page_title: "Backup & Templates",
            public_templates: vec![TemplateEntry {
                slug: "gaming-starter".into(),
                name: "Gaming Starter".into(),
                description: Some("A kit".into()),
                downloads: 3,
                ..Default::default()
            }],
            own_templates: Vec::new(),
        }
        .render()
        .expect("render backup");
        assert!(html.contains("Download backup"));
        assert!(html.contains("gaming-starter"));
        assert!(html.contains("Template gallery"));
    }
}
