//! Percy dashboard — standalone web binary.
//!
//! A configuration dashboard for [Percy](https://percy.klappstuhl.me) that runs
//! **independently out of the box**, linking only the shared crates —
//! [`kls_web_core`] (SQLite + crypto + migrations kernel), [`kls_ui`] (the design
//! system, served at `/kls/*`), and [`percy_client`] (Percy's internal API) — with
//! no dependency on the `klappstuhl_me` app crate.
//!
//! Structure (ported incrementally from the klappstuhl_me monolith):
//! - [`config`] — `config.json` (Discord OAuth, Percy API, signing key, scan token),
//! - [`db`] — typed access to the dashboard-owned tables in `dashboard.db`,
//! - [`migrations`] — the embedded schema, applied via the shared runner,
//! - the routes below render through the shared design system via Askama.

use std::{
    path::{Path, PathBuf},
    sync::Arc,
};

use askama::Template;
use axum::{
    extract::State,
    routing::{delete, get, patch, post},
    Json, Router,
};
use kls_web_core::Database;

use crate::config::Config;

mod actions;
mod auth;
mod comics;
mod config;
mod crud;
mod db;
mod errors;
mod forms;
mod guilds;
mod integrations;
mod leaderboard;
mod migrations;
mod moderation;
mod mutations;
mod news;
mod pages;
mod progress;
mod releases;
mod scan;
mod session;
mod site;
mod subscriptions;
mod templates;
mod userpages;
mod view;
mod watchlist;

/// Shared application state, threaded through every handler.
#[derive(Clone)]
struct AppState {
    config: Arc<Config>,
    db: Arc<Database>,
    /// The Percy API client, present only when Percy is configured.
    percy: Option<Arc<percy_client::PercyClient>>,
    /// Endpoint for the external malware scan service (klappstuhl.me).
    scan_endpoint: String,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt().with_target(false).init();

    let config = Config::load()?;
    let addr = config.bind;
    let state = build_state(config).await?;
    let app = build_router(state);

    let listener = tokio::net::TcpListener::bind(addr).await?;
    tracing::info!("percy-dashboard listening on http://{addr}");

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await?;
    Ok(())
}

/// Returns (and creates) the path to the dashboard's own database,
/// `<data>/percy-dashboard/dashboard.db`. Overridable with `DASHBOARD_DB` (set it
/// to `:memory:` for an ephemeral database).
fn database_path() -> anyhow::Result<PathBuf> {
    use anyhow::Context;

    if let Ok(explicit) = std::env::var("DASHBOARD_DB") {
        return Ok(PathBuf::from(explicit));
    }
    let mut path = dirs::data_dir().context("could not find a data directory for the current user")?;
    path.push("percy-dashboard");
    std::fs::create_dir_all(&path).context("could not create the dashboard data directory")?;
    path.push("dashboard.db");
    Ok(path)
}

/// Builds application state against the dashboard's own on-disk database.
async fn build_state(config: Config) -> anyhow::Result<AppState> {
    build_state_with(config, &database_path()?).await
}

/// Opens `dashboard.db` at `path`, applying the embedded migrations on every
/// connection via the shared runner, and wires an optional Percy client from the
/// config. Split out so tests can drive a hermetic `:memory:` database.
async fn build_state_with(config: Config, path: &Path) -> anyhow::Result<AppState> {
    // Publish the public base URL for templates that need absolute asset URLs
    // (the link-preview `og:image`). `set` only takes the first value, so repeated
    // state builds in tests are harmless.
    let _ = crate::config::PUBLIC_BASE_URL.set(config.base_url.trim_end_matches('/').to_string());

    // A small pool suits the dashboard's light, read-mostly workload. The shared
    // kernel (v0.1.1+) pre-initialises WAL before spawning the pool, so opening
    // several connections on a brand-new file no longer races. `with_init`
    // applies the migration set on each connection before it is handed out.
    let db = Database::file(path)
        .connections(4)
        .with_init(migrations::migrate)
        .open()
        .await?;

    let percy = config.percy_api_url().map(|url| {
        tracing::info!("Percy API client configured: {}", url);

        Arc::new(percy_client::PercyClient::new(
            reqwest::Client::new(),
            url,
            config.percy_api_token(),
        ))
    });

    // Default scan endpoint: klappstuhl.me's external API (versioned /api/v1).
    // Overridable via env.
    let scan_endpoint = std::env::var("PERCY_SCAN_ENDPOINT")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "https://klappstuhl.me/api/v1/scan".to_string());

    Ok(AppState {
        config: Arc::new(config),
        db: Arc::new(db),
        percy,
        scan_endpoint,
    })
}

/// Builds the router. Split out so a test can assert it composes without route
/// conflicts (mirrors klappstuhl_me's `full_router_builds`).
fn build_router(state: AppState) -> Router {
    Router::new()
        .route("/", get(index))
        .route("/watchlist", get(watchlist::watchlist_index))
        .route("/watchlist/:slug", get(watchlist::watch_order))
        .route("/watchlist/:universe/:slug", get(watchlist::watch_title))
        .route("/people/:slug", get(watchlist::person_page))
        .route("/comics", get(comics::comic_browse))
        .route("/comics/series", get(comics::comic_series_index))
        .route("/comics/series/:slug", get(comics::comic_series_detail))
        .route("/comics/:brand/:slug", get(comics::comic_detail))
        // The overview across both catalogues. Public (D9): signed out it renders the
        // same page minus the `Following` section.
        .route("/releases", get(releases::releases_page))
        // The news stream, and the ticker's "All news" target. Public (D9): signed out it
        // is the same page, minus the followed stories leading the list.
        .route("/news", get(news::news_page))
        .route("/dashboard", get(guilds::dashboard))
        .route("/dashboard/guild/:guild_id", get(guilds::guild_detail))
        .route("/dashboard/guild/:guild_id/members", get(guilds::guild_members))
        .route("/dashboard/guild/:guild_id/stats", get(pages::guild_stats))
        .route("/dashboard/guild/:guild_id/commands", get(pages::guild_commands))
        .route("/dashboard/guild/:guild_id/leveling", get(pages::guild_leveling))
        .route("/dashboard/guild/:guild_id/economy", get(pages::guild_economy))
        .route("/dashboard/guild/:guild_id/music", get(pages::guild_music))
        .route(
            "/dashboard/guild/:guild_id/autoresponders",
            get(pages::guild_autoresponders).post(actions::guild_autoresponders_action),
        )
        .route(
            "/dashboard/guild/:guild_id/comics",
            get(pages::guild_comics).post(crud::guild_comic_create),
        )
        .route(
            "/dashboard/guild/:guild_id/comics/:brand",
            patch(crud::guild_comic_patch).delete(crud::guild_comic_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/comics/:brand/push",
            post(crud::guild_comics_push),
        )
        .route(
            "/dashboard/guild/:guild_id/temp-channels",
            get(pages::guild_temp_channels).post(crud::guild_temp_channel_create),
        )
        .route(
            "/dashboard/guild/:guild_id/temp-channels/:channel_id",
            patch(crud::guild_temp_channel_patch).delete(crud::guild_temp_channel_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/autoresponders/:trigger",
            patch(crud::guild_autoresponder_patch).delete(crud::guild_autoresponder_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/economy/items",
            post(crud::guild_economy_items_create),
        )
        .route(
            "/dashboard/guild/:guild_id/economy/items/:name",
            delete(crud::guild_economy_item_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/economy/settings",
            post(crud::guild_economy_settings_update),
        )
        .route(
            "/dashboard/guild/:guild_id/economy/lottery",
            post(crud::guild_economy_lottery_create).delete(crud::guild_economy_lottery_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/economy/balances/:user_id",
            patch(crud::guild_economy_balance_update),
        )
        .route(
            "/dashboard/guild/:guild_id/status-feed",
            post(crud::guild_status_feed_subscribe).delete(crud::guild_status_feed_unsubscribe),
        )
        .route(
            "/dashboard/guild/:guild_id/highlights/:user_id",
            delete(crud::guild_highlight_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/polls",
            get(pages::guild_polls).post(actions::guild_poll_create),
        )
        .route(
            "/dashboard/guild/:guild_id/polls/:poll_id",
            post(actions::guild_poll_edit),
        )
        .route(
            "/dashboard/guild/:guild_id/polls/:poll_id/end",
            post(actions::guild_poll_end),
        )
        .route("/dashboard/guild/:guild_id/gallery", get(pages::guild_gallery))
        .route(
            "/dashboard/guild/:guild_id/gallery/upload",
            post(actions::guild_gallery_upload),
        )
        .route(
            "/dashboard/guild/:guild_id/gallery/:image_id/delete",
            post(actions::guild_gallery_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/giveaways",
            get(pages::guild_giveaways).post(actions::guild_giveaway_create),
        )
        .route(
            "/dashboard/guild/:guild_id/giveaways/:giveaway_id/end",
            post(actions::guild_giveaway_end),
        )
        .route(
            "/dashboard/guild/:guild_id/giveaways/:giveaway_id",
            delete(actions::guild_giveaway_delete),
        )
        .route("/dashboard/guild/:guild_id/tags", get(pages::guild_tags))
        .route(
            "/dashboard/guild/:guild_id/tags/import",
            post(actions::guild_tags_import),
        )
        .route(
            "/dashboard/guild/:guild_id/tags/export.csv",
            get(actions::guild_tags_export),
        )
        .route(
            "/dashboard/guild/:guild_id/tags/:tag_id",
            get(actions::guild_tag_detail).delete(actions::guild_tag_delete),
        )
        .route("/dashboard/guild/:guild_id/highlights", get(pages::guild_highlights))
        .route("/dashboard/guild/:guild_id/emoji-stats", get(pages::guild_emoji_stats))
        // Analytics: the page + its two JSON feeds (series for the chart, summary
        // for the headline tiles) that the range/metric pickers re-fetch.
        .route("/dashboard/guild/:guild_id/analytics", get(pages::guild_analytics))
        .route(
            "/dashboard/guild/:guild_id/analytics/series",
            get(integrations::guild_analytics_series),
        )
        .route(
            "/dashboard/guild/:guild_id/analytics/summary",
            get(integrations::guild_analytics_summary),
        )
        // Outbound webhooks (event subscriptions): page + CRUD/test/deliveries.
        .route("/dashboard/guild/:guild_id/webhooks", get(pages::guild_webhooks))
        .route(
            "/dashboard/guild/:guild_id/webhooks/create",
            post(integrations::guild_webhook_create),
        )
        .route(
            "/dashboard/guild/:guild_id/webhooks/:sub_id",
            patch(integrations::guild_webhook_patch).delete(integrations::guild_webhook_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/webhooks/:sub_id/test",
            post(integrations::guild_webhook_test),
        )
        .route(
            "/dashboard/guild/:guild_id/webhooks/:sub_id/deliveries",
            get(integrations::guild_webhook_deliveries),
        )
        // Backup / restore + shareable templates.
        .route("/dashboard/guild/:guild_id/backup", get(pages::guild_backup))
        .route(
            "/dashboard/guild/:guild_id/backup/export.json",
            get(integrations::guild_backup_export),
        )
        .route(
            "/dashboard/guild/:guild_id/backup/import",
            post(integrations::guild_backup_import),
        )
        .route(
            "/dashboard/guild/:guild_id/templates",
            post(integrations::guild_template_publish),
        )
        .route(
            "/dashboard/guild/:guild_id/templates/:slug/apply",
            post(integrations::guild_template_apply),
        )
        .route(
            "/dashboard/guild/:guild_id/templates/:slug",
            delete(integrations::guild_template_delete),
        )
        // Members JSON feed + actions.
        .route(
            "/dashboard/guild/:guild_id/members.json",
            get(actions::guild_members_json),
        )
        .route(
            "/dashboard/guild/:guild_id/members/:user_id/action",
            post(actions::guild_member_action),
        )
        .route(
            "/dashboard/guild/:guild_id/members/:user_id/roles",
            patch(actions::guild_member_roles),
        )
        .route(
            "/dashboard/guild/:guild_id/members/:user_id/messages",
            get(actions::guild_member_messages),
        )
        // Leveling mutations.
        .route(
            "/dashboard/guild/:guild_id/leveling/users/:user_id",
            post(actions::guild_leveling_update),
        )
        .route(
            "/dashboard/guild/:guild_id/leveling/config",
            post(actions::guild_leveling_config_update),
        )
        .route(
            "/dashboard/guild/:guild_id/leveling/roles",
            post(actions::guild_leveling_roles),
        )
        .route(
            "/dashboard/guild/:guild_id/leveling/roles/preset",
            post(actions::guild_leveling_roles_preset),
        )
        .route(
            "/dashboard/guild/:guild_id/leveling/multipliers",
            post(actions::guild_leveling_multipliers),
        )
        .route(
            "/dashboard/guild/:guild_id/leveling/blacklist",
            post(actions::guild_leveling_blacklist),
        )
        // Music controls + live-player JSON.
        .route(
            "/dashboard/guild/:guild_id/music/status",
            get(actions::guild_music_status),
        )
        .route(
            "/dashboard/guild/:guild_id/music/control",
            post(actions::guild_music_control),
        )
        .route(
            "/dashboard/guild/:guild_id/music/lyrics",
            get(actions::guild_music_lyrics),
        )
        .route(
            "/dashboard/guild/:guild_id/music/setup",
            post(actions::guild_music_setup),
        )
        .route(
            "/dashboard/guild/:guild_id/music/reset",
            post(actions::guild_music_reset),
        )
        .route(
            "/dashboard/guild/:guild_id/music/equalizer",
            post(actions::guild_music_equalizer),
        )
        .route(
            "/dashboard/guild/:guild_id/music/filters",
            post(actions::guild_music_filters),
        )
        .route("/dashboard/guild/:guild_id/music/247", post(actions::guild_music_247))
        .route(
            "/dashboard/guild/:guild_id/music/dj-mode",
            patch(actions::guild_music_dj_mode),
        )
        // Moderation audit log + cases, bulk actions, activity/avatars, exports.
        .route("/dashboard/guild/:guild_id/audit-log", get(moderation::guild_audit_log))
        .route(
            "/dashboard/guild/:guild_id/audit-log.json",
            get(moderation::guild_audit_log_json),
        )
        .route(
            "/dashboard/guild/:guild_id/audit-log/recent",
            get(moderation::guild_cases_recent),
        )
        .route("/dashboard/guild/:guild_id/cases", post(moderation::guild_case_create))
        .route(
            "/dashboard/guild/:guild_id/cases/:case_index",
            patch(moderation::guild_case_update).delete(moderation::guild_case_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/members/bulk-action",
            post(moderation::guild_bulk_action),
        )
        .route(
            "/dashboard/guild/:guild_id/members/:user_id/activity",
            get(moderation::guild_member_activity),
        )
        .route(
            "/dashboard/guild/:guild_id/members/:user_id/avatars",
            get(moderation::guild_member_avatars),
        )
        .route(
            "/dashboard/guild/:guild_id/export/leaderboard.csv",
            get(moderation::guild_export_leaderboard),
        )
        .route(
            "/dashboard/guild/:guild_id/export/cases.csv",
            get(moderation::guild_export_cases),
        )
        .route(
            "/dashboard/guild/:guild_id/custom-bot",
            patch(moderation::guild_custom_bot_update),
        )
        .route(
            "/dashboard/guild/:guild_id/custom-bot/reset",
            post(moderation::guild_custom_bot_reset),
        )
        // The signed-in member's own self / history / data pages.
        .route("/dashboard/guild/:guild_id/me", get(userpages::guild_user_self))
        .route(
            "/dashboard/guild/:guild_id/me/settings",
            post(userpages::guild_user_settings_update),
        )
        .route(
            "/dashboard/guild/:guild_id/me/history",
            get(userpages::guild_user_history),
        )
        .route(
            "/dashboard/guild/:guild_id/me/data-export",
            get(userpages::guild_user_data_export),
        )
        .route(
            "/dashboard/guild/:guild_id/me/delete-data",
            post(userpages::guild_user_data_delete),
        )
        // The read-only public overview + its shared live-player endpoints.
        .route("/dashboard/guild/:guild_id/overview", get(userpages::guild_overview))
        .route(
            "/dashboard/guild/:guild_id/overview/music",
            get(userpages::guild_overview_music_status),
        )
        .route(
            "/dashboard/guild/:guild_id/overview/music/control",
            post(userpages::guild_overview_music_control),
        )
        .route(
            "/dashboard/guild/:guild_id/overview/music/lyrics",
            get(userpages::guild_overview_music_lyrics),
        )
        // A moderator's read of a single member (bare :user_id under /members).
        .route(
            "/dashboard/guild/:guild_id/members/:user_id",
            get(userpages::guild_user_lookup),
        )
        // Config + AI + sentinel mutations (form/JSON POST).
        .route(
            "/dashboard/guild/:guild_id/config",
            post(mutations::guild_config_update),
        )
        .route("/dashboard/guild/:guild_id/ai", post(mutations::guild_ai_flags_update))
        .route("/dashboard/guild/:guild_id/ai/ask", post(mutations::guild_ai_ask))
        .route(
            "/dashboard/guild/:guild_id/ai/override",
            post(mutations::guild_ai_override_update),
        )
        .route(
            "/dashboard/guild/:guild_id/ai/override/:channel_id/delete",
            post(mutations::guild_ai_override_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/sentinel",
            post(mutations::guild_sentinel_update),
        )
        // Commands / moderation / lockdown mutations (JSON POST/PATCH).
        .route(
            "/dashboard/guild/:guild_id/commands/toggle",
            post(mutations::guild_command_toggle),
        )
        .route(
            "/dashboard/guild/:guild_id/commands/permissions",
            post(mutations::guild_command_permissions),
        )
        .route("/dashboard/guild/:guild_id/plonks", post(mutations::guild_plonk_manage))
        .route(
            "/dashboard/guild/:guild_id/lockdowns/lock",
            post(mutations::guild_lockdown_lock),
        )
        .route(
            "/dashboard/guild/:guild_id/lockdowns/unlock",
            post(mutations::guild_lockdown_unlock),
        )
        .route(
            "/dashboard/guild/:guild_id/moderation/ignore",
            post(mutations::guild_moderation_ignore),
        )
        .route(
            "/dashboard/guild/:guild_id/audit-log-flags",
            patch(mutations::guild_audit_log_flags),
        )
        // Public leaderboard (no login) + vanity slug management.
        .route("/lb/:target", get(leaderboard::public_leaderboard))
        .route(
            "/lb/:guild_id/vanity",
            post(leaderboard::public_leaderboard_vanity_claim).delete(leaderboard::public_leaderboard_vanity_delete),
        )
        // Public marketing / site pages.
        .route("/commands", get(site::percy_commands))
        .route("/changelog", get(site::percy_changelog))
        .route("/support", get(site::percy_support_server))
        .route("/privacy-policy", get(site::percy_privacy))
        .route("/terms-of-service", get(site::percy_terms))
        .route("/dashboard/bot-version", get(site::percy_bot_version))
        .route("/login", get(auth::login))
        .route("/logout", get(auth::logout))
        .route("/auth/discord/callback", get(auth::callback))
        .route("/auth/handoff", get(auth::handoff))
        .route("/health", get(health))
        .route("/account", get(userpages::account_page))
        .route("/account/settings", post(userpages::account_settings_update))
        .route("/account/data-export", get(userpages::account_data_export))
        .route("/account/delete-data", post(userpages::account_data_delete))
        .route("/account/discord/avatar", get(auth::discord_avatar))
        // Release subscriptions: the page, its picker feed, and the three one-off
        // mutations (subscribe / retune / unfollow), each a plain form post.
        .route(
            "/account/subscriptions",
            get(subscriptions::subscriptions_page).post(subscriptions::subscription_create),
        )
        .route(
            "/account/subscriptions/update",
            post(subscriptions::subscription_update),
        )
        .route(
            "/account/subscriptions/remove",
            post(subscriptions::subscription_delete),
        )
        .route("/account/subscriptions/search", get(subscriptions::subscription_search))
        // Read/watched marks (R8). Two plain form posts plus the JSON read `progress.js`
        // reconciles against — the same endpoints whether or not JS is running.
        .route(
            "/account/progress",
            get(progress::progress_list).post(progress::progress_mark),
        )
        .route("/account/progress/remove", post(progress::progress_unmark))
        .route("/api-info", get(api_info))
        .nest("/kls", kls_ui::routes())
        // The dashboard's own app-specific assets (percy CSS/JS/img), shipped as a
        // `static/` dir next to the binary. The shared design system lives at /kls.
        .nest_service("/static", tower_http::services::ServeDir::new("static"))
        .with_state(state)
}

/// The public landing page (ported verbatim from the monolith), shown to
/// logged-out visitors.
#[derive(Template)]
#[template(path = "landing.html")]
struct LandingTemplate {
    account: Option<view::Viewer>,
}

async fn index(session: Option<session::Session>) -> axum::response::Response {
    use axum::response::IntoResponse;
    // Always render the landing/home page — even for signed-in visitors — so there
    // is a viewable home distinct from /dashboard (reachable via the nav "Home"
    // link and by visiting "/" directly). The header reflects auth state: signed-in
    // users see their account menu, everyone else the login link. `Option<Session>`
    // yields `None` when the cookie is absent or invalid.
    let account = session.as_ref().map(view::Viewer::from_session);
    LandingTemplate { account }.into_response()
}

/// Liveness probe that also confirms the kernel database is serving queries.
async fn health(State(state): State<AppState>) -> Json<serde_json::Value> {
    // `execute_batch` steps the statement and ignores any returned rows, so a
    // row-yielding probe like `SELECT 1` is fine (plain `execute` would error).
    let db_ok = state.db.execute_batch("SELECT 1;").await.is_ok();
    Json(serde_json::json!({ "status": "ok", "database": db_ok }))
}

/// Reports whether the Percy API client is configured — the dashboard's only
/// hard runtime dependency, and optional at boot.
async fn api_info(State(state): State<AppState>) -> Json<serde_json::Value> {
    Json(serde_json::json!({ "percy_configured": state.percy.is_some() }))
}

/// Waits for Ctrl-C so `axum::serve` can drain in-flight requests on shutdown.
async fn shutdown_signal() {
    let _ = tokio::signal::ctrl_c().await;
    tracing::info!("shutdown signal received");
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A hermetic in-memory state for tests — never touches the real
    /// `dashboard.db` on disk or the operator's `config.json`.
    async fn test_state() -> AppState {
        build_state_with(Config::test_default(), Path::new(":memory:"))
            .await
            .expect("build state")
    }

    /// The router must compose without panicking (route/state conflicts).
    #[tokio::test]
    async fn router_builds() {
        let _ = build_router(test_state().await);
    }

    /// The database is live and its migrated schema is present through shared state.
    #[tokio::test]
    async fn health_state_is_live() {
        let state = test_state().await;
        assert!(state.db.execute_batch("SELECT 1;").await.is_ok());
        // The migration runner applied the dashboard-owned schema.
        assert!(
            state
                .db
                .execute_batch("SELECT slug FROM percy_leaderboard_vanity LIMIT 0;")
                .await
                .is_ok(),
            "migrated schema not present"
        );
        assert!(state.percy.is_none(), "no Percy client without a configured URL");
    }

    /// The verbatim landing page compiles and renders for a logged-out visitor.
    #[test]
    fn landing_template_renders() {
        let html = LandingTemplate { account: None }.render().expect("render");
        assert!(html.contains("Percy"));
        // Logged-out hero prompts a login.
        assert!(html.contains("Log in to continue"));
    }

    /// The landing page also renders for a signed-in visitor, swapping the hero
    /// CTA for a dashboard link (so "/" is a viewable home even when logged in).
    #[test]
    fn landing_template_renders_when_signed_in() {
        let viewer = view::Viewer::from_session(&session::Session::new("1", "ferris"));
        let html = LandingTemplate { account: Some(viewer) }.render().expect("render");
        assert!(html.contains("Go to your dashboard"));
        assert!(!html.contains("Log in to continue"));
    }
}
