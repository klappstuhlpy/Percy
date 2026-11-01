//! The shared klappstuhl.me design assets (base.css / base.js / timestamps.js), embedded at
//! compile time and served at `/kls/*` — formerly the `kls-ui` crate, now owned in-tree.

use axum::{
    http::header::{CACHE_CONTROL, CONTENT_TYPE},
    response::IntoResponse,
    routing::get,
    Router,
};

const BASE_CSS: &str = include_str!("../static/kls/base.css");
const BASE_JS: &str = include_str!("../static/kls/base.js");
const TIMESTAMPS_JS: &str = include_str!("../static/kls/timestamps.js");

/// `no-cache` so a design change is never masked by a stale copy.
fn asset(content_type: &'static str, body: &'static str) -> impl IntoResponse {
    ([(CONTENT_TYPE, content_type), (CACHE_CONTROL, "no-cache")], body)
}

pub fn routes<S>() -> Router<S>
where
    S: Clone + Send + Sync + 'static,
{
    Router::new()
        .route(
            "/base.css",
            get(|| async { asset("text/css; charset=utf-8", BASE_CSS) }),
        )
        .route(
            "/base.js",
            get(|| async { asset("text/javascript; charset=utf-8", BASE_JS) }),
        )
        .route(
            "/timestamps.js",
            get(|| async { asset("text/javascript; charset=utf-8", TIMESTAMPS_JS) }),
        )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn assets_are_embedded() {
        assert!(BASE_CSS.contains("JetBrains"), "base.css design tokens missing");
        assert!(!BASE_JS.trim().is_empty() && !TIMESTAMPS_JS.trim().is_empty());
    }

    #[test]
    fn routes_build() {
        let _ = routes::<()>();
    }
}
