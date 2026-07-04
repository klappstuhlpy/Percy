//! Small view-model shims that let the templates ported verbatim from the
//! klappstuhl_me monolith render unchanged.
//!
//! The monolith templates were written against its `Account` model, referencing
//! `account.name` and `account.has_discord()` in the shared layout. The dashboard
//! has no account system — just a Discord [`Session`] — so [`Viewer`] presents the
//! same two accessors over the session. Templates stay byte-for-byte identical.

use crate::session::Session;

/// The signed-in user, shaped for the templates' `account` slot.
pub struct Viewer {
    /// Display name, shown in the nav account menu.
    pub name: String,
}

impl Viewer {
    /// Builds the view-model from the current session.
    pub fn from_session(session: &Session) -> Self {
        Self {
            name: session.username.clone(),
        }
    }

    /// Always true — the dashboard only ever signs users in through Discord, so
    /// the layout's Discord-avatar branch is the right one.
    pub fn has_discord(&self) -> bool {
        true
    }
}
