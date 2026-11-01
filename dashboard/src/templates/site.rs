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
    /// Total commands across all categories, subcommands included.
    pub(crate) total: usize,
    /// Pre-serialised schema.org `ItemList` (already safe to embed in a `<script>`).
    pub(crate) json_ld: String,
}

/// A command category for the public commands page.
pub(crate) struct CommandCategory {
    pub(crate) name: String,
    /// Anchor-safe id (`cat-economy`).
    pub(crate) slug: String,
    /// Commands in the category, subcommands included.
    pub(crate) count: usize,
    pub(crate) commands: Vec<PublicCommand>,
}

/// A single command entry for the public commands page. Subcommands of a group are nested in
/// `subs` (flattened: a sub-sub-command lists under the root group with its full name).
pub(crate) struct PublicCommand {
    pub(crate) name: String,
    /// Anchor id: the slug of the qualified name (`economy-shop-add`).
    pub(crate) slug: String,
    pub(crate) description: String,
    pub(crate) help: Option<String>,
    pub(crate) usage: Option<String>,
    pub(crate) example: Option<String>,
    pub(crate) aliases: Vec<String>,
    pub(crate) slash: bool,
    pub(crate) user_permissions: Vec<String>,
    pub(crate) bot_permissions: Vec<String>,
    /// Human text such as "1 use per 30s".
    pub(crate) cooldown: Option<String>,
    /// Lower-cased name, aliases and description for the client-side search.
    pub(crate) search: String,
    pub(crate) subs: Vec<PublicCommand>,
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

/// Rounded headline numbers for the landing page's stats bar.
#[derive(Clone)]
pub(crate) struct HomeStats {
    pub(crate) servers: String,
    pub(crate) members: String,
    /// Empty when the commands catalogue could not be fetched (that tile is then skipped).
    pub(crate) commands: String,
    pub(crate) uptime: String,
}

/// One tile of the landing page's module directory.
#[derive(Clone)]
pub(crate) struct HomeModule {
    pub(crate) name: String,
    /// Anchor on `/commands` (`cat-economy`).
    pub(crate) slug: String,
    pub(crate) count: usize,
}

/// The public landing page. `stats` is `None` when Percy is unreachable, which drops the whole bar.
#[derive(Template)]
#[template(path = "site/landing.html")]
pub(crate) struct HomeTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) invite_url: String,
    pub(crate) stats: Option<HomeStats>,
    pub(crate) modules: Vec<HomeModule>,
    /// Screenshot basenames (`1-inventory`) present under `static/img/home/`.
    pub(crate) shots: Vec<&'static str>,
}

impl HomeTemplate {
    pub(crate) fn has(&self, shot: &str) -> bool {
        self.shots.contains(&shot)
    }
}
