//! Public marketing / site pages — privacy policy, terms, the commands catalogue,
//! the changelog, the bot-version JSON probe, and the support-server redirect.
//! Ported 1:1 from the monolith; the changelog and bot version come from Percy's
//! internal API, the legal docs are fetched (and cached) as CommonMark from the
//! canonical GitHub repo and rendered to HTML.

use std::{
    collections::HashMap,
    sync::{Arc, Mutex, OnceLock},
    time::{Duration, Instant},
};

use axum::{
    extract::State,
    http::StatusCode,
    response::{IntoResponse, Redirect, Response},
    Json,
};
use percy_client::CommandInfo;

use crate::{
    session::Session,
    templates::{
        ChangelogRelease, ChangelogSection, CommandCategory, HomeModule, HomeStats, HomeTemplate,
        PercyChangelogTemplate, PercyCommandsTemplate, PercyLegalTemplate, PublicCommand,
    },
    view::Viewer,
    AppState,
};

/// Canonical sources for Percy's public legal docs, fetched live and rendered.
const PRIVACY_POLICY_URL: &str = "https://raw.githubusercontent.com/klappstuhlpy/Percy/master/PRIVACY_POLICY.md";
const TERMS_OF_SERVICE_URL: &str = "https://raw.githubusercontent.com/klappstuhlpy/Percy/master/TERMS_OF_SERVICE.md";

/// How long a fetched-and-rendered legal doc stays cached (the docs change only
/// when the canonical GitHub repo does).
const LEGAL_DOC_TTL: Duration = Duration::from_secs(3600);

/// A time-stamped, rendered legal doc keyed by its source URL.
type LegalDocCache = Mutex<HashMap<&'static str, (Instant, Arc<String>)>>;
/// Rendered local markdown files keyed by path, cached for the process lifetime.
type MdFileCache = Mutex<HashMap<String, Arc<String>>>;

/// Optional viewer chrome for the site layout — `None` when logged out.
fn viewer(session: &Option<Session>) -> Option<Viewer> {
    session.as_ref().map(Viewer::from_session)
}

/// `GET /privacy-policy`
pub async fn percy_privacy(State(app): State<AppState>, session: Option<Session>) -> Response {
    render_legal_doc(&app, &session, "Privacy Policy", PRIVACY_POLICY_URL).await
}

/// `GET /terms-of-service`
pub async fn percy_terms(State(app): State<AppState>, session: Option<Session>) -> Response {
    render_legal_doc(&app, &session, "Terms of Service", TERMS_OF_SERVICE_URL).await
}

async fn render_legal_doc(
    app: &AppState,
    session: &Option<Session>,
    title: &'static str,
    url: &'static str,
) -> Response {
    match cached_legal_doc(app, url).await {
        Ok(content) => PercyLegalTemplate {
            account: viewer(session),
            title,
            content: (*content).clone(),
        }
        .into_response(),
        Err(e) => {
            tracing::warn!(error = %e, url, "failed to fetch Percy legal doc");
            (
                StatusCode::BAD_GATEWAY,
                "Could not load this document right now. Please try again shortly.",
            )
                .into_response()
        }
    }
}

/// `raw GitHub url -> (fetched_at, rendered HTML)`.
fn legal_doc_cache() -> &'static LegalDocCache {
    static CACHE: OnceLock<LegalDocCache> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

/// Fetches a markdown document from `url`, renders it to HTML, and caches the
/// result for [`LEGAL_DOC_TTL`]. No local fallback — the page reflects the live
/// source of truth, so a fetch failure surfaces to the caller.
async fn cached_legal_doc(_app: &AppState, url: &'static str) -> Result<Arc<String>, reqwest::Error> {
    if let Some((ts, html)) = legal_doc_cache().lock().unwrap_or_else(|e| e.into_inner()).get(url) {
        if ts.elapsed() < LEGAL_DOC_TTL {
            return Ok(html.clone());
        }
    }
    let markdown = reqwest::Client::new()
        .get(url)
        .timeout(Duration::from_secs(8))
        .send()
        .await?
        .error_for_status()?
        .text()
        .await?;

    let html = Arc::new(render_markdown(&markdown));
    legal_doc_cache()
        .lock()
        .unwrap()
        .insert(url, (Instant::now(), html.clone()));
    Ok(html)
}

/// Renders CommonMark (plus tables, strikethrough, task-lists, footnotes) to
/// HTML. The input is a trusted document from our own repo, so embedded raw HTML
/// is passed through.
fn render_markdown(markdown: &str) -> String {
    use pulldown_cmark::{html, Options, Parser};
    let mut options = Options::empty();
    options.insert(Options::ENABLE_TABLES);
    options.insert(Options::ENABLE_STRIKETHROUGH);
    options.insert(Options::ENABLE_TASKLISTS);
    options.insert(Options::ENABLE_FOOTNOTES);
    let parser = Parser::new_ext(markdown, options);
    let mut out = String::new();
    html::push_html(&mut out, parser);
    out
}

/// One changelog bullet's inline markdown (bold, code, links) as HTML. Raw HTML in the
/// source is escaped rather than passed through, and the paragraph wrapper is dropped so
/// the result sits inside an `<li>`.
fn render_inline_markdown(markdown: &str) -> String {
    use pulldown_cmark::{html, Event, Parser, Tag, TagEnd};
    let events = Parser::new(markdown).filter_map(|event| match event {
        Event::Start(Tag::Paragraph) | Event::End(TagEnd::Paragraph) => None,
        Event::Html(raw) | Event::InlineHtml(raw) => Some(Event::Text(raw)),
        other => Some(other),
    });
    let mut out = String::new();
    html::push_html(&mut out, events);
    out
}

/// Local markdown files are read once and cached for the process lifetime.
fn md_files_cache() -> &'static MdFileCache {
    static CACHE: OnceLock<MdFileCache> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

fn cached_md_file(path: &str) -> Result<Arc<String>, std::io::Error> {
    if let Some(html) = md_files_cache().lock().unwrap().get(path) {
        return Ok(html.clone());
    }
    let content = std::fs::read_to_string(path)?;
    let html = Arc::new(render_markdown(&content));
    md_files_cache().lock().unwrap().insert(path.to_string(), html.clone());
    Ok(html)
}

/// Screenshots the landing page can show; each is rendered only if `static/img/home/<name>.webp` exists.
const HOME_SHOTS: [&str; 13] = [
    "1-inventory",
    "2-item",
    "3-shop",
    "4-economy-hub",
    "5-rank-card",
    "6-level-up",
    "7-music",
    "8-modlog",
    "9-request",
    "10-ai",
    "11-dashboard-overview",
    "12-dashboard-leveling",
    "13-dashboard-music",
];

/// Stats and module tiles are cached this long; a Percy outage is cached briefly so a down bot
/// does not make every page view wait on a failing call.
const HOME_TTL: Duration = Duration::from_secs(300);
const HOME_FAIL_TTL: Duration = Duration::from_secs(30);

#[derive(Clone)]
struct HomeData {
    stats: Option<HomeStats>,
    modules: Vec<HomeModule>,
    shots: Vec<&'static str>,
}

type HomeCache = Mutex<Option<(Instant, Duration, HomeData)>>;
static HOME_CACHE: HomeCache = Mutex::new(None);

/// `12345` -> `12.3k`. Truncates rather than rounds up, so the figure never overstates.
fn short_number(n: u64) -> String {
    let (div, suffix) = match n {
        0..=999 => return n.to_string(),
        1_000..=999_999 => (1_000, "k"),
        _ => (1_000_000, "M"),
    };
    let tenths = n * 10 / div;
    if tenths >= 100 || tenths.is_multiple_of(10) {
        format!("{}{suffix}", tenths / 10)
    } else {
        format!("{}.{}{suffix}", tenths / 10, tenths % 10)
    }
}

fn uptime_label(secs: f64) -> String {
    let s = secs.max(0.0) as u64;
    match (s / 86_400, s / 3_600) {
        (0, 0) => format!("{}m", s / 60),
        (0, h) => format!("{h}h"),
        (d, _) => format!("{d}d"),
    }
}

async fn load_home(app: &AppState) -> HomeData {
    let shots = HOME_SHOTS
        .into_iter()
        .filter(|n| {
            std::path::Path::new("static/img/home")
                .join(format!("{n}.webp"))
                .is_file()
        })
        .collect();
    let Some(percy) = &app.percy else {
        return HomeData {
            stats: None,
            modules: Vec::new(),
            shots,
        };
    };
    let (stats, commands) = tokio::join!(percy.get_bot_stats(), percy.get_bot_commands());
    let categories = commands.map(group_commands_by_category).unwrap_or_default();
    let commands_total: usize = categories.iter().map(|c| c.count).sum();
    let stats = stats.ok().map(|s| HomeStats {
        servers: short_number(s.guild_count as u64),
        members: short_number(s.user_count as u64),
        commands: if commands_total > 0 {
            commands_total.to_string()
        } else {
            String::new()
        },
        uptime: uptime_label(s.uptime_seconds),
    });
    let modules = categories
        .into_iter()
        .map(|c| HomeModule {
            name: c.name,
            slug: c.slug,
            count: c.count,
        })
        .collect();
    HomeData { stats, modules, shots }
}

/// `GET /` — the landing page: hero, live stats, showcases and the module directory.
pub async fn percy_home(State(app): State<AppState>, session: Option<Session>) -> Response {
    let cached = {
        let guard = HOME_CACHE.lock().unwrap();
        guard
            .as_ref()
            .filter(|(at, ttl, _)| at.elapsed() < *ttl)
            .map(|(_, _, d)| d.clone())
    };
    let data = match cached {
        Some(d) => d,
        None => {
            let d = load_home(&app).await;
            let ttl = if d.stats.is_some() { HOME_TTL } else { HOME_FAIL_TTL };
            *HOME_CACHE.lock().unwrap() = Some((Instant::now(), ttl, d.clone()));
            d
        }
    };
    HomeTemplate {
        account: viewer(&session),
        invite_url: crate::guilds::build_invite_url(&app.config.invite_client_id(), None),
        stats: data.stats,
        modules: data.modules,
        shots: data.shots,
    }
    .into_response()
}

/// `GET /commands` — public commands catalogue, grouped by category.
pub async fn percy_commands(State(app): State<AppState>, session: Option<Session>) -> Response {
    let flags_md = match cached_md_file("static/discord_command_syntax_guide.md") {
        Ok(content) => (*content).clone(),
        Err(e) => {
            tracing::warn!(error = %e, "failed to read static/discord_command_syntax_guide.md");
            "# Command Syntax & Flags Guide".to_string()
        }
    };

    let categories = if let Some(percy) = &app.percy {
        match percy.get_bot_commands().await {
            Ok(commands) => group_commands_by_category(commands),
            Err(_) => Vec::new(),
        }
    } else {
        Vec::new()
    };

    let total = categories.iter().map(|c| c.count).sum();
    let json_ld = commands_json_ld(&categories);
    PercyCommandsTemplate {
        account: viewer(&session),
        categories,
        flags_md,
        total,
        json_ld,
    }
    .into_response()
}

/// Bot permissions every command needs (Percy's default `PermissionTemplate.bot`); listing them on
/// every row would be noise, so only the extras are shown.
const BASELINE_BOT_PERMISSIONS: [&str; 5] = [
    "view_channel",
    "send_messages",
    "embed_links",
    "read_message_history",
    "use_external_emojis",
];

/// `economy shop add` -> `economy-shop-add`; also used for category ids.
fn slugify(s: &str) -> String {
    let mut out = String::new();
    for c in s.to_lowercase().chars() {
        if c.is_ascii_alphanumeric() {
            out.push(c);
        } else if !out.ends_with('-') {
            out.push('-');
        }
    }
    out.trim_matches('-').to_string()
}

/// `manage_roles` -> `Manage Roles`.
fn permission_label(flag: &str) -> String {
    flag.split('_')
        .map(|w| {
            let mut c = w.chars();
            c.next()
                .map(|f| f.to_uppercase().chain(c).collect::<String>())
                .unwrap_or_default()
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn duration_label(secs: f64) -> String {
    let s = secs.round() as u64;
    // Largest unit that divides evenly, so 90 s stays "90s" rather than rounding to "1m".
    for (unit, size) in [("d", 86400), ("h", 3600), ("m", 60)] {
        if s >= size && s.is_multiple_of(size) {
            return format!("{}{unit}", s / size);
        }
    }
    format!("{s}s")
}

fn public_command(cmd: &CommandInfo) -> PublicCommand {
    let search = format!("{} {} {}", cmd.name, cmd.aliases.join(" "), cmd.description).to_lowercase();
    let help = cmd
        .help
        .as_deref()
        .map(str::trim)
        // The first docstring line is already shown as the description.
        .filter(|h| !h.is_empty() && *h != cmd.description.trim())
        .map(str::to_string);
    PublicCommand {
        name: cmd.name.clone(),
        slug: slugify(&cmd.name),
        description: cmd.description.clone(),
        help,
        usage: cmd.signature.clone().filter(|s| !s.is_empty()),
        example: cmd.example.clone().filter(|s| !s.is_empty()),
        aliases: cmd.aliases.clone(),
        slash: cmd.slash,
        user_permissions: cmd.user_permissions.iter().map(|p| permission_label(p)).collect(),
        bot_permissions: cmd
            .bot_permissions
            .iter()
            .filter(|p| !BASELINE_BOT_PERMISSIONS.contains(&p.as_str()))
            .map(|p| permission_label(p))
            .collect(),
        cooldown: cmd.cooldown.as_ref().map(|c| {
            let uses = if c.rate == 1 {
                "use".to_string()
            } else {
                "uses".to_string()
            };
            format!("{} {uses} per {}", c.rate, duration_label(c.per))
        }),
        search,
        subs: Vec::new(),
    }
}

fn count_commands(cmds: &[PublicCommand]) -> usize {
    cmds.iter().map(|c| 1 + c.subs.len()).sum()
}

/// Groups a flat list of [`CommandInfo`] into sorted categories, nesting subcommands under the
/// group that owns them.
fn group_commands_by_category(commands: Vec<CommandInfo>) -> Vec<CommandCategory> {
    let mut map: HashMap<String, Vec<CommandInfo>> = HashMap::new();
    for cmd in commands {
        let category = cmd.cog.clone().unwrap_or_else(|| cmd.category.clone());
        map.entry(category).or_default().push(cmd);
    }
    let mut categories: Vec<CommandCategory> = map
        .into_iter()
        .map(|(name, cmds)| {
            // name -> parent, to find the root group of a (possibly deeply nested) subcommand.
            let parents: HashMap<&str, &str> = cmds
                .iter()
                .filter_map(|c| c.parent.as_deref().map(|p| (c.name.as_str(), p)))
                .collect();
            let names: std::collections::HashSet<&str> = cmds.iter().map(|c| c.name.as_str()).collect();
            let root_of = |name: &str| -> String {
                let mut n = name.to_string();
                while let Some(p) = parents.get(n.as_str()) {
                    if !names.contains(p) {
                        break;
                    }
                    n = (*p).to_string();
                }
                n
            };
            let mut roots: Vec<PublicCommand> = Vec::new();
            let mut subs: Vec<(String, PublicCommand)> = Vec::new();
            for c in &cmds {
                let pc = public_command(c);
                let root = root_of(&c.name);
                if root == c.name {
                    roots.push(pc);
                } else {
                    subs.push((root, pc));
                }
            }
            roots.sort_by(|a, b| a.name.cmp(&b.name));
            subs.sort_by(|a, b| a.1.name.cmp(&b.1.name));
            for (root, sub) in subs {
                if let Some(r) = roots.iter_mut().find(|r| r.name == root) {
                    r.subs.push(sub);
                }
            }
            CommandCategory {
                slug: format!("cat-{}", slugify(&name)),
                name,
                count: count_commands(&roots),
                commands: roots,
            }
        })
        .collect();
    categories.sort_by(|a, b| a.name.cmp(&b.name));
    categories
}

/// schema.org `ItemList` of every command, safe to embed inside `<script type="application/ld+json">`.
fn commands_json_ld(categories: &[CommandCategory]) -> String {
    let base = crate::config::public_base_url();
    let items: Vec<serde_json::Value> = categories
        .iter()
        .flat_map(|c| {
            c.commands
                .iter()
                .flat_map(|cmd| std::iter::once(cmd).chain(cmd.subs.iter()))
        })
        .enumerate()
        .map(|(i, cmd)| {
            serde_json::json!({
                "@type": "ListItem",
                "position": i + 1,
                "name": cmd.name,
                "description": cmd.description,
                "url": format!("{base}/commands#{}", cmd.slug),
            })
        })
        .collect();
    serde_json::json!({
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": "Percy commands",
        "numberOfItems": items.len(),
        "itemListElement": items,
    })
    .to_string()
    // A `</script>` inside a string value must not close the tag.
    .replace("</", "<\\/")
}

/// `GET /changelog` — version history from Percy's CHANGELOG.md via internal API.
pub async fn percy_changelog(State(app): State<AppState>, session: Option<Session>) -> Response {
    let (entries, current_version) = match &app.percy {
        Some(percy) => percy
            .get_changelog()
            .await
            .map(|resp| {
                let releases: Vec<ChangelogRelease> = resp
                    .entries
                    .into_iter()
                    .map(|e| ChangelogRelease {
                        version: e.version,
                        date: e.date,
                        sections: e
                            .sections
                            .into_iter()
                            .map(|s| ChangelogSection {
                                name: s.name,
                                slug: s.slug,
                                entries: s.entries.iter().map(|e| render_inline_markdown(e)).collect(),
                            })
                            .collect(),
                    })
                    .collect();
                let ver = resp.current_version.unwrap_or_default();
                (releases, ver)
            })
            .unwrap_or_default(),
        None => (Vec::new(), String::new()),
    };

    PercyChangelogTemplate {
        account: viewer(&session),
        entries,
        current_version,
    }
    .into_response()
}

/// `GET /bot-version` — the live bot version as JSON (for the site footer).
pub async fn percy_bot_version(State(app): State<AppState>) -> Json<serde_json::Value> {
    let version = match &app.percy {
        Some(percy) => percy.get_bot_stats().await.ok().and_then(|s| s.version),
        None => None,
    };
    Json(serde_json::json!({ "version": version }))
}

/// `GET /support` — redirect to the support Discord server.
pub async fn percy_support_server() -> Response {
    Redirect::to("https://discord.gg/3jSYQ9VNbA").into_response()
}

#[cfg(test)]
mod tests {
    use super::{group_commands_by_category, render_inline_markdown};
    use crate::templates::PercyCommandsTemplate;
    use askama::Template;
    use percy_client::CommandInfo;

    #[test]
    fn commands_page_renders_groups_subcommands_and_json_ld() {
        let commands: Vec<CommandInfo> = serde_json::from_value(serde_json::json!([
            {"name": "shop", "cog": "Economy", "description": "Browse.", "signature": null, "slash": true,
             "aliases": ["store"], "example": "shop", "help": "Browse the shop.
        More detail.", "parent": null,
             "user_permissions": [], "bot_permissions": ["send_messages", "manage_roles"], "cooldown": null},
            {"name": "shop add", "cog": "Economy", "description": "Add an item.", "signature": "<name> <price>",
             "slash": true, "parent": "shop", "user_permissions": ["administrator"], "bot_permissions": [],
             "cooldown": {"rate": 1, "per": 90.0}, "example": null, "help": null, "aliases": []},
            {"name": "ping", "cog": "Meta", "description": "Pong </script>.", "slash": false}
        ]))
        .unwrap();
        let categories = group_commands_by_category(commands);
        assert_eq!(categories.len(), 2);
        let eco = &categories[0];
        assert_eq!((eco.count, eco.commands.len(), eco.commands[0].subs.len()), (2, 1, 1));
        assert_eq!(eco.commands[0].subs[0].slug, "shop-add");
        assert_eq!(eco.commands[0].subs[0].cooldown.as_deref(), Some("1 use per 90s"));
        assert_eq!(eco.commands[0].bot_permissions, vec!["Manage Roles"]);

        let json_ld = super::commands_json_ld(&categories);
        assert!(!json_ld.contains("</"));
        let html = PercyCommandsTemplate {
            account: None,
            categories,
            flags_md: String::new(),
            total: 3,
            json_ld,
        }
        .render()
        .unwrap();
        for needle in [
            "id=\"shop-add\"",
            "Manage Roles",
            "1 use per 90s",
            "cat-economy",
            "application/ld+json",
            "store",
        ] {
            assert!(html.contains(needle), "missing {needle}");
        }
    }

    #[test]
    fn short_numbers_truncate() {
        for (n, want) in [
            (0, "0"),
            (999, "999"),
            (1_000, "1k"),
            (1_299, "1.2k"),
            (12_999, "12k"),
            (2_500_000, "2.5M"),
        ] {
            assert_eq!(super::short_number(n), want);
        }
    }

    fn home(stats: Option<crate::templates::HomeStats>) -> String {
        crate::templates::HomeTemplate {
            account: None,
            invite_url: "https://discord.com/oauth2/authorize?client_id=1".into(),
            stats,
            modules: vec![crate::templates::HomeModule {
                name: "Economy".into(),
                slug: "cat-economy".into(),
                count: 7,
            }],
            shots: vec!["1-inventory"],
        }
        .render()
        .unwrap()
    }

    #[test]
    fn landing_renders_with_and_without_stats() {
        let with = home(Some(crate::templates::HomeStats {
            servers: "1.2k".into(),
            members: "300k".into(),
            commands: "250".into(),
            uptime: "12d".into(),
        }));
        for needle in [
            "1.2k",
            "Add to Discord",
            "Log in",
            "/commands#cat-economy",
            "/static/img/home/1-inventory.webp",
            "Screenshot coming soon",
        ] {
            assert!(with.contains(needle), "missing {needle}");
        }
        let without = home(None);
        assert!(
            !without.contains("home-stats"),
            "stats bar must vanish when Percy is down"
        );
        assert!(without.contains("Add to Discord"));
    }

    #[test]
    fn landing_renders_when_signed_in() {
        let viewer = crate::view::Viewer::from_session(&crate::session::Session::new("1", "ferris"));
        let html = crate::templates::HomeTemplate {
            account: Some(viewer),
            invite_url: String::new(),
            stats: None,
            modules: Vec::new(),
            shots: Vec::new(),
        }
        .render()
        .unwrap();
        assert!(html.contains("Go to your dashboard"));
        assert!(!html.contains("Log in"));
    }

    #[test]
    fn changelog_entry_renders_inline_markdown_and_escapes_html() {
        assert_eq!(
            render_inline_markdown("Lost **Ban Members** via `ban` <script>x</script>"),
            "Lost <strong>Ban Members</strong> via <code>ban</code> &lt;script&gt;x&lt;/script&gt;"
        );
    }
}
