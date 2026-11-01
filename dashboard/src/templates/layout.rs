//! Shared view-models for the layout partials (progress toggle, ticker, chips).

#![allow(dead_code)]

// -- Progress ------------------------------------------------------------------

/// One read/watched control, as `templates/layout/progress_toggle.html` renders it.
///
/// Everything the markup needs is resolved in `progress.rs` — which verb this medium
/// takes, whether it is already marked, where a JS-off POST comes back to — so the macro
/// stays a layout and the rules stay testable.
pub(crate) struct ProgressToggle {
    /// Signed out this is not a dead button: the macro renders the sign-in link instead.
    pub(crate) signed_in: bool,
    pub(crate) marked: bool,
    /// `comic` / `title` — half of the identity, and what decides the verb.
    pub(crate) media: String,
    /// The card's `key` verbatim (`{brand}/{slug}`, `{universe}/{slug}`).
    pub(crate) release_key: String,
    /// `read` / `watched` — Percy 400s the wrong one for a medium rather than coercing it.
    pub(crate) status: String,
    /// `Mark read` / `Mark watched`.
    pub(crate) label: String,
    /// `Read` / `Watched` — what the control says once it is on.
    pub(crate) marked_label: String,
    /// The page this control is on, posted back so a JS-off click lands where it started.
    pub(crate) redirect: String,
    pub(crate) sign_in_href: String,
}

/// `done of total` on an order page, plus the percentage its bar is drawn at.
pub(crate) struct ProgressSummary {
    pub(crate) done: usize,
    pub(crate) total: usize,
    pub(crate) percent: usize,
    /// `read` / `watched` — the participle the count is phrased with.
    pub(crate) verb: String,
}

impl ProgressSummary {
    /// The summary for a page, or `None` when there is nothing to count (signed out, or
    /// an empty list) — an order page must not draw a "0 of 0" bar.
    pub(crate) fn new(signed_in: bool, done: usize, total: usize, verb: &str) -> Option<Self> {
        (signed_in && total > 0).then(|| Self {
            done,
            total,
            percent: done * 100 / total,
            verb: verb.to_string(),
        })
    }
}

/// One subject chip. `href` is `None` when no catalogue page exists for that subject —
/// a franchise, a creator — and the chip renders as plain text rather than a dead link.
pub(crate) struct ChipRow {
    pub(crate) label: String,
    pub(crate) href: Option<String>,
}

/// One headline in the ticker (§6.3), rendered server-side into the page it decorates.
pub(crate) struct TickerItem {
    pub(crate) url: String,
    pub(crate) headline: String,
    pub(crate) source_name: String,
}
