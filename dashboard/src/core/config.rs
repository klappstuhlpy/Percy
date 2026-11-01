//! Dashboard configuration, loaded from `config.json`.
//!
//! A deliberately small config — the dashboard needs far less than the main site.
//! On first run (no file) a default is written with a freshly generated signing
//! key so the operator only has to fill in the Discord OAuth credentials. The
//! path is `$DASHBOARD_CONFIG`, else `<config-dir>/percy-dashboard/config.json`.

use std::{
    net::SocketAddr,
    path::{Path, PathBuf},
    sync::OnceLock,
};

use anyhow::Context;
use kls_web_core::key::SecretKey;
use serde::{Deserialize, Serialize};

/// Top-level dashboard configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    /// Address the server binds to. `DASHBOARD_PORT` overrides the port at boot.
    #[serde(default = "default_bind")]
    pub bind: SocketAddr,
    /// Public base URL the dashboard is served from, used to build absolute OAuth
    /// redirect URLs. No trailing slash.
    #[serde(default = "default_base_url")]
    pub base_url: String,
    /// HMAC key for signed cookies (sessions, flash). Generated on first run.
    pub secret_key: SecretKey,
    /// Discord OAuth2 application credentials.
    #[serde(default)]
    pub discord: DiscordConfig,
    /// Connection to Percy's internal API.
    #[serde(default)]
    pub percy: PercyConfig,
    /// Optional shared key for the cross-app SSO handoff from the klappstuhl.me
    /// main site. Set to the **same** value as the main site's `sso_secret` to let
    /// a user already logged in there arrive here already signed in (via
    /// `GET /auth/handoff`). Unset → only the dashboard's own Discord login works.
    #[serde(default)]
    pub sso_secret: Option<SecretKey>,
    /// Reverse proxies in front of the app that append to `X-Forwarded-For`. The API
    /// rate limiter reads the client address that many entries from the right of the
    /// header; `0` ignores the headers and uses the socket peer. Default `1` (one
    /// reverse proxy, the production deploy). Set `0` when exposed directly, otherwise
    /// clients can forge the header and dodge per-IP limits.
    #[serde(default = "default_trusted_proxies")]
    pub trusted_proxies: u8,
}

fn default_trusted_proxies() -> u8 {
    1
}

/// Discord OAuth2 application credentials (from the Discord developer portal).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct DiscordConfig {
    pub client_id: String,
    pub client_secret: String,
    /// The OAuth2 redirect URI registered with the Discord app. Empty → derived
    /// from `base_url` as `<base_url>/auth/discord/callback`.
    #[serde(default)]
    pub redirect_uri: String,
}

/// Percy internal-API connection. Both fields fall back to the `PERCY_API_URL` /
/// `PERCY_API_TOKEN` environment variables when blank, so container deployments
/// can inject them without editing the file.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct PercyConfig {
    #[serde(default)]
    pub api_url: Option<String>,
    #[serde(default)]
    pub api_token: Option<String>,
    /// Percy's bot application id, used to build "add Percy to your server" invite
    /// links. Falls back to the OAuth `client_id` when unset (usually the same app).
    #[serde(default)]
    pub bot_client_id: Option<String>,
    /// klappstuhl.me API token with `images:read` scope, used for the external
    /// malware scan endpoint (`/api/v1/scan`). Falls back to `PERCY_SCAN_TOKEN`.
    #[serde(default)]
    pub scan_token: Option<String>,
}

/// The public base URL (no trailing slash), published for templates that build
/// absolute asset URLs — the link-preview `og:image` in `layout.html` needs an
/// absolute HTTPS URL for Discord/Slack to fetch it. Set once at startup from the
/// loaded config; falls back to the production URL when unset (e.g. in the
/// render-only template tests that never build application state).
pub static PUBLIC_BASE_URL: OnceLock<String> = OnceLock::new();

/// Absolute base URL for use in templates, with a production fallback so a
/// `.render()` before startup never panics.
pub fn public_base_url() -> &'static str {
    PUBLIC_BASE_URL
        .get()
        .map(String::as_str)
        .unwrap_or("https://percy.klappstuhl.me")
}

fn default_bind() -> SocketAddr {
    SocketAddr::from(([127, 0, 0, 1], 8091))
}

fn default_base_url() -> String {
    "http://127.0.0.1:8091".to_string()
}

impl Config {
    /// Loads the config, creating a default (with a fresh signing key) on first
    /// run. `DASHBOARD_PORT` overrides the bind port after loading.
    pub fn load() -> anyhow::Result<Self> {
        let path = config_path()?;
        let mut config = if path.exists() {
            let raw = std::fs::read_to_string(&path)
                .with_context(|| format!("could not read config file at {}", path.display()))?;
            serde_json::from_str(&raw).with_context(|| format!("could not parse config file at {}", path.display()))?
        } else {
            let config = Self::default_with_generated_key()?;
            config.write_to(&path)?;
            tracing::warn!(
                "no config found — wrote a default with a generated signing key to {}. \
                 Fill in the Discord OAuth credentials to enable login.",
                path.display()
            );
            config
        };

        if let Ok(port) = std::env::var("DASHBOARD_PORT") {
            if let Ok(port) = port.parse::<u16>() {
                config.bind.set_port(port);
            }
        }
        Ok(config)
    }

    fn default_with_generated_key() -> anyhow::Result<Self> {
        Ok(Self {
            bind: default_bind(),
            base_url: default_base_url(),
            secret_key: SecretKey::random().context("could not generate a signing key")?,
            discord: DiscordConfig::default(),
            percy: PercyConfig::default(),
            sso_secret: None,
            trusted_proxies: default_trusted_proxies(),
        })
    }

    fn write_to(&self, path: &Path) -> anyhow::Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("could not create config directory {}", parent.display()))?;
        }
        let json = serde_json::to_string_pretty(self).context("could not serialise the default config")?;
        std::fs::write(path, json).with_context(|| format!("could not write config to {}", path.display()))?;
        Ok(())
    }

    /// The effective Percy API base URL: the config value, or `PERCY_API_URL`.
    pub fn percy_api_url(&self) -> Option<String> {
        self.percy
            .api_url
            .clone()
            .filter(|s| !s.is_empty())
            .or_else(|| std::env::var("PERCY_API_URL").ok().filter(|s| !s.is_empty()))
    }

    /// The effective Percy API token: the config value, or `PERCY_API_TOKEN`.
    pub fn percy_api_token(&self) -> String {
        self.percy
            .api_token
            .clone()
            .filter(|s| !s.is_empty())
            .or_else(|| std::env::var("PERCY_API_TOKEN").ok())
            .unwrap_or_default()
    }

    /// The `Domain` attribute used when **clearing** the session cookie on logout.
    ///
    /// The dashboard normally sets a host-only session cookie (scoped to
    /// `percy.<domain>`). But if a session cookie ever ends up scoped to the
    /// registrable domain (`.klappstuhl.me`, which the main site uses for its own
    /// SSO cookie, or a leftover from an earlier deploy), a host-only expiry alone
    /// leaves it in the browser jar and the user stays logged in. Logout therefore
    /// also emits a clear at this scope.
    ///
    /// Derived from `base_url`'s host: the parent domain (leftmost label stripped)
    /// for a real multi-label host like `percy.klappstuhl.me` → `klappstuhl.me`;
    /// `None` for `localhost`/IP/dev so no invalid `Domain` is ever set.
    pub fn cookie_domain(&self) -> Option<String> {
        let host = self
            .base_url
            .split("://")
            .nth(1)
            .unwrap_or(&self.base_url)
            .split('/')
            .next()?
            .split(':')
            .next()?;
        if host.parse::<std::net::IpAddr>().is_ok() {
            return None;
        }
        let labels: Vec<&str> = host.split('.').collect();
        // Need a subdomain to strip (e.g. `percy.klappstuhl.me`). A bare
        // `klappstuhl.me` or `localhost` has nothing to widen to.
        if labels.len() < 3 {
            return None;
        }
        Some(labels[1..].join("."))
    }

    /// The OAuth2 redirect URI, derived from `base_url` when not set explicitly.
    pub fn discord_redirect_uri(&self) -> String {
        if self.discord.redirect_uri.is_empty() {
            format!("{}/auth/discord/callback", self.base_url.trim_end_matches('/'))
        } else {
            self.discord.redirect_uri.clone()
        }
    }

    /// The effective scan token: the config value, or `PERCY_SCAN_TOKEN`.
    pub fn percy_scan_token(&self) -> Option<String> {
        self.percy
            .scan_token
            .clone()
            .filter(|s| !s.is_empty())
            .or_else(|| std::env::var("PERCY_SCAN_TOKEN").ok().filter(|s| !s.is_empty()))
    }

    /// Whether Discord OAuth login is configured (credentials present).
    pub fn discord_login_enabled(&self) -> bool {
        !self.discord.client_id.is_empty() && !self.discord.client_secret.is_empty()
    }

    /// The application id used in bot-invite links: the configured `bot_client_id`,
    /// else the OAuth `client_id` (they're usually the same Discord app).
    pub fn invite_client_id(&self) -> String {
        self.percy
            .bot_client_id
            .clone()
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| self.discord.client_id.clone())
    }
}

#[cfg(test)]
impl Config {
    /// A minimal in-memory config for tests (random key, no external creds).
    pub fn test_default() -> Self {
        Self {
            bind: default_bind(),
            base_url: "http://localhost".to_string(),
            secret_key: SecretKey::random().expect("generate test key"),
            discord: DiscordConfig::default(),
            percy: PercyConfig {
                scan_token: Some("test-scan-token".to_string()),
                ..PercyConfig::default()
            },
            sso_secret: None,
            trusted_proxies: default_trusted_proxies(),
        }
    }
}

/// Resolves the config file path: `$DASHBOARD_CONFIG`, else
/// `<config-dir>/percy-dashboard/config.json`.
fn config_path() -> anyhow::Result<PathBuf> {
    if let Ok(explicit) = std::env::var("DASHBOARD_CONFIG") {
        return Ok(PathBuf::from(explicit));
    }
    let mut path = dirs::config_dir().context("could not find a config directory for the current user")?;
    path.push("percy-dashboard");
    path.push("config.json");
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn config_with_base(base: &str) -> Config {
        let mut c = Config::test_default();
        c.base_url = base.to_string();
        c
    }

    #[test]
    fn cookie_domain_widens_subdomain_to_parent() {
        assert_eq!(
            config_with_base("https://percy.klappstuhl.me")
                .cookie_domain()
                .as_deref(),
            Some("klappstuhl.me")
        );
        assert_eq!(
            config_with_base("https://dash.percy.klappstuhl.me")
                .cookie_domain()
                .as_deref(),
            Some("percy.klappstuhl.me")
        );
    }

    #[test]
    fn cookie_domain_is_none_for_dev_and_bare_domains() {
        // No subdomain to widen to.
        assert_eq!(config_with_base("https://klappstuhl.me").cookie_domain(), None);
        // localhost / IP / port — never a valid Domain attribute.
        assert_eq!(config_with_base("http://localhost").cookie_domain(), None);
        assert_eq!(config_with_base("http://127.0.0.1:8091").cookie_domain(), None);
        assert_eq!(config_with_base("http://localhost:8091").cookie_domain(), None);
    }
}
