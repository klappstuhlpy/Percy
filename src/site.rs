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
        ChangelogEntry, CommandCategory, PercyChangelogTemplate, PercyCommandsTemplate, PercyLegalTemplate,
        PublicCommand,
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
fn legal_doc_cache() -> &'static Mutex<HashMap<&'static str, (Instant, Arc<String>)>> {
    static CACHE: OnceLock<Mutex<HashMap<&'static str, (Instant, Arc<String>)>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

/// Fetches a markdown document from `url`, renders it to HTML, and caches the
/// result for [`LEGAL_DOC_TTL`]. No local fallback — the page reflects the live
/// source of truth, so a fetch failure surfaces to the caller.
async fn cached_legal_doc(_app: &AppState, url: &'static str) -> Result<Arc<String>, reqwest::Error> {
    if let Some((ts, html)) = legal_doc_cache().lock().unwrap().get(url) {
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

/// Local markdown files are read once and cached for the process lifetime.
fn md_files_cache() -> &'static Mutex<HashMap<String, Arc<String>>> {
    static CACHE: OnceLock<Mutex<HashMap<String, Arc<String>>>> = OnceLock::new();
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

    PercyCommandsTemplate {
        account: viewer(&session),
        categories,
        flags_md,
    }
    .into_response()
}

/// Groups a flat list of [`CommandInfo`] into sorted categories for display.
fn group_commands_by_category(commands: Vec<CommandInfo>) -> Vec<CommandCategory> {
    let mut map: HashMap<String, Vec<PublicCommand>> = HashMap::new();
    for cmd in commands {
        let category = cmd.cog.unwrap_or(cmd.category);
        map.entry(category).or_default().push(PublicCommand {
            name: cmd.name,
            description: cmd.description,
            usage: cmd.signature,
        });
    }
    let mut categories: Vec<CommandCategory> = map
        .into_iter()
        .map(|(name, mut commands)| {
            commands.sort_by(|a, b| a.name.cmp(&b.name));
            CommandCategory { name, commands }
        })
        .collect();
    categories.sort_by(|a, b| a.name.cmp(&b.name));
    categories
}

/// `GET /changelog` — version history from Percy's internal API.
pub async fn percy_changelog(State(app): State<AppState>, session: Option<Session>) -> Response {
    let entries: Vec<ChangelogEntry> = match &app.percy {
        Some(percy) => percy
            .get_changelog()
            .await
            .map(|resp| {
                resp.entries
                    .into_iter()
                    .map(|e| ChangelogEntry {
                        version: e.version,
                        date: e.date,
                        changes: e.changes,
                    })
                    .collect()
            })
            .unwrap_or_default(),
        None => Vec::new(),
    };

    PercyChangelogTemplate {
        account: viewer(&session),
        entries,
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
