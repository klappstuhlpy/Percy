//! The dashboard home (the user's servers) and the per-guild pages, rendered
//! through the templates ported verbatim from the klappstuhl_me monolith.
//!
//! Guild buckets come from two sources:
//! - **Percy** knows the guilds it *shares* with the user (`get_user_guilds`),
//!   each tagged `manageable` → **Managed** vs **Servers you're in**.
//! - The **OAuth admin-guild cache** (`db::admin_guilds_for_user`, captured at
//!   login) adds servers the user administrates that Percy isn't in → **Add Percy**.

use std::collections::HashSet;

use axum::{
    extract::{Path, State},
    response::{IntoResponse, Redirect, Response},
};
use percy_client::{GuildInfo, LockdownsResponse, PercyError, StatusFeedInfo, UserGuild};

use crate::{
    session::Session,
    templates::{AddPercyGuild, GuildNotFoundTemplate, GuildTemplate, GuildsTemplate, MembersTemplate},
    view::Viewer,
    AppState,
};

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

    let add_percy: Vec<AddPercyGuild> = admin
        .into_iter()
        .filter(|g| !present.contains(&g.guild_id))
        .map(|g| AddPercyGuild {
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
        account: Some(Viewer::from_session(&session)),
        managed,
        public,
        add_percy,
        invite_url: build_invite_url(&app.config.invite_client_id(), None),
    }
    .into_response()
}

/// `GET /dashboard/guild/{id}` — the per-guild configuration page. Requires a
/// session and that the user manages the guild (else back to the list).
pub async fn guild_detail(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let Some(percy) = &app.percy else {
        return Redirect::to("/dashboard").into_response();
    };

    if !user_can_manage(&app, &session.discord_user_id, guild_id).await {
        return Redirect::to("/dashboard").into_response();
    }

    // Fan out the independent reads; a single degraded sub-resource doesn't abort
    // the others (join!, not try_join!) — each is handled below.
    let (guild, channels, roles, sentinel, lockdowns, status_feed, bot_profile) = tokio::join!(
        percy.get_guild(guild_id),
        percy.get_guild_channels(guild_id),
        percy.get_guild_roles(guild_id),
        percy.get_sentinel(guild_id),
        percy.get_lockdowns(guild_id),
        percy.get_status_feed(guild_id),
        percy.get_custom_bot(guild_id),
    );

    let guild = match guild {
        Ok(guild) => guild,
        Err(PercyError::NotFound) => {
            return GuildNotFoundTemplate {
                account: Some(Viewer::from_session(&session)),
                guild_id,
                invite_url: build_invite_url(&app.config.invite_client_id(), Some(&guild_id.to_string())),
            }
            .into_response();
        }
        Err(e) => {
            tracing::error!(error = %e, guild_id, "failed to load guild config");
            return Redirect::to("/dashboard").into_response();
        }
    };

    // If the role/channel pickers failed to load we warn rather than silently
    // showing empty dropdowns (saving with empty pickers could clobber config).
    let degraded = channels.is_err() || roles.is_err();

    GuildTemplate {
        account: Some(Viewer::from_session(&session)),
        guild,
        channels: channels.unwrap_or_default(),
        roles: roles.unwrap_or_default(),
        sentinel: sentinel.ok().flatten(),
        lockdowns: lockdowns.unwrap_or(LockdownsResponse { entries: Vec::new() }),
        status_feed: status_feed.unwrap_or(StatusFeedInfo {
            subscribed: false,
            channel: None,
        }),
        bot_profile: bot_profile.unwrap_or_default(),
        degraded,
    }
    .into_response()
}

/// `GET /dashboard/guild/{id}/members` — the members tab (initial page; live
/// search/pagination is driven client-side against the JSON endpoint).
pub async fn guild_members(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");

    let (members, roles) = tokio::join!(
        percy.get_guild_members(guild_id, 100, 0, None),
        percy.get_guild_roles(guild_id),
    );

    MembersTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "members",
        page_title: "Members",
        members: members.map(|r| r.members).unwrap_or_default(),
        roles: roles.unwrap_or_default(),
    }
    .into_response()
}

/// Shared prelude for the per-guild feature pages: requires a session, checks the
/// user manages the guild, and loads its config. Returns a redirect [`Response`]
/// (to `/dashboard`) on any failure so callers can early-return.
pub(crate) async fn gate_and_load_guild(
    app: &AppState,
    session: &Session,
    guild_id: u64,
) -> Result<GuildInfo, Response> {
    let Some(percy) = &app.percy else {
        return Err(Redirect::to("/dashboard").into_response());
    };
    if !user_can_manage(app, &session.discord_user_id, guild_id).await {
        return Err(Redirect::to("/dashboard").into_response());
    }
    percy
        .get_guild(guild_id)
        .await
        .map_err(|_| Redirect::to("/dashboard").into_response())
}

/// Percy's view of the guilds it shares with the user. Returns an empty list when
/// Percy isn't configured or the call fails — the page still renders.
async fn fetch_mutual_guilds(app: &AppState, discord_id: &str) -> Vec<UserGuild> {
    let Some(percy) = &app.percy else {
        return Vec::new();
    };
    percy.get_user_guilds(discord_id).await.unwrap_or_default()
}

/// Whether the user manages (Manage Server / Administrator) a guild Percy shares
/// with them — the gate for the management pages. Fails closed.
pub(crate) async fn user_can_manage(app: &AppState, discord_id: &str, guild_id: u64) -> bool {
    fetch_mutual_guilds(app, discord_id)
        .await
        .iter()
        .any(|g| g.manageable && g.id == guild_id.to_string())
}

/// Whether the user merely *shares* a guild with Percy (is a member), regardless
/// of manage permissions — the weaker gate for the read-only overview and the
/// user's own self/history/data pages. Fails closed.
pub(crate) async fn user_is_member(app: &AppState, discord_id: &str, guild_id: u64) -> bool {
    fetch_mutual_guilds(app, discord_id)
        .await
        .iter()
        .any(|g| g.id == guild_id.to_string())
}

/// Builds a bot-invite URL, optionally pre-selecting a guild.
pub(crate) fn build_invite_url(client_id: &str, guild_id: Option<&str>) -> String {
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
    use askama::Template;
    use percy_client::UserGuild;

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
    fn guilds_template_renders_verbatim() {
        let html = GuildsTemplate {
            account: Some(Viewer { name: "ferris".into() }),
            managed: vec![UserGuild {
                id: "1".into(),
                name: "Managed Server".into(),
                icon_url: None,
                member_count: Some(42),
                owner: true,
                manageable: true,
            }],
            public: Vec::new(),
            add_percy: vec![AddPercyGuild {
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
        assert!(html.contains("Unadded Server"));
        assert!(html.contains("/dashboard/guild/1"));
        assert!(html.contains("ferris")); // the account menu from the shared layout
    }
}
