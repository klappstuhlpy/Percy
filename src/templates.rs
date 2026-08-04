//! Askama view templates and view-model row structs for the Percy dashboard.
//!
//! Ported wholesale from the klappstuhl_me monolith's `dashboard/templates.rs`
//! with a uniform, mechanical adjustment: the `flashes` field is dropped (no
//! template references it), `Option<Account>` becomes `Option<Viewer>`, and the
//! `percy/` template-path prefix is flattened. Structs for pages not yet wired to
//! a handler are still compiled (which validates their templates), hence the
//! module-wide dead-code allowance.
#![allow(dead_code)]

use crate::view::Viewer;
use askama::Template;
use percy_client::*;

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guilds.html")]
pub(crate) struct GuildsTemplate {
    pub(crate) account: Option<Viewer>,
    /// Servers the user can manage and Percy is in — full admin dashboard cards.
    pub(crate) managed: Vec<UserGuild>,
    /// Servers the user is in (but can't manage) where Percy is present —
    /// read-only public overview cards.
    pub(crate) public: Vec<UserGuild>,
    /// Servers the user can manage but Percy is *not* in yet — greyed "Add Percy"
    /// cards that link to the bot invite (captured from Discord OAuth).
    pub(crate) add_percy: Vec<AddPercyGuild>,
    /// Invite URL (no guild pre-selected) for the "Invite Percy" empty-state button.
    pub(crate) invite_url: String,
}

/// A greyed "Add Percy" card: a server the user administrates but Percy isn't in.
pub(crate) struct AddPercyGuild {
    pub(crate) name: String,
    pub(crate) icon_url: Option<String>,
    pub(crate) owner: bool,
    /// Bot invite URL with this guild pre-selected.
    pub(crate) invite_url: String,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild.html")]
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
    /// True when the guild loaded but a sub-resource (roles/channels) failed to
    /// fetch — the page renders, but with empty pickers, so saving could clobber
    /// config. Drives a warning banner instead of a silently hollow page.
    pub(crate) degraded: bool,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "no_discord.html")]
pub(crate) struct NoDiscordTemplate {
    pub(crate) account: Option<Viewer>,
}

/// Logged-out welcome/landing page shown at `/dashboard` to visitors who
/// haven't signed in yet. Introduces the bot and points to login/signup (which
/// redirect back to the dashboard afterwards).
#[allow(dead_code)]
#[derive(Template)]
#[template(path = "landing.html")]
pub(crate) struct PercyLandingTemplate {
    pub(crate) account: Option<Viewer>,
}

/// A public Percy legal document (Privacy Policy / Terms of Service). `content`
/// is pre-rendered, trusted HTML produced from the canonical GitHub markdown.
#[allow(dead_code)]
#[derive(Template)]
#[template(path = "legal.html")]
pub(crate) struct PercyLegalTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) title: &'static str,
    pub(crate) content: String,
}

/// Public commands page: lists all bot commands with categories and descriptions.
#[allow(dead_code)]
#[derive(Template)]
#[template(path = "commands_public.html")]
pub(crate) struct PercyCommandsTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) categories: Vec<CommandCategory>,
    pub(crate) flags_md: String,
}

/// A command category for the public commands page.
pub(crate) struct CommandCategory {
    pub(crate) name: String,
    pub(crate) commands: Vec<PublicCommand>,
}

/// A single command entry for the public commands page.
pub(crate) struct PublicCommand {
    pub(crate) name: String,
    pub(crate) description: String,
    pub(crate) usage: Option<String>,
}

/// Public changelog page showing recent version history.
#[allow(dead_code)]
#[derive(Template)]
#[template(path = "changelog.html")]
pub(crate) struct PercyChangelogTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) entries: Vec<ChangelogRelease>,
    pub(crate) current_version: String,
}

/// A changelog release with categorized sections.
pub(crate) struct ChangelogRelease {
    pub(crate) version: String,
    pub(crate) date: String,
    pub(crate) sections: Vec<ChangelogSection>,
}

/// A category section within a release (Added, Changed, Fixed, etc.).
pub(crate) struct ChangelogSection {
    pub(crate) name: String,
    pub(crate) slug: String,
    pub(crate) entries: Vec<String>,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "guild_not_found.html")]
pub(crate) struct GuildNotFoundTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) invite_url: String,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "user_dashboard.html")]
pub(crate) struct UserDashboardTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) guild_icon: Option<String>,
    pub(crate) profile: MemberSelf,
    pub(crate) settings: UserSettings,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "account.html")]
pub(crate) struct AccountTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) settings: UserSettings,
    pub(crate) discord_id: String,
    pub(crate) username: String,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "members.html")]
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
#[template(path = "leveling.html")]
pub(crate) struct LevelingTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) config: LevelingConfig,
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

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "user.html")]
pub(crate) struct UserLookupTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: String,
    pub(crate) member: MemberDetail,
}

/// Read-only public server overview shown to members who can't manage the guild.
/// Surfaces the same information a member could already see in Discord: server
/// stats, top leveling ranks, active polls/giveaways, the shop, the live music
/// player, and a short "powered by Percy" blurb.
#[allow(dead_code)]
#[derive(Template)]
#[template(path = "overview.html")]
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

/// Public leaderboard page — accessible by anyone, no login required.
#[allow(dead_code)]
#[derive(Template)]
#[template(path = "leaderboard.html")]
pub(crate) struct LeaderboardTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) guild_icon: Option<String>,
    pub(crate) leaderboard: LeaderboardResponse,
    pub(crate) balances: BalancesResponse,
    /// The vanity slug claimed for this guild, if any.
    pub(crate) vanity: Option<String>,
    /// Whether the current viewer can manage vanity settings (guild admin).
    pub(crate) can_manage: bool,
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
#[template(path = "polls.html")]
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
#[template(path = "gallery.html")]
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
#[template(path = "giveaways.html")]
pub(crate) struct GiveawaysTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) giveaways: GiveawaysResponse,
    pub(crate) channels: Vec<Channel>,
    pub(crate) active_count: usize,
    pub(crate) ended_count: usize,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "tags.html")]
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
#[template(path = "commands.html")]
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
#[template(path = "stats.html")]
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
#[template(path = "autoresponders.html")]
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
#[template(path = "economy.html")]
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

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "music.html")]
pub(crate) struct MusicTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) music: MusicInfo,
    pub(crate) guild: GuildInfo,
    pub(crate) channels: Vec<Channel>,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "comics.html")]
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
#[template(path = "temp_channels.html")]
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
#[template(path = "highlights.html")]
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
#[template(path = "emoji_stats.html")]
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
#[template(path = "analytics.html")]
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
#[template(path = "webhooks.html")]
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
#[template(path = "backup.html")]
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
#[template(path = "audit_log.html")]
pub(crate) struct AuditLogTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: &'static str,
    pub(crate) cases: CasesResponse,
    pub(crate) filter_action: String,
    pub(crate) filter_moderator: String,
    pub(crate) filter_after: String,
    pub(crate) filter_before: String,
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

// -- Watchlist ---------------------------------------------------------------

/// `GET /watchlist` — the universe index.
#[derive(Template)]
#[template(path = "watchlist.html")]
pub(crate) struct WatchlistTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) universes: Vec<WatchUniverseSummary>,
}

/// `GET /people/:slug` — one person's filmography across every universe.
#[derive(Template)]
#[template(path = "person.html")]
pub(crate) struct PersonTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) name: String,
    pub(crate) slug: String,
    /// TMDB profile image, already sized. Absent for a person TMDB has no photo of.
    pub(crate) photo_url: Option<String>,
    /// `Actor · Director` — the distinct roles they are credited with.
    pub(crate) roles: String,
    pub(crate) credit_count: usize,
    /// How many universes they appear in.
    pub(crate) universes: usize,
    /// Undated or still-ahead titles — the "what's next" the page opens with.
    pub(crate) upcoming: Vec<PersonCreditRow>,
    /// Everything already out, newest first (Percy's order, never re-sorted here).
    pub(crate) filmography: Vec<PersonCreditRow>,
}

/// One row of a person's filmography.
pub(crate) struct PersonCreditRow {
    pub(crate) universe_slug: String,
    pub(crate) universe_name: String,
    /// The universe's accent as `#RRGGBB`, so a row keeps its franchise's colour.
    pub(crate) accent: String,
    pub(crate) slug: String,
    pub(crate) title: String,
    pub(crate) year: Option<String>,
    /// `FILM` / `TV` / `SPEC`.
    pub(crate) kind_label: String,
    /// The character for an acting credit, else the role noun.
    pub(crate) role_label: String,
    pub(crate) poster_url: Option<String>,
}

/// `GET /watchlist/:slug` — one universe's ordered catalogue.
#[derive(Template)]
#[template(path = "watch_order.html")]
pub(crate) struct WatchOrderTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) universe: WatchUniverse,
    /// The curated paths offered as toggles.
    pub(crate) paths: Vec<WatchPath>,
    /// Titles grouped into their era sections, in watch order.
    pub(crate) eras: Vec<OrderEra>,
    /// The resolved path/region/sort, echoed back so the toggles keep the other two.
    pub(crate) path: String,
    pub(crate) region: String,
    pub(crate) sort: String,
    /// `story order` / `release order`, for prose in the meta description.
    pub(crate) sort_label: String,
    pub(crate) total: usize,
    pub(crate) hours: i64,
    pub(crate) essential: usize,
    pub(crate) movies: usize,
    pub(crate) shows: usize,
}

/// One era heading and the titles under it.
pub(crate) struct OrderEra {
    pub(crate) key: String,
    pub(crate) label: String,
    pub(crate) rows: Vec<OrderRow>,
}

/// One row of a watch order, with everything the template needs precomputed.
pub(crate) struct OrderRow {
    /// Position in the whole order (1-based), not within the era.
    pub(crate) position: usize,
    /// `position` zero-padded to two digits, as the row's index column shows it.
    pub(crate) number: String,
    /// `movie` / `tv` / `special` -- the modifier that colours the row and its tile.
    pub(crate) kind_class: String,
    /// The word inside the type tile: FILM, TV, SPEC.
    pub(crate) kind_label: String,
    pub(crate) slug: String,
    pub(crate) title: String,
    pub(crate) year: Option<String>,
    pub(crate) runtime_label: String,
    /// The row era, shown as a chip in story order (where era headings are absent).
    pub(crate) era: Option<String>,
    pub(crate) rating: Option<String>,
    /// `essential` / `recommended` / `optional`, doubling as the badge's modifier class.
    pub(crate) importance: Option<String>,
    pub(crate) instruction: Option<String>,
    /// The sub-brand a title belongs to (Sony's Spider-Man films, Fox's X-Men), shown
    /// as a neutral tag so a licensing detour is visible at a glance.
    pub(crate) studio: Option<String>,
    pub(crate) milestone: bool,
    pub(crate) providers: Vec<OrderProvider>,
}

/// A streaming service logo on a row.
pub(crate) struct OrderProvider {
    pub(crate) name: String,
    pub(crate) logo_url: String,
}

// -- Comics --------------------------------------------------------------------
//
// Public comic-catalogue pages (browse, one series, one release). Shaping lives
// in `comics.rs`, mirroring the watchlist split above. `comics.html` is already
// taken by the guild dashboard's comic-feed tab, so these templates are named
// `comic_*` instead.

/// A release, shaped for a card. Shared by the browse grid, a series' issue
/// list, and the detail page's previous/next/more-from-series links.
pub(crate) struct ComicCard {
    pub(crate) brand: String,
    pub(crate) slug: String,
    pub(crate) title: String,
    pub(crate) issue_label: Option<String>,
    pub(crate) series_name: Option<String>,
    pub(crate) release_date: Option<String>,
    pub(crate) cover_url: Option<String>,
    pub(crate) price_label: Option<String>,
}

/// One brand-filter tab on the browse page.
pub(crate) struct BrandTab {
    pub(crate) label: String,
    pub(crate) href: String,
    pub(crate) active: bool,
}

/// One credited role and everyone credited under it on a release.
pub(crate) struct CreditGroup {
    pub(crate) role: String,
    pub(crate) names: Vec<String>,
}

/// `GET /comics` — the release browser.
#[derive(Template)]
#[template(path = "comic_browse.html")]
pub(crate) struct ComicBrowseTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) items: Vec<ComicCard>,
    pub(crate) brand_tabs: Vec<BrandTab>,
    /// Echoed back so the search form keeps the active brand filter.
    pub(crate) brand: Option<String>,
    pub(crate) search: String,
    pub(crate) total: i64,
    pub(crate) page: i64,
    pub(crate) total_pages: i64,
    pub(crate) prev_href: Option<String>,
    pub(crate) next_href: Option<String>,
    /// Percy could not be reached, so an empty grid is an outage rather than a
    /// filter that matched nothing. The two need different wording.
    pub(crate) unavailable: bool,
    /// Empty on this page: results mix brands, so the attribution partial
    /// shows only the general League of Comic Geeks / VIZ notice.
    pub(crate) comic_copyright: String,
}

/// `GET /comics/series` — every series as a card.
#[derive(Template)]
#[template(path = "comic_series_index.html")]
pub(crate) struct ComicSeriesIndexTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) series: Vec<ComicSeriesSummary>,
    /// Always empty here for the same reason as `ComicBrowseTemplate`'s.
    pub(crate) comic_copyright: String,
}

/// `GET /comics/series/:slug` — one series, issues in reading order.
#[derive(Template)]
#[template(path = "comic_series.html")]
pub(crate) struct ComicSeriesTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) series_slug: String,
    pub(crate) series_name: String,
    pub(crate) brand: String,
    pub(crate) brand_label: String,
    /// The page's one `--comic-accent`, resolved from `brand`.
    pub(crate) accent: String,
    pub(crate) publisher: Option<String>,
    pub(crate) issue_count: i64,
    /// Already in reading order as Percy returned them — never re-sorted.
    pub(crate) issues: Vec<ComicCard>,
    /// The publisher copyright line for the attribution partial; empty when
    /// the series (and so its brand) couldn't be resolved.
    pub(crate) comic_copyright: String,
}

/// `GET /comics/:brand/:slug` — one release.
#[derive(Template)]
#[template(path = "comic_detail.html")]
pub(crate) struct ComicDetailTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) brand: String,
    pub(crate) brand_label: String,
    /// The page's one `--comic-accent`, resolved from `brand`.
    pub(crate) accent: String,
    /// Empty when Percy is unreachable or the release doesn't exist — the
    /// template's empty-state signal, the same way `watch_order.html` checks
    /// `eras.is_empty()`.
    pub(crate) title: String,
    pub(crate) slug: String,
    pub(crate) issue_label: Option<String>,
    pub(crate) release_date: Option<String>,
    pub(crate) cover_url: Option<String>,
    pub(crate) format: Option<String>,
    pub(crate) page_count: Option<i64>,
    pub(crate) price_label: Option<String>,
    pub(crate) publisher: Option<String>,
    pub(crate) series_name: Option<String>,
    pub(crate) series_slug: Option<String>,
    pub(crate) previous: Option<ComicCard>,
    pub(crate) next: Option<ComicCard>,
    pub(crate) more_from_series: Vec<ComicCard>,
    pub(crate) credit_groups: Vec<CreditGroup>,
    pub(crate) characters: Vec<ComicCharacter>,
    pub(crate) description: Option<String>,
    pub(crate) comic_copyright: String,
}
