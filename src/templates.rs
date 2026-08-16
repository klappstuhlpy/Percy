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
    /// The news ticker's headlines. Empty means no strip at all — see `news::ticker`.
    pub(crate) ticker: Vec<TickerItem>,
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
    /// The universes they appear in, deduplicated in the order they first appear in the
    /// credits — never re-sorted, so the first title Percy lists is also the first link.
    pub(crate) universe_links: Vec<PersonUniverseLink>,
    /// Undated or still-ahead titles — the "what's next" the page opens with, unsplit by
    /// role since it is a short, mixed list.
    pub(crate) upcoming: Vec<PersonCreditRow>,
    /// Everything already out, grouped by role (cast, director, writer, creator, in that
    /// order) and present only for a role this person actually has a credit under.
    pub(crate) role_groups: Vec<PersonRoleGroup>,
    /// §6.2's related-news strip for this person. Empty means the section is absent, not
    /// an empty box — same rule as `ComicDetailTemplate::related_news`.
    pub(crate) related_news: Vec<NewsRow>,
    /// Everything tagged with this person, on `/news`.
    pub(crate) related_news_href: String,
    /// `https://www.themoviedb.org/person/{id}` — the attribution §9 requires on any page
    /// showing TMDB data. Absent when Percy has no id for the person (the error page).
    pub(crate) tmdb_person_id: Option<i64>,
}

/// One universe this person has a credit in, linked from the page header.
pub(crate) struct PersonUniverseLink {
    pub(crate) slug: String,
    pub(crate) name: String,
}

/// One role's section of a person's filmography — "Cast", "Director", "Writer",
/// "Creator" — rendered only when the person has at least one credit under it.
pub(crate) struct PersonRoleGroup {
    pub(crate) role_label: String,
    pub(crate) rows: Vec<PersonCreditRow>,
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
    /// The role noun — "Actor" / "Director" / "Writer" / "Creator".
    pub(crate) role_label: String,
    /// The character played, for a cast credit TMDB actually credited. `None` for crew
    /// and for an uncredited actor — the row then falls back to `role_label` alone.
    pub(crate) character: Option<String>,
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
    /// `N of M watched` over exactly the rows below, or `None` signed out — see
    /// `ProgressSummary::new`.
    pub(crate) progress: Option<ProgressSummary>,
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
    pub(crate) poster_url: Option<String>,
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
    /// The watched control, keyed `{universe}/{slug}` — `release_progress`, not V39's
    /// per-title table; `progress.rs`'s module doc says why.
    pub(crate) toggle: ProgressToggle,
}

/// A streaming service logo on a row.
pub(crate) struct OrderProvider {
    pub(crate) name: String,
    pub(crate) logo_url: String,
}

/// `GET /watchlist/:universe/:slug` — one title as a destination of its own.
///
/// Sections follow the plan's §6.2 in order. Everything is precomputed here rather than
/// in the template, exactly as `OrderRow` is, so the markup stays a layout and the rules
/// (what an empty runtime prints, which offer group leads) stay testable.
#[derive(Template)]
#[template(path = "watch_title.html")]
pub(crate) struct WatchTitleTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) universe: WatchUniverse,
    /// Empty when Percy is unreachable or the title doesn't exist — the template's
    /// empty-state signal, the same check `comic_detail.html` makes.
    pub(crate) title: String,
    pub(crate) slug: String,
    /// The watched control, keyed `{universe}/{slug}` — the same key the order row uses,
    /// so marking a title here and marking it in the order are one row.
    pub(crate) toggle: ProgressToggle,
    pub(crate) year: Option<String>,
    /// `Film` / `Series` / `Special`, spelled out — the hero has room for a word.
    pub(crate) kind_label: String,
    pub(crate) backdrop_url: Option<String>,
    pub(crate) poster_url: Option<String>,
    /// The curator's one-line "why it matters" (`context`), not TMDB's synopsis.
    pub(crate) context: Option<String>,
    pub(crate) overview: Option<String>,
    // -- Fact panel --
    pub(crate) runtime_label: String,
    pub(crate) rating: Option<String>,
    pub(crate) votes: Option<i64>,
    pub(crate) release_date: Option<String>,
    /// The sub-brand a licensing detour belongs to (Sony's Spider-Man films).
    pub(crate) studio: Option<String>,
    pub(crate) era: Option<String>,
    // -- Where it fits --
    pub(crate) position: i64,
    pub(crate) total: i64,
    /// The curated path whose importance this title carries, humanised.
    pub(crate) path_name: Option<String>,
    pub(crate) importance: Option<String>,
    pub(crate) instruction: Option<String>,
    pub(crate) milestone: bool,
    pub(crate) sort_label: String,
    /// Back to the ordering this position is measured in, carrying path/region/sort.
    pub(crate) order_href: String,
    /// `?sort=…&region=…` — appended to every link to a sibling title, so walking
    /// previous/next never silently drops back to story order in the default region.
    pub(crate) link_query: String,
    pub(crate) previous: Option<TitleNeighbour>,
    pub(crate) next: Option<TitleNeighbour>,
    /// The title whose credits scene this entry really is, when it is one.
    pub(crate) credits_of: Option<TitleNeighbour>,
    // -- Streaming --
    pub(crate) region: String,
    pub(crate) regions: Vec<RegionOption>,
    /// Providers grouped by offer, in a fixed order (stream, rent, buy).
    pub(crate) provider_groups: Vec<ProviderGroup>,
    // -- Cast & crew --
    pub(crate) cast: Vec<TitleCredit>,
    pub(crate) crew: Vec<TitleCredit>,
    /// A long cast starts collapsed — a `<details>`, so it opens with JS off.
    pub(crate) cast_collapsed: bool,
    // -- Related news (§6.2's 6b): absent rather than empty --
    pub(crate) related_news: Vec<NewsRow>,
    pub(crate) related_news_href: String,
    // -- Trailer: rendered only when Percy selected one --
    pub(crate) trailer: Option<TrailerView>,
    /// The curator's spoiler note, shown only inside a collapsed `<details>`.
    pub(crate) spoiler: Option<String>,
    /// Signed out, the action row collapses to one "Sign in to track" link.
    pub(crate) signed_in: bool,
}

/// A neighbouring title, or the one this entry plays the credits of.
pub(crate) struct TitleNeighbour {
    pub(crate) slug: String,
    pub(crate) title: String,
    pub(crate) year: Option<String>,
    pub(crate) poster_url: Option<String>,
}

/// One offer type's providers — "Stream", "Rent", "Buy".
pub(crate) struct ProviderGroup {
    pub(crate) label: String,
    pub(crate) providers: Vec<OrderProvider>,
}

/// One option in the region form's `<select>`.
pub(crate) struct RegionOption {
    pub(crate) code: String,
    pub(crate) name: String,
    pub(crate) selected: bool,
}

/// One credited person on a title.
pub(crate) struct TitleCredit {
    pub(crate) name: String,
    pub(crate) slug: String,
    /// The character for an acting credit, else the role noun.
    pub(crate) role_label: String,
    pub(crate) photo_url: Option<String>,
}

/// The one trailer, ready to render: a link that JS upgrades into an iframe.
pub(crate) struct TrailerView {
    /// TMDB's video title, or a plain "Trailer" when it carries none.
    pub(crate) label: String,
    /// `YouTube` / `Vimeo` — named on the fallback link so the destination is stated.
    pub(crate) site: String,
    /// Where a JS-off click goes. Never omitted.
    pub(crate) url: String,
    /// What the click-to-load swaps in. Never in the initial HTML.
    pub(crate) embed_url: String,
}

// -- Release overview ----------------------------------------------------------

/// `GET /releases` — the front door: what just landed and what is imminent.
#[derive(Template)]
#[template(path = "releases.html")]
pub(crate) struct ReleasesTemplate {
    pub(crate) account: Option<Viewer>,
    /// Half-width of the window, in days — the `days` control's current value.
    pub(crate) days: u32,
    /// The sort control's current value (`date`, `date_asc` or `name`) — echoed into the
    /// window form's hidden field so choosing a window never resets the sort.
    pub(crate) sort: &'static str,
    /// The window's ends, as Percy reported them (ISO `YYYY-MM-DD`).
    pub(crate) window_start: String,
    pub(crate) window_end: String,
    /// The cards the viewer's subscriptions matched. Empty signed out, and the section
    /// is omitted rather than shown empty — see `releases.rs`.
    pub(crate) followed: Vec<ReleaseCardRow>,
    pub(crate) comics: Vec<ReleaseCardRow>,
    pub(crate) titles: Vec<ReleaseCardRow>,
    /// The news ticker's headlines. Empty means no strip at all — see `news::ticker`.
    pub(crate) ticker: Vec<TickerItem>,
    /// A signed-out visitor gets a one-line invitation instead of the `Following` section.
    pub(crate) signed_in: bool,
    /// Percy could not be reached — an empty page is an outage, not an empty catalogue.
    /// The two need different wording, exactly as on `/comics`.
    pub(crate) unavailable: bool,
    /// Empty here: the page mixes brands, so the attribution partial shows only the
    /// general League of Comic Geeks / VIZ notice.
    pub(crate) comic_copyright: String,
}

/// One overview card. Both media flatten into this — the differences (which CDN prefixes
/// the art, where the link points) are resolved in `releases.rs`, not in the template.
pub(crate) struct ReleaseCardRow {
    /// `comic` or `title`, doubling as the card's modifier class.
    pub(crate) media: String,
    /// COMIC / FILM & TV — the tile that says which catalogue a card came from.
    pub(crate) media_label: String,
    /// Where the card links on *this* site. Never the external source listing: a
    /// catalogue page that sends its traffic off-site on the first click is a link farm.
    pub(crate) href: String,
    pub(crate) name: String,
    pub(crate) subtitle: Option<String>,
    pub(crate) release_date: Option<String>,
    /// Already CDN-prefixed for titles.
    pub(crate) cover_url: Option<String>,
    pub(crate) chips: Vec<ChipRow>,
    /// The read/watched control, keyed by the card's own `key`.
    pub(crate) toggle: ProgressToggle,
}

// -- Progress ------------------------------------------------------------------

/// One read/watched control, as `templates/progress_toggle.html` renders it.
///
/// Everything the markup needs is resolved in `progress.rs` — which verb this medium
/// takes, whether it is already marked, where a JS-off POST comes back to — so the macro
/// stays a layout and the rules stay testable.
pub(crate) struct ProgressToggle {
    /// Signed out this is not a dead button: the macro renders the sign-in link instead.
    pub(crate) signed_in: bool,
    pub(crate) marked: bool,
    /// `comic` / `title` — half of the identity, and what decides the verb.
    pub(crate) media: String,
    /// The card's `key` verbatim (`{brand}/{slug}`, `{universe}/{slug}`).
    pub(crate) release_key: String,
    /// `read` / `watched` — Percy 400s the wrong one for a medium rather than coercing it.
    pub(crate) status: String,
    /// `Mark read` / `Mark watched`.
    pub(crate) label: String,
    /// `Read` / `Watched` — what the control says once it is on.
    pub(crate) marked_label: String,
    /// The page this control is on, posted back so a JS-off click lands where it started.
    pub(crate) redirect: String,
    pub(crate) sign_in_href: String,
}

/// `done of total` on an order page, plus the percentage its bar is drawn at.
pub(crate) struct ProgressSummary {
    pub(crate) done: usize,
    pub(crate) total: usize,
    pub(crate) percent: usize,
    /// `read` / `watched` — the participle the count is phrased with.
    pub(crate) verb: String,
}

impl ProgressSummary {
    /// The summary for a page, or `None` when there is nothing to count (signed out, or
    /// an empty list) — an order page must not draw a "0 of 0" bar.
    pub(crate) fn new(signed_in: bool, done: usize, total: usize, verb: &str) -> Option<Self> {
        (signed_in && total > 0).then(|| Self {
            done,
            total,
            percent: done * 100 / total,
            verb: verb.to_string(),
        })
    }
}

/// One subject chip. `href` is `None` when no catalogue page exists for that subject —
/// a franchise, a creator — and the chip renders as plain text rather than a dead link.
pub(crate) struct ChipRow {
    pub(crate) label: String,
    pub(crate) href: Option<String>,
}

// -- News ----------------------------------------------------------------------

/// `GET /news` — the stream, filterable and paged.
#[derive(Template)]
#[template(path = "news.html")]
pub(crate) struct NewsTemplate {
    pub(crate) account: Option<Viewer>,
    /// A signed-out visitor gets a one-line invitation instead of the followed markers.
    pub(crate) signed_in: bool,
    /// One list, in Percy's order — the followed stories already lead it.
    pub(crate) rows: Vec<NewsRow>,
    /// Options for the source `<select>`; empty when the source call failed, which
    /// costs the page a filter and nothing else.
    pub(crate) sources: Vec<SourceOption>,
    /// Where "clear filters" goes — `/news`, built by the same code as every other href.
    pub(crate) clear_href: String,
    pub(crate) prev_href: Option<String>,
    pub(crate) next_href: Option<String>,
    /// Echoed back so the form keeps its state with JS off.
    pub(crate) media: String,
    /// The subject pair, carried through the form as hidden fields. `subject_value` is
    /// Percy's opaque key and travels verbatim — the label would match nothing.
    pub(crate) subject_type: Option<String>,
    pub(crate) subject_value: Option<String>,
    pub(crate) source: String,
    pub(crate) search: String,
    /// Any filter is in force, so the empty state can offer to clear them.
    pub(crate) filtered: bool,
    pub(crate) total: i64,
    pub(crate) page: i64,
    pub(crate) total_pages: i64,
    /// Percy could not be reached — an empty list is an outage, not "nothing matched".
    pub(crate) unavailable: bool,
}

/// One story, carrying **only** what §9 permits a page to show: the headline, a short
/// excerpt, the link to the original and the source's name. The payload's `image_url` and
/// `author` are deliberately absent from this struct so no template can reach them.
pub(crate) struct NewsRow {
    /// The original article. The headline links here, never to a page of ours.
    pub(crate) url: String,
    pub(crate) headline: String,
    pub(crate) excerpt: Option<String>,
    pub(crate) source_name: String,
    /// `YYYY-MM-DD`, and the full timestamp for `<time datetime>`.
    pub(crate) published: Option<String>,
    pub(crate) published_iso: Option<String>,
    /// This story matched one of the viewer's subscriptions. A quiet marker on a row in
    /// the one list — never a second list.
    pub(crate) followed: bool,
    pub(crate) chips: Vec<ChipRow>,
}

/// One option in the source filter.
pub(crate) struct SourceOption {
    pub(crate) slug: String,
    pub(crate) name: String,
    pub(crate) selected: bool,
}

/// One headline in the ticker (§6.3), rendered server-side into the page it decorates.
pub(crate) struct TickerItem {
    pub(crate) url: String,
    pub(crate) headline: String,
    pub(crate) source_name: String,
}

// -- Release subscriptions -----------------------------------------------------

/// `GET /account/subscriptions` — what the signed-in user follows, plus the picker.
#[derive(Template)]
#[template(path = "subscriptions.html")]
pub(crate) struct SubscriptionsTemplate {
    pub(crate) account: Option<Viewer>,
    /// One section per medium, in a fixed order, so the page never reshuffles.
    pub(crate) groups: Vec<SubscriptionGroup>,
    pub(crate) total: usize,
    /// The picker's current search term, echoed back so the form keeps it with JS off.
    pub(crate) query: String,
    /// Popular subjects on first paint, search results once a query is set.
    pub(crate) subjects: Vec<SubjectRow>,
    /// Percy could not be reached — an empty page is an outage, not "you follow nothing".
    pub(crate) unavailable: bool,
    /// A mutation failed and bounced back here (`?error=1`).
    pub(crate) errored: bool,
}

/// One medium's subscriptions.
pub(crate) struct SubscriptionGroup {
    pub(crate) media: String,
    pub(crate) label: String,
    pub(crate) rows: Vec<SubscriptionRow>,
}

/// One followed subject, with every control's state precomputed.
pub(crate) struct SubscriptionRow {
    pub(crate) media: String,
    pub(crate) subject_type: String,
    /// The opaque key — posted back verbatim, never the label.
    pub(crate) subject_value: String,
    pub(crate) label: String,
    /// The `Series` / `Person` / `Character` chip.
    pub(crate) type_label: String,
    pub(crate) streams: String,
    /// `dm` or `off`; `off` mutes without unfollowing.
    pub(crate) delivery: String,
    pub(crate) muted: bool,
}

/// One picker row. `followed` greys the subscribe button for something already followed.
pub(crate) struct SubjectRow {
    pub(crate) media: String,
    pub(crate) subject_type: String,
    pub(crate) subject_value: String,
    pub(crate) label: String,
    pub(crate) hint: String,
    pub(crate) followed: bool,
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
    /// The read control, on the surfaces that offer one. `None` on the detail page's
    /// neighbours and a series' issue list: those cards are navigation, and a toggle on
    /// every thumbnail would be a second, quieter way to mark the wrong issue.
    pub(crate) toggle: Option<ProgressToggle>,
}

/// One brand-filter tab on the browse page.
pub(crate) struct BrandTab {
    pub(crate) label: String,
    pub(crate) href: String,
    pub(crate) active: bool,
}

/// One credited contributor, linked to the browse page filtered to their name.
pub(crate) struct CreditLink {
    pub(crate) name: String,
    pub(crate) href: String,
}

/// One credited role and everyone credited under it on a release.
pub(crate) struct CreditGroup {
    pub(crate) role: String,
    pub(crate) credits: Vec<CreditLink>,
}

/// A character appearing in a release, linked to the browse page filtered to their name.
pub(crate) struct CharacterLink {
    pub(crate) name: String,
    pub(crate) kind: Option<String>,
    pub(crate) href: String,
}

/// One active browse filter (creator/character/series), shown as a removable chip
/// above the grid. The × is a plain link back to the same page without that
/// parameter -- no script required to undo a filter.
pub(crate) struct FilterChip {
    pub(crate) label: String,
    pub(crate) remove_href: String,
}

/// `GET /comics` — the release browser.
#[derive(Template)]
#[template(path = "comic_browse.html")]
pub(crate) struct ComicBrowseTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) items: Vec<ComicCard>,
    pub(crate) brand_tabs: Vec<BrandTab>,
    /// The news ticker's headlines. Empty means no strip at all — see `news::ticker`.
    pub(crate) ticker: Vec<TickerItem>,
    /// Echoed back so the search form and brand tabs keep the active filters.
    pub(crate) brand: Option<String>,
    pub(crate) search: String,
    pub(crate) creator: Option<String>,
    pub(crate) character: Option<String>,
    pub(crate) series: Option<String>,
    /// The active `creator`/`character`/`series` filters, each removable. Empty when
    /// none are set -- the chip row is then absent, not an empty box.
    pub(crate) filter_chips: Vec<FilterChip>,
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
    /// The read control, keyed by `{brand}/{slug}` — the same string the browse grid
    /// and a release card file their marks under, so one release is one row.
    pub(crate) toggle: ProgressToggle,
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
    pub(crate) characters: Vec<CharacterLink>,
    /// The outbound link to this issue's own listing on its source (League of Comic
    /// Geeks / VIZ) -- distinct from the general attribution notice in the footer.
    pub(crate) source_url: Option<String>,
    pub(crate) description: Option<String>,
    /// §6.2's "6b. Related news" — the most recent stories tagged with this release's
    /// series. Empty means the section is *absent*, not an empty box.
    pub(crate) related_news: Vec<NewsRow>,
    /// Everything tagged with that series, on `/news`.
    pub(crate) related_news_href: String,
    pub(crate) comic_copyright: String,
}
