//! The public developer API: `percy.klappstuhl.me/api/v1/*`.
//!
//! The dashboard is the gateway; Percy's internal FastAPI stays private on localhost (see
//! `PUBLIC_SITE_AND_API.md`, section 3). This module group holds the token store, the auth /
//! scope / rate-limit middleware, the handlers, the Developers tab and the `/api` docs page.

use axum::{
    middleware::from_fn_with_state,
    response::{Html, IntoResponse},
    routing::get,
    Json, Router,
};

use crate::AppState;

mod developers;
mod gate;
mod handlers;
pub(crate) mod limit;
pub(crate) mod tokens;

pub(crate) use developers::{developers_page, token_create, token_revoke};

/// The hand-written OpenAPI 3.1 spec, embedded so the binary needs no file beside it.
const SPEC_YAML: &str = include_str!("../../static/api/openapi.yaml");

/// Scalar renders the spec; the version is pinned to an exact release.
const DOCS_HTML: &str = r#"<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Percy API reference</title>
<meta name="description" content="Reference for the Percy public API: economy, items, inventory and leveling over HTTPS.">
</head>
<body>
<script id="api-reference" data-url="/api/openapi.json"></script>
<script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.25.130" integrity="sha384-oyL8y2b0EvVxYsvg2qNlT8xHcsvmySaljIklWvbATuTrccZSyvWnsPnmOunYQL2R" crossorigin="anonymous"></script>
</body>
</html>
"#;

fn spec() -> serde_json::Value {
    serde_yaml::from_str(SPEC_YAML).expect("static/api/openapi.yaml is valid YAML")
}

async fn openapi_json() -> impl IntoResponse {
    ([("cache-control", "public, max-age=300")], Json(spec()))
}

async fn docs_page() -> Html<&'static str> {
    Html(DOCS_HTML)
}

/// Every `/api*` route. Merged into the main router before its state is applied.
pub(crate) fn routes(state: AppState) -> Router<AppState> {
    let guild = Router::new()
        .route("/api/v1/guilds/:guild_id", get(handlers::guild))
        .route("/api/v1/guilds/:guild_id/users", get(handlers::users))
        .route(
            "/api/v1/guilds/:guild_id/users/:user_id",
            get(handlers::user).patch(handlers::adjust_user).put(handlers::set_user),
        )
        .route(
            "/api/v1/guilds/:guild_id/users/:user_id/inventory",
            get(handlers::inventory).post(handlers::give_item),
        )
        .route(
            "/api/v1/guilds/:guild_id/users/:user_id/inventory/:item_id",
            axum::routing::delete(handlers::take_item),
        )
        .route(
            "/api/v1/guilds/:guild_id/items",
            get(handlers::items).post(handlers::create_item),
        )
        .route(
            "/api/v1/guilds/:guild_id/items/:item_id",
            get(handlers::item)
                .patch(handlers::patch_item)
                .delete(handlers::delete_item),
        )
        .route(
            "/api/v1/guilds/:guild_id/leveling/leaderboard",
            get(handlers::leaderboard),
        )
        .route(
            "/api/v1/guilds/:guild_id/leveling/users/:user_id",
            get(handlers::level_user),
        )
        .route_layer(from_fn_with_state(state.clone(), gate::guild_gate));

    let public = Router::new()
        .route("/api/v1/bot/stats", get(handlers::bot_stats))
        .route_layer(from_fn_with_state(state, gate::public_gate));

    Router::new()
        .route("/api", get(docs_page))
        .route("/api/openapi.json", get(openapi_json))
        .merge(guild)
        .merge(public)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{build_state_with, config::Config, routes::build_router};
    use std::path::Path;

    const METHODS: [&str; 5] = ["get", "put", "post", "patch", "delete"];

    /// `/guilds/{guild_id}/users/{user_id}` -> `/api/v1/guilds/1/users/2`
    fn concrete(path: &str) -> String {
        let mut n = 0;
        let mut out = String::from("/api/v1");
        for seg in path.split('/').filter(|s| !s.is_empty()) {
            out.push('/');
            if seg.starts_with('{') {
                n += 1;
                out.push_str(&n.to_string());
            } else {
                out.push_str(seg);
            }
        }
        out
    }

    #[test]
    fn spec_is_openapi_31_with_documented_security() {
        let spec = spec();
        assert!(spec["openapi"].as_str().unwrap().starts_with("3.1"));
        assert!(spec["components"]["securitySchemes"]["bearerAuth"].is_object());
        assert!(!spec["paths"].as_object().unwrap().is_empty());
    }

    #[test]
    fn spec_scopes_match_the_enforced_scope_table() {
        let spec = spec();
        for (path, item) in spec["paths"].as_object().unwrap() {
            for m in METHODS {
                let Some(op) = item.get(m) else { continue };
                let method = axum::http::Method::from_bytes(m.to_uppercase().as_bytes()).unwrap();
                let declared = op["x-required-scope"]
                    .as_str()
                    .unwrap_or_else(|| panic!("{m} {path}: no x-required-scope"));
                if let Some(rest) = path.strip_prefix("/guilds/{guild_id}") {
                    let rest = rest.replace(['{', '}'], "");
                    let enforced = match gate::required_scope(&method, &rest) {
                        Some(gate::Required::Any) => "any",
                        Some(gate::Required::Scope(s)) => s,
                        None => panic!("{m} {path} documented but denied by the scope table"),
                    };
                    assert_eq!(declared, enforced, "{m} {path}");
                    if enforced != "any" {
                        assert!(tokens::is_known_scope(enforced));
                    }
                } else {
                    assert_eq!(declared, "none", "{m} {path} is public");
                }
            }
        }
    }

    /// Every path+method in the spec is a registered route: it must never answer 404 (no such path)
    /// or 405 (path exists, method does not). Unauthenticated, the gate answers 401/429/503 instead.
    #[tokio::test]
    async fn every_spec_operation_is_a_registered_route() {
        let state = build_state_with(Config::test_default(), Path::new(":memory:"))
            .await
            .expect("state");
        let app = build_router(state);
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move { axum::serve(listener, app).await });

        let client = reqwest::Client::new();
        let spec = spec();
        let mut checked = 0;
        for (path, item) in spec["paths"].as_object().unwrap() {
            for m in METHODS {
                if item.get(m).is_none() {
                    continue;
                }
                let url = format!("http://{addr}{}", concrete(path));
                let resp = client
                    .request(reqwest::Method::from_bytes(m.to_uppercase().as_bytes()).unwrap(), &url)
                    .send()
                    .await
                    .unwrap();
                let status = resp.status().as_u16();
                assert!(
                    status != 404 && status != 405,
                    "{m} {path} -> {status}: not a registered route"
                );
                checked += 1;
            }
        }
        assert!(checked >= 15, "spec lists suspiciously few operations ({checked})");

        // And the reverse for the docs routes themselves.
        for p in ["/api", "/api/openapi.json"] {
            let r = client.get(format!("http://{addr}{p}")).send().await.unwrap();
            assert_eq!(r.status().as_u16(), 200, "{p}");
        }
    }

    /// End to end through the real middleware: auth, guild binding, scope, error envelope.
    #[tokio::test]
    async fn gateway_enforces_auth_binding_and_scopes() {
        // A real file: the pool has several connections and `:memory:` would give each its own database.
        let db_path = std::env::temp_dir().join(format!("percy-api-test-{}.db", std::process::id()));
        let state = build_state_with(Config::test_default(), &db_path).await.expect("state");
        let read = tokens::create(&state.db, "42", "t", &["economy:read".to_string()], "1")
            .await
            .ok()
            .expect("token");
        let db = state.db.clone();
        let app = build_router(state);
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move { axum::serve(listener, app).await });
        let client = reqwest::Client::new();
        let get = |path: &str, token: Option<&str>| {
            let mut r = client.get(format!("http://{addr}{path}"));
            if let Some(t) = token {
                r = r.bearer_auth(t);
            }
            r.send()
        };

        let r = get("/api/v1/guilds/42/users/9", None).await.unwrap();
        assert_eq!(r.status(), 401);
        let body: serde_json::Value = r.json().await.unwrap();
        assert_eq!(body["error"]["code"], "missing_token");

        let r = get("/api/v1/guilds/42/users/9", Some("pct_wrong")).await.unwrap();
        assert_eq!(r.status(), 401);

        // Wrong guild for this token.
        let r = get("/api/v1/guilds/43/users/9", Some(&read.plaintext)).await.unwrap();
        assert_eq!(r.status(), 403);
        assert_eq!(
            r.json::<serde_json::Value>().await.unwrap()["error"]["code"],
            "guild_mismatch"
        );

        // Right guild, missing scope.
        let r = get("/api/v1/guilds/42/items", Some(&read.plaintext)).await.unwrap();
        assert_eq!(r.status(), 403);
        assert_eq!(
            r.json::<serde_json::Value>().await.unwrap()["error"]["code"],
            "missing_scope"
        );

        // A read token cannot write.
        let r = client
            .patch(format!("http://{addr}/api/v1/guilds/42/users/9"))
            .bearer_auth(&read.plaintext)
            .json(&serde_json::json!({"cash": 5}))
            .send()
            .await
            .unwrap();
        assert_eq!(r.status(), 403);

        // Allowed scope passes the gate (Percy is not configured in tests -> 503, not 4xx auth).
        let r = get("/api/v1/guilds/42/users/9", Some(&read.plaintext)).await.unwrap();
        assert_eq!(r.status(), 503);
        assert!(r.headers().contains_key("x-ratelimit-remaining"));

        // Revoked tokens stop working on the very next request.
        assert!(tokens::revoke(&db, "42", read.id).await.unwrap());
        let r = get("/api/v1/guilds/42/users/9", Some(&read.plaintext)).await.unwrap();
        assert_eq!(r.status(), 401);
        let _ = std::fs::remove_file(&db_path);
    }
}
