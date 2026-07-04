//! The dashboard home: the signed-in user's servers.
//!
//! Mirrors the split the monolith used, sourced from two places:
//! - **Percy** knows the guilds it *shares* with the user (`get_user_guilds`),
//!   each tagged `manageable` → **Managed** (full access) vs **Servers you're in**.
//! - The **OAuth admin-guild cache** (`db::admin_guilds_for_user`, captured at login)
//!   adds servers the user administrates that Percy isn't in yet → **Add Percy**.

use std::collections::HashSet;

use askama::Template;
use axum::{
    extract::{Path, State},
    response::{IntoResponse, Redirect, Response},
};
use percy_client::{GuildInfo, PercyError, UserGuild};

use crate::{session::Session, AppState};

/// A greyed "Add Percy" card — a server the user administrates but Percy isn't in.
pub struct AddPercyCard {
    pub name: String,
    pub icon_url: Option<String>,
    pub owner: bool,
    /// Bot-invite URL with this guild pre-selected.
    pub invite_url: String,
}

#[derive(Template)]
#[template(path = "guilds.html")]
struct GuildsTemplate {
    username: String,
    /// Percy is here and the user can manage it — full dashboard cards.
    managed: Vec<UserGuild>,
    /// Percy is here but the user can't manage it — read-only overview cards.
    public: Vec<UserGuild>,
    /// Servers the user manages where Percy isn't a member yet.
    add_percy: Vec<AddPercyCard>,
    /// Invite URL with no guild pre-selected (the empty-state / header button).
    invite_url: String,
}

/// `GET /dashboard` — the guild list. The [`Session`] extractor redirects to
/// `/login` when the visitor isn't signed in.
pub async fn dashboard(State(app): State<AppState>, session: Session) -> Response {
    let discord_id = session.discord_user_id.as_str();

    // Percy's shared-guild list and the OAuth admin cache are independent reads —
    // fetch them together. Either degrading just thins the page, never errors.
    let (mutual, admin) = tokio::join!(fetch_mutual_guilds(&app, discord_id), async {
        crate::db::admin_guilds_for_user(&app.db, discord_id)
            .await
            .unwrap_or_default()
    });

    // Guilds Percy already shares with the user — excluded from "Add Percy".
    let present: HashSet<String> = mutual.iter().map(|g| g.id.clone()).collect();

    let add_percy: Vec<AddPercyCard> = admin
        .into_iter()
        .filter(|g| !present.contains(&g.guild_id))
        .map(|g| AddPercyCard {
            invite_url: build_invite_url(&app.config.invite_client_id(), Some(&g.guild_id)),
            icon_url: g
                .icon
                .as_deref()
                .map(|hash| format!("https://cdn.discordapp.com/icons/{}/{}.png", g.guild_id, hash)),
            name: g.name,
            owner: g.owner,
        })
        .collect();

    let (managed, public): (Vec<UserGuild>, Vec<UserGuild>) = mutual.into_iter().partition(|g| g.manageable);

    GuildsTemplate {
        username: session.username,
        managed,
        public,
        add_percy,
        invite_url: build_invite_url(&app.config.invite_client_id(), None),
    }
    .into_response()
}

/// Percy's view of the guilds it shares with the user. Returns an empty list when
/// Percy isn't configured or the call fails — the page still renders.
async fn fetch_mutual_guilds(app: &AppState, discord_id: &str) -> Vec<UserGuild> {
    let Some(percy) = &app.percy else {
        return Vec::new();
    };
    percy.get_user_guilds(discord_id).await.unwrap_or_default()
}

/// One resolved config value (a channel/role name, or `None` for unset).
pub struct ConfigRow {
    pub label: &'static str,
    pub value: Option<String>,
}

/// One boolean feature toggle, for a summary badge.
pub struct FlagRow {
    pub label: &'static str,
    pub on: bool,
}

#[derive(Template)]
#[template(path = "guild.html")]
struct GuildDetailTemplate {
    guild_id: u64,
    name: String,
    icon_url: Option<String>,
    member_count: Option<u32>,
    prefixes: Vec<String>,
    flags: Vec<FlagRow>,
    channels: Vec<ConfigRow>,
    roles: Vec<ConfigRow>,
    ai_flags: Vec<FlagRow>,
}

#[derive(Template)]
#[template(path = "guild_not_found.html")]
struct GuildNotFoundTemplate {
    invite_url: String,
}

/// `GET /dashboard/guild/{id}` — the per-guild overview. Requires a session, and
/// that the user actually manages the guild (else back to the list). Read-only for
/// now; editable tabs land in later slices.
pub async fn guild_detail(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let Some(percy) = &app.percy else {
        // Without Percy there's nothing to configure — send them home.
        return Redirect::to("/dashboard").into_response();
    };

    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return Redirect::to("/dashboard").into_response();
    }

    let guild = match percy.get_guild(guild_id).await {
        Ok(guild) => guild,
        Err(PercyError::NotFound) => {
            // Percy isn't in this guild (yet) — offer the invite.
            return GuildNotFoundTemplate {
                invite_url: build_invite_url(&app.config.invite_client_id(), Some(&guild_id.to_string())),
            }
            .into_response();
        }
        Err(e) => {
            tracing::error!(error = %e, guild_id, "failed to load guild config");
            return Redirect::to("/dashboard").into_response();
        }
    };

    detail_template(guild).into_response()
}

/// Whether the user manages (Manage Server / Administrator) a guild Percy shares
/// with them — the gate for the management pages. Fails closed.
async fn user_can_manage(app: &AppState, discord_id: &str, guild_id: u64) -> bool {
    fetch_mutual_guilds(app, discord_id)
        .await
        .iter()
        .any(|g| g.manageable && g.id == guild_id.to_string())
}

/// Turns a `GuildInfo` into the read-only overview view-model.
fn detail_template(guild: GuildInfo) -> GuildDetailTemplate {
    let channel = |label, ch: Option<percy_client::ChannelRef>| ConfigRow {
        label,
        value: ch.map(|c| c.name),
    };
    let role = |label, r: Option<percy_client::RoleRef>| ConfigRow {
        label,
        value: r.map(|r| r.name),
    };

    let flags = vec![
        FlagRow {
            label: "Audit log",
            on: guild.flags.audit_log,
        },
        FlagRow {
            label: "Raid protection",
            on: guild.flags.raid,
        },
        FlagRow {
            label: "Alerts",
            on: guild.flags.alerts,
        },
        FlagRow {
            label: "Sentinel",
            on: guild.flags.sentinel,
        },
        FlagRow {
            label: "Mention guard",
            on: guild.flags.mentions,
        },
    ];

    let ai = &guild.ai.flags;
    let ai_flags = vec![
        FlagRow {
            label: "Assistant",
            on: ai.assistant,
        },
        FlagRow {
            label: "Command router",
            on: ai.router,
        },
        FlagRow {
            label: "Moderation",
            on: ai.moderation,
        },
        FlagRow {
            label: "Sentinel",
            on: ai.sentinel,
        },
        FlagRow {
            label: "Music",
            on: ai.music,
        },
        FlagRow {
            label: "Polls",
            on: ai.polls,
        },
        FlagRow {
            label: "Giveaways",
            on: ai.giveaways,
        },
        FlagRow {
            label: "Tags",
            on: ai.tags,
        },
    ];

    let channels = vec![
        channel("Audit log", guild.audit_log_channel),
        channel("Mod log", guild.mod_log_channel),
        channel("Message log", guild.message_log_channel),
        channel("Voice log", guild.voice_log_channel),
        channel("Alerts", guild.alert_channel),
        channel("Polls", guild.poll_channel),
        channel("Music panel", guild.music_panel_channel),
    ];

    let roles = vec![
        role("Mute role", guild.mute_role),
        role("Poll ping role", guild.poll_ping_role),
    ];

    GuildDetailTemplate {
        guild_id: guild.id,
        name: guild.name,
        icon_url: guild.icon_url,
        member_count: guild.member_count,
        prefixes: guild.prefixes,
        flags,
        channels,
        roles,
        ai_flags,
    }
}

/// Builds a bot-invite URL, optionally pre-selecting a guild.
fn build_invite_url(client_id: &str, guild_id: Option<&str>) -> String {
    let base = format!(
        "https://discord.com/oauth2/authorize?client_id={client_id}&scope=bot+applications.commands&permissions=8"
    );
    match guild_id {
        Some(gid) => format!("{base}&guild_id={gid}"),
        None => base,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn invite_url_optionally_pins_a_guild() {
        let general = build_invite_url("123", None);
        assert!(general.contains("client_id=123"));
        assert!(general.contains("scope=bot+applications.commands"));
        assert!(!general.contains("guild_id="));

        let pinned = build_invite_url("123", Some("999"));
        assert!(pinned.contains("guild_id=999"));
    }

    #[test]
    fn template_renders_all_three_sections() {
        let html = GuildsTemplate {
            username: "ferris".into(),
            managed: vec![UserGuild {
                id: "1".into(),
                name: "Managed Server".into(),
                icon_url: None,
                member_count: Some(42),
                owner: true,
                manageable: true,
            }],
            public: vec![UserGuild {
                id: "2".into(),
                name: "Public Server".into(),
                icon_url: None,
                member_count: None,
                owner: false,
                manageable: false,
            }],
            add_percy: vec![AddPercyCard {
                name: "Unadded Server".into(),
                icon_url: None,
                owner: false,
                invite_url: "https://discord.com/oauth2/authorize?client_id=1&guild_id=3".into(),
            }],
            invite_url: "https://discord.com/oauth2/authorize?client_id=1".into(),
        }
        .render()
        .expect("render");

        assert!(html.contains("Managed Server"));
        assert!(html.contains("Public Server"));
        assert!(html.contains("Unadded Server"));
        assert!(html.contains("/dashboard/guild/1"));
    }

    #[test]
    fn detail_template_renders_overview() {
        let html = GuildDetailTemplate {
            guild_id: 7,
            name: "My Guild".into(),
            icon_url: None,
            member_count: Some(120),
            prefixes: vec!["!".into(), "?".into()],
            flags: vec![FlagRow {
                label: "Audit log",
                on: true,
            }],
            channels: vec![
                ConfigRow {
                    label: "Audit log",
                    value: Some("mod-log".into()),
                },
                ConfigRow {
                    label: "Alerts",
                    value: None,
                },
            ],
            roles: vec![ConfigRow {
                label: "Mute role",
                value: Some("Muted".into()),
            }],
            ai_flags: vec![FlagRow {
                label: "Assistant",
                on: false,
            }],
        }
        .render()
        .expect("render");

        assert!(html.contains("My Guild"));
        assert!(html.contains("120 members"));
        assert!(html.contains("#mod-log"));
        assert!(html.contains("Not set")); // the unset Alerts channel
        assert!(html.contains("@Muted"));
    }

    #[test]
    fn not_found_template_offers_the_invite() {
        let html = GuildNotFoundTemplate {
            invite_url: "https://discord.com/oauth2/authorize?client_id=1&guild_id=9".into(),
        }
        .render()
        .expect("render");
        assert!(html.contains("isn't in this server"));
        assert!(html.contains("guild_id=9"));
    }
}
