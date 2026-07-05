//! Dashboard configuration, loaded from `config.json`.
//!
//! A deliberately small config — the dashboard needs far less than the main site.
//! On first run (no file) a default is written with a freshly generated signing
//! key so the operator only has to fill in the Discord OAuth credentials. The
//! path is `$DASHBOARD_CONFIG`, else `<config-dir>/percy-dashboard/config.json`.

use std::{
    net::SocketAddr,
    path::{Path, PathBuf},
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
    /// malware scan endpoint (`/api/scan`). Falls back to `PERCY_SCAN_TOKEN`.
    #[serde(default)]
    pub scan_token: Option<String>,
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
