//! Signed-in account pages: settings, user lookup, subscriptions.

#![allow(dead_code)]

use crate::view::Viewer;
use askama::Template;
use percy_client::*;
use std::collections::HashMap;

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "account/user_dashboard.html")]
pub(crate) struct UserDashboardTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) guild_icon: Option<String>,
    pub(crate) profile: MemberSelf,
    pub(crate) settings: UserSettings,
    /// The rank card pickers; empty when the server locks the style (the section is hidden).
    pub(crate) card_fields: Vec<CardField>,
    /// The member's running reminders (everywhere, not just this server).
    pub(crate) reminders: Vec<percy_client::Reminder>,
}

/// One rank card picker: its stored choice (`""` = follow the server) and the presets.
pub(crate) struct CardField {
    pub(crate) key: &'static str,
    pub(crate) label: &'static str,
    pub(crate) current: String,
    /// Text of the empty option, e.g. "Server default (Ink)".
    pub(crate) default_label: String,
    pub(crate) options: Vec<percy_client::CardOption>,
}

pub(crate) const CARD_FIELDS: [(&str, &str); 4] = [
    ("theme", "Background theme"),
    ("accent", "Accent colour"),
    ("bar", "Progress bar"),
    ("font", "Name font"),
];

impl CardField {
    /// The pickers for a member's card, or none when the server has locked the style.
    pub(crate) fn from_rank_card(card: &percy_client::RankCard) -> Vec<CardField> {
        if card.locked {
            return Vec::new();
        }
        Self::build(&card.style, &card.guild_default, &card.options, "Server default")
    }

    /// The pickers for a server's default card style (empty choice = built-in look).
    pub(crate) fn for_server(config: &percy_client::LevelingConfig) -> Vec<CardField> {
        Self::build(&config.card_style, &HashMap::new(), &config.card_options, "Built-in")
    }

    fn build(
        style: &HashMap<String, String>,
        fallback: &HashMap<String, String>,
        options: &HashMap<String, Vec<percy_client::CardOption>>,
        default_word: &'static str,
    ) -> Vec<CardField> {
        CARD_FIELDS
            .iter()
            .map(|(key, label)| {
                let options = options.get(*key).cloned().unwrap_or_default();
                let fallback = fallback.get(*key).cloned().unwrap_or_default();
                let fallback_label = options
                    .iter()
                    .find(|o| o.value == fallback)
                    .or_else(|| options.first())
                    .map(|o| o.label.clone())
                    .unwrap_or_default();
                CardField {
                    key,
                    label,
                    current: style.get(*key).cloned().unwrap_or_default(),
                    default_label: format!("{default_word} ({fallback_label})"),
                    options,
                }
            })
            .collect()
    }
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "account/account.html")]
pub(crate) struct AccountTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) settings: UserSettings,
    pub(crate) discord_id: String,
    pub(crate) username: String,
}

#[allow(dead_code)]
#[derive(Template)]
#[template(path = "account/user.html")]
pub(crate) struct UserLookupTemplate {
    pub(crate) account: Option<Viewer>,
    pub(crate) guild_id: u64,
    pub(crate) guild_name: String,
    pub(crate) nav_active: &'static str,
    pub(crate) page_title: String,
    pub(crate) member: MemberDetail,
}

// -- Release subscriptions -----------------------------------------------------

/// `GET /account/subscriptions` — what the signed-in user follows, plus the picker.
#[derive(Template)]
#[template(path = "account/subscriptions.html")]
pub(crate) struct SubscriptionsTemplate {
    pub(crate) account: Option<Viewer>,
    /// One section per medium, in a fixed order, so the page never reshuffles.
    pub(crate) groups: Vec<SubscriptionGroup>,
    pub(crate) total: usize,
    /// The picker's current search term, echoed back so the form keeps it with JS off.
    pub(crate) query: String,
    /// Popular subjects on first paint, search results once a query is set.
    pub(crate) subjects: Vec<SubjectRow>,
    /// Percy could not be reached — an empty page is an outage, not "you follow nothing".
    pub(crate) unavailable: bool,
    /// A mutation failed and bounced back here (`?error=1`).
    pub(crate) errored: bool,
}

/// One medium's subscriptions.
pub(crate) struct SubscriptionGroup {
    pub(crate) media: String,
    pub(crate) label: String,
    pub(crate) rows: Vec<SubscriptionRow>,
}

/// One followed subject, with every control's state precomputed.
pub(crate) struct SubscriptionRow {
    pub(crate) media: String,
    pub(crate) subject_type: String,
    /// The opaque key — posted back verbatim, never the label.
    pub(crate) subject_value: String,
    pub(crate) label: String,
    /// The `Series` / `Person` / `Character` chip.
    pub(crate) type_label: String,
    pub(crate) streams: String,
    /// `dm` or `off`; `off` mutes without unfollowing.
    pub(crate) delivery: String,
    pub(crate) muted: bool,
}

/// One picker row. `followed` greys the subscribe button for something already followed.
pub(crate) struct SubjectRow {
    pub(crate) media: String,
    pub(crate) subject_type: String,
    pub(crate) subject_value: String,
    pub(crate) label: String,
    pub(crate) hint: String,
    pub(crate) followed: bool,
}
