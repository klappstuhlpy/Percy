//! Percy dashboard — standalone web binary.
//!
//! A configuration dashboard for [Percy](https://percy.klappstuhl.me) that runs
//! **independently out of the box**, linking only the shared crates —
//! [`kls_web_core`] (SQLite + crypto + migrations kernel), the in-tree `kls` module (the design
//! system, served at `/kls/*`), and [`percy_client`] (Percy's internal API) — with
//! no dependency on the `klappstuhl_me` app crate.
//!
//! Structure (ported incrementally from the klappstuhl_me monolith):
//! - [`config`] — `config.json` (Discord OAuth, Percy API, signing key, scan token),
//! - [`db`] — typed access to the dashboard-owned tables in `dashboard.db`,
//! - [`migrations`] — the embedded schema, applied via the shared runner,
//! - the routes below render through the shared design system via Askama.

// Handlers return `Result<_, Response>` to short-circuit with a redirect or error page —
// idiomatic axum; boxing every `Response` would only add noise.
#![allow(clippy::result_large_err)]

use std::{
    path::{Path, PathBuf},
    sync::Arc,
};

use kls_web_core::Database;

use crate::config::Config;

mod account;
mod api;
mod catalogue;
mod core;
mod guild;
mod kls;
mod routes;
mod site;
mod templates;

// Flat aliases, so every `crate::pages::…` / `crate::auth::…` path keeps working
// wherever a module actually lives on disk.
pub(crate) use account::{progress, subscriptions, userpages};
pub(crate) use catalogue::{comics, news, releases, watchlist};
pub(crate) use core::{auth, config, db, errors, migrations, scan, session, view};
pub(crate) use guild::{
    actions, changes, community, crud, forms, guilds, integrations, leaderboard, moderation, music_app, mutations,
    notifications, pages,
};

/// Shared application state, threaded through every handler.
#[derive(Clone)]
struct AppState {
    config: Arc<Config>,
    db: Arc<Database>,
    /// The Percy API client, present only when Percy is configured.
    percy: Option<Arc<percy_client::PercyClient>>,
    /// Endpoint for the external malware scan service (klappstuhl.me).
    scan_endpoint: String,
    /// In-memory rate-limit buckets for the public API (`/api/v1`).
    limiter: Arc<api::limit::Limiter>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt().with_target(false).init();

    let config = Config::load()?;
    let addr = config.bind;
    let state = build_state(config).await?;
    let app = routes::build_router(state);

    let listener = tokio::net::TcpListener::bind(addr).await?;
    tracing::info!("percy-dashboard listening on http://{addr}");

    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<std::net::SocketAddr>(),
    )
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

        // A bounded timeout: a Percy that accepts the connection but never answers must
        // fail the request (and any test against a fake), never hang it.
        let http = reqwest::Client::builder()
            .connect_timeout(std::time::Duration::from_secs(5))
            .timeout(std::time::Duration::from_secs(30))
            .build()
            .expect("reqwest client builds");
        Arc::new(percy_client::PercyClient::new(http, url, config.percy_api_token()))
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
        limiter: Arc::default(),
    })
}

/// Waits for Ctrl-C so `axum::serve` can drain in-flight requests on shutdown.
async fn shutdown_signal() {
    let _ = tokio::signal::ctrl_c().await;
    tracing::info!("shutdown signal received");
}
