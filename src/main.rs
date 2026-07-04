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

use std::{net::SocketAddr, sync::Arc};

use axum::{extract::State, response::Html, routing::get, Json, Router};
use kls_web_core::Database;

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

/// Opens the kernel database and wires an optional Percy client from the
/// environment. The dashboard will own its own `dashboard.db` (Phase 6); the
/// skeleton uses an in-memory database purely to prove the kernel is usable.
async fn build_state() -> anyhow::Result<AppState> {
    // A single connection is plenty for the skeleton's in-memory health DB, and
    // keeps the worker-thread footprint minimal (the default pool opens 10). The
    // real dashboard will size its own pool when it owns a persistent file DB.
    let db = Database::file(":memory:").connections(1).open().await?;
    // Prove the kernel round-trips: a trivial schema + query.
    db.execute_batch("CREATE TABLE health(checked_at TEXT NOT NULL DEFAULT (datetime('now')));")
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

    /// The router must compose without panicking (route/state conflicts).
    #[tokio::test]
    async fn router_builds() {
        let state = build_state().await.expect("build state");
        let _ = build_router(state);
    }

    /// The kernel database is live and answering queries through shared state.
    #[tokio::test]
    async fn health_state_is_live() {
        let state = build_state().await.expect("build state");
        assert!(state.db.execute_batch("SELECT 1;").await.is_ok());
        assert!(state.percy.is_none(), "no Percy client without PERCY_API_URL");
    }
}
