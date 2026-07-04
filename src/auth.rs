//! Discord OAuth2 login for the dashboard.
//!
//! The dashboard authenticates users with their **own** Discord OAuth flow —
//! independent of klappstuhl.me's account system (per the decoupling plan). This
//! module currently ports the entry point: `/login` builds the Discord authorize
//! URL from config and redirects. The callback (code → token exchange → session
//! cookie) and the session extractor are the next porting slice.

use axum::{
    extract::State,
    http::StatusCode,
    response::{IntoResponse, Redirect, Response},
};

use crate::{config::Config, AppState};

/// Discord's OAuth2 authorization endpoint.
const DISCORD_AUTHORIZE: &str = "https://discord.com/oauth2/authorize";

/// Scopes the dashboard requests: the user's identity and their guild list
/// (to show which servers they can configure / add Percy to).
const OAUTH_SCOPES: &str = "identify guilds";

/// Builds the Discord OAuth2 authorize URL to redirect the user to. Percent
/// encoding of the redirect URI and scopes is handled by the URL builder.
pub fn authorize_url(config: &Config) -> anyhow::Result<reqwest::Url> {
    let redirect = config.discord_redirect_uri();
    let url = reqwest::Url::parse_with_params(
        DISCORD_AUTHORIZE,
        &[
            ("client_id", config.discord.client_id.as_str()),
            ("redirect_uri", redirect.as_str()),
            ("response_type", "code"),
            ("scope", OAUTH_SCOPES),
        ],
    )?;
    Ok(url)
}

/// `GET /login` — start the Discord OAuth flow (or explain it's not configured).
pub async fn login(State(state): State<AppState>) -> Response {
    if !state.config.discord_login_enabled() {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            "Discord login is not configured on this dashboard.",
        )
            .into_response();
    }
    match authorize_url(&state.config) {
        Ok(url) => Redirect::to(url.as_str()).into_response(),
        Err(e) => {
            tracing::error!(error = %e, "failed to build the Discord authorize URL");
            (StatusCode::INTERNAL_SERVER_ERROR, "could not start login").into_response()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn authorize_url_has_the_expected_params() {
        let mut config = Config::test_default();
        config.discord.client_id = "123456".to_string();
        config.discord.client_secret = "shh".to_string();
        config.base_url = "https://dash.example".to_string();

        let url = authorize_url(&config).expect("build url");
        let s = url.as_str();

        assert!(s.starts_with("https://discord.com/oauth2/authorize?"), "endpoint: {s}");
        assert!(s.contains("client_id=123456"), "client_id: {s}");
        assert!(s.contains("response_type=code"), "response_type: {s}");
        // Scopes and the redirect URI are percent-encoded.
        assert!(
            s.contains("scope=identify+guilds") || s.contains("scope=identify%20guilds"),
            "scope: {s}"
        );
        assert!(
            s.contains("redirect_uri=https%3A%2F%2Fdash.example%2Fauth%2Fdiscord%2Fcallback"),
            "redirect_uri: {s}"
        );
    }
}
