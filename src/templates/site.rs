//! Public site pages: home, legal, changelog, commands, server list.

#![allow(dead_code)]

use crate::view::Viewer;
use askama::Template;
use percy_client::*;

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "site/guilds.html")]
pub(crate) struct GuildsTemplate {
    pub(crate) account: Option<Viewer>,
    /// Servers the user can manage and Percy is in — full admin dashboard cards.
    pub(crate) managed: Vec<UserGuild>,
    /// Servers the user is in (but can't manage) where Percy is present —
    /// read-only public overview cards.
    pub(crate) public: Vec<UserGuild>,
    /// Servers the user can manage but Percy is *not* in yet — greyed "Add Percy"
    /// cards that link to the bot invite (captured from Discord OAuth).
    pub(crate) add_percy: Vec<AddPercyGuild>,
    /// Invite URL (no guild pre-selected) for the "Invite Percy" empty-state button.
    pub(crate) invite_url: String,
}

/// A greyed "Add Percy" card: a server the user administrates but Percy isn't in.
pub(crate) struct AddPercyGuild {
    pub(crate) name: String,
    pub(crate) icon_url: Option<String>,
    pub(crate) owner: bool,
    /// Bot invite URL with this guild pre-selected.
    pub(crate) invite_url: String,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "site/no_discord.html")]
pub(crate) struct NoDiscordTemplate {
    pub(crate) account: Option<Viewer>,
}

/// Logged-out welcome/landing page shown at `/dashboard` to visitors who
/// haven't signed in yet. Introduces the bot and points to login/signup (which
/// redirect back to the dashboard afterwards).
#[allow(dead_code)]
#[derive(Template)]
#[template(path = "site/landing.html")]
pub(crate) struct PercyLandingTemplate {
    pub(crate) account: Option<Viewer>,
}

/// A public Percy legal document (Privacy Policy / Terms of Service). `content`
/// is pre-rendered, trusted HTML produced from the canonical GitHub markdown.
#[allow(dead_code)]
#[derive(Template)]
#[template(path = "site/legal.html")]
pub(crate) struct PercyLegalTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) title: &'static str,
    pub(crate) content: String,
}

/// Public commands page: lists all bot commands with categories and descriptions.
#[allow(dead_code)]
#[derive(Template)]
#[template(path = "site/commands_public.html")]
pub(crate) struct PercyCommandsTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) categories: Vec<CommandCategory>,
    pub(crate) flags_md: String,
}

/// A command category for the public commands page.
pub(crate) struct CommandCategory {
    pub(crate) name: String,
    pub(crate) commands: Vec<PublicCommand>,
}

/// A single command entry for the public commands page.
pub(crate) struct PublicCommand {
    pub(crate) name: String,
    pub(crate) description: String,
    pub(crate) usage: Option<String>,
}

/// Public changelog page showing recent version history.
#[allow(dead_code)]
#[derive(Template)]
#[template(path = "site/changelog.html")]
pub(crate) struct PercyChangelogTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) entries: Vec<ChangelogRelease>,
    pub(crate) current_version: String,
}

/// A changelog release with categorized sections.
pub(crate) struct ChangelogRelease {
    pub(crate) version: String,
    pub(crate) date: String,
    pub(crate) sections: Vec<ChangelogSection>,
}

/// A category section within a release (Added, Changed, Fixed, etc.).
pub(crate) struct ChangelogSection {
    pub(crate) name: String,
    pub(crate) slug: String,
    pub(crate) entries: Vec<String>,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "site/guild_not_found.html")]
pub(crate) struct GuildNotFoundTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) invite_url: String,
}
