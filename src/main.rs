//! Percy dashboard — standalone binary (skeleton).
//!
//! This is the first tangible proof of the decoupling goal (see
//! DASHBOARD_DECOUPLING_PLAN.md): a web server that boots **independently out of
//! the box**, linking only the shared crates —
//!
//! * [`kls_web_core`] — the SQLite + crypto + migrations kernel,
//! * [`kls_ui`] — the shared design system (served at `/kls/*`),
//! * [`percy_client`] — the typed client for Percy's internal API,
//!
//! and **not** the `klappstuhl_me` app crate. Its only hard runtime dependency is
//! Percy's internal API, and even that is optional at boot: with no
//! `PERCY_API_URL` configured the dashboard still serves.
//!
//! It deliberately does almost nothing yet — a landing page on the shared
//! stylesheet, a `/health` probe backed by a live in-memory kernel database, and
//! a `/api-info` route that reports whether the Percy client is wired. Real
//! dashboard routes arrive when this graduates into its own repo (Phase 5+).

use std::{
    net::SocketAddr,
    path::{Path, PathBuf},
    sync::Arc,
};

use axum::{extract::State, response::Html, routing::get, Json, Router};
use kls_web_core::Database;

mod db;
mod migrations;

/// Shared application state. Kept intentionally tiny; it grows its own config,
/// session store, and Percy wiring as the dashboard fills in — the point here is
/// only that the shared kernel types compose into a normal axum state.
#[derive(Clone)]
struct AppState {
    db: Arc<Database>,
    /// The Percy API client, present only when `PERCY_API_URL` is configured.
    percy: Option<Arc<percy_client::PercyClient>>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt().with_target(false).init();

    let state = build_state().await?;
    let app = build_router(state);

    let port = std::env::var("DASHBOARD_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(8091);
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let listener = tokio::net::TcpListener::bind(addr).await?;
    tracing::info!("dashboard skeleton listening on http://{addr}");

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
async fn build_state() -> anyhow::Result<AppState> {
    build_state_with(&database_path()?).await
}

/// Opens `dashboard.db` at `path`, applying the embedded migrations on every
/// connection via the shared runner, and wires an optional Percy client from the
/// environment. Split out so tests can drive a hermetic `:memory:` database.
async fn build_state_with(path: &Path) -> anyhow::Result<AppState> {
    // A small pool suits the dashboard's light, read-mostly workload. The shared
    // kernel (v0.1.1+) pre-initialises WAL before spawning the pool, so opening
    // several connections on a brand-new file no longer races. `with_init`
    // applies the migration set on each connection before it is handed out.
    let db = Database::file(path)
        .connections(4)
        .with_init(migrations::migrate)
        .open()
        .await?;

    let percy = match std::env::var("PERCY_API_URL").ok().filter(|s| !s.is_empty()) {
        Some(url) => {
            let token = std::env::var("PERCY_API_TOKEN").unwrap_or_default();
            Some(Arc::new(percy_client::PercyClient::new(
                reqwest::Client::new(),
                url,
                token,
            )))
        }
        None => None,
    };

    Ok(AppState {
        db: Arc::new(db),
        percy,
    })
}

/// Builds the router. Split out so a test can assert it composes without route
/// conflicts (mirrors klappstuhl_me's `full_router_builds`).
fn build_router(state: AppState) -> Router {
    Router::new()
        .route("/", get(index))
        .route("/health", get(health))
        .route("/api-info", get(api_info))
        .nest("/kls", kls_ui::routes())
        .with_state(state)
}

/// Landing page, styled with the shared design system to prove `kls-ui` loads.
async fn index() -> Html<&'static str> {
    Html(concat!(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>Percy Dashboard</title>",
        "<link rel=\"stylesheet\" href=\"/kls/base.css\">",
        "</head><body><main style=\"max-width:40rem;margin:4rem auto;padding:0 1.5rem\">",
        "<h1>Percy Dashboard</h1>",
        "<p>Standalone skeleton. Linked against the shared klappstuhl.me kernel ",
        "(<code>kls-web-core</code>, <code>kls-ui</code>, <code>percy-client</code>) ",
        "with no dependency on the main site binary.</p>",
        "<p><a href=\"/health\">/health</a> &middot; <a href=\"/api-info\">/api-info</a></p>",
        "</main><script src=\"/kls/base.js\"></script></body></html>",
    ))
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
    /// `dashboard.db` on disk.
    async fn test_state() -> AppState {
        build_state_with(Path::new(":memory:")).await.expect("build state")
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
        assert!(state.percy.is_none(), "no Percy client without PERCY_API_URL");
    }
}
