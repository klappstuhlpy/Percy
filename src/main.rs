//! Percy dashboard — standalone web binary.
//!
//! A configuration dashboard for [Percy](https://percy.klappstuhl.me) that runs
//! **independently out of the box**, linking only the shared crates —
//! [`kls_web_core`] (SQLite + crypto + migrations kernel), [`kls_ui`] (the design
//! system, served at `/kls/*`), and [`percy_client`] (Percy's internal API) — with
//! no dependency on the `klappstuhl_me` app crate.
//!
//! Structure (ported incrementally from the klappstuhl_me monolith):
//! - [`config`] — `config.json` (Discord OAuth, Percy API, signing key),
//! - [`db`] — typed access to the dashboard-owned tables in `dashboard.db`,
//! - [`migrations`] — the embedded schema, applied via the shared runner,
//! - the routes below render through the shared design system via Askama.

use std::{
    path::{Path, PathBuf},
    sync::Arc,
};

use askama::Template;
use axum::{extract::State, routing::get, Json, Router};
use kls_web_core::Database;

use crate::config::Config;

mod auth;
mod config;
mod db;
mod migrations;
mod session;

/// Shared application state, threaded through every handler.
#[derive(Clone)]
struct AppState {
    config: Arc<Config>,
    db: Arc<Database>,
    /// The Percy API client, present only when Percy is configured.
    percy: Option<Arc<percy_client::PercyClient>>,
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
        Arc::new(percy_client::PercyClient::new(
            reqwest::Client::new(),
            url,
            config.percy_api_token(),
        ))
    });

    Ok(AppState {
        config: Arc::new(config),
        db: Arc::new(db),
        percy,
    })
}

/// Builds the router. Split out so a test can assert it composes without route
/// conflicts (mirrors klappstuhl_me's `full_router_builds`).
fn build_router(state: AppState) -> Router {
    Router::new()
        .route("/", get(index))
        .route("/login", get(auth::login))
        .route("/logout", get(auth::logout))
        .route("/auth/discord/callback", get(auth::callback))
        .route("/health", get(health))
        .route("/api-info", get(api_info))
        .nest("/kls", kls_ui::routes())
        .with_state(state)
}

/// The dashboard landing page, rendered through the shared design system.
#[derive(Template)]
#[template(path = "index.html")]
struct IndexTemplate {
    discord_login_enabled: bool,
    percy_configured: bool,
}

async fn index(State(state): State<AppState>) -> IndexTemplate {
    IndexTemplate {
        discord_login_enabled: state.config.discord_login_enabled(),
        percy_configured: state.percy.is_some(),
    }
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

    /// The landing page renders (template compiles + fills) in both states.
    #[test]
    fn index_template_renders() {
        let enabled = IndexTemplate {
            discord_login_enabled: true,
            percy_configured: true,
        }
        .render()
        .expect("render");
        assert!(enabled.contains("Log in with Discord"));

        let disabled = IndexTemplate {
            discord_login_enabled: false,
            percy_configured: false,
        }
        .render()
        .expect("render");
        assert!(disabled.contains("isn't configured"));
    }
}
