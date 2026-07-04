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
    extract::State,
    response::{IntoResponse, Response},
};
use percy_client::UserGuild;

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
}
