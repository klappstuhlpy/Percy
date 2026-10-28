//! Read/watched marks — the toggle that appears on every catalogue surface, and the two
//! proxied writes behind it (§6.2's "interaction budget", plan phase R8).
//!
//! **Which store backs which surface.** Percy has two progress tables and they are not
//! interchangeable:
//!
//! * `release_progress` (V42) — keyed by `(media, release_key)`, the *same* string a card
//!   already carries as `key` (`{brand}/{slug}`, `{universe}/{slug}`). It is what
//!   `GET /releases/overview` ships its `progress` map from, and its API has a per-item
//!   unmark (`DELETE ?media=&release_key=`).
//! * `watch_progress` (V39) — keyed by `watch_titles.id`, behind
//!   `/watchlist/users/{id}/progress`, and untouched by R8.
//!
//! Every surface here — including the universe order page — uses the **first**, for one
//! decisive reason: R8's contract is a mark *plus its undo*, and V39's router exposes no
//! per-title delete. Its only `DELETE` resets a whole universe, so an "undo" built on it
//! would wipe the visitor's entire watch order on a mis-click. (`watch_progress` also has
//! no writer anywhere today — not this dashboard, not a cog — so nothing is being
//! abandoned.) The two tables stay separate; this module simply never writes the older one.
//!
//! Progressive enhancement, per §6.2 and D9: the control is a real `<form>` first, so a
//! JS-off visitor marks an item with a plain POST and is redirected back to the page they
//! were on. `static/js/progress.js` only intercepts that submit to write localStorage and
//! debounce the same call — the exact shape `/account/subscriptions` already uses for its
//! one-off actions. Nothing here is registered with `PercyUnsaved`: these are actions, not
//! edits to a settings form.
//!
//! D4 holds throughout: every write is a proxied Percy call, never a database touch.

use axum::{
    extract::State,
    http::HeaderMap,
    response::{IntoResponse, Response},
    Form,
};
use percy_client::{ProgressEntry, ProgressMap};
use serde::Deserialize;

use crate::{errors::percy_message, forms::json_or_redirect, session::Session, templates::ProgressToggle, AppState};

/// Where a mutation bounces back to when the form carried no usable `redirect`.
const FALLBACK: &str = "/releases";

/// The status each medium takes. Percy **rejects** the wrong one with a 400 rather than
/// translating it: `read` and `watched` are the same gesture in two media, but `media` also
/// namespaces `release_key`, so a client that sent the wrong verb has probably named an item
/// in the other catalogue. The pairing is decided here, once, and never by a caller.
pub(crate) fn status_for(media: &str) -> &'static str {
    if media == "comic" {
        "read"
    } else {
        "watched"
    }
}

/// Whether `key` is marked in `map`. Both media are always present in Percy's payload, but
/// `get` is still the read: a signed-out page has an empty map and must not panic.
pub(crate) fn is_marked(map: &ProgressMap, media: &str, key: &str) -> bool {
    map.get(media).map(|marks| marks.contains_key(key)).unwrap_or(false)
}

/// The toggle for one item, or the "Sign in to track" link when there is no session.
///
/// `redirect` is where a JS-off POST comes back to — the page the control is on, so the
/// visitor lands where they clicked rather than on a generic page.
pub(crate) fn toggle(map: &ProgressMap, signed_in: bool, media: &str, key: &str, redirect: &str) -> ProgressToggle {
    let marked = is_marked(map, media, key);
    let comic = media == "comic";
    ProgressToggle {
        signed_in,
        marked,
        status: status_for(media).to_string(),
        label: if comic { "Mark read" } else { "Mark watched" }.to_string(),
        marked_label: if comic { "Read" } else { "Watched" }.to_string(),
        sign_in_href: format!("/login?next={}", redirect.replace('%', "%25").replace('&', "%26").replace('#', "%23").replace('+', "%2B")),
        redirect: redirect.to_string(),
        media: media.to_string(),
        release_key: key.to_string(),
    }
}

/// `done of total` for an order page, counted over exactly the keys on the page.
///
/// Counted from the rendered rows rather than from the map's size: a user who marked an
/// issue that has since left the series must not push the count past the list.
pub(crate) fn summary<'a>(map: &ProgressMap, media: &str, keys: impl IntoIterator<Item = &'a str>) -> (usize, usize) {
    let mut total = 0;
    let mut done = 0;
    for key in keys {
        total += 1;
        if is_marked(map, media, key) {
            done += 1;
        }
    }
    (done, total)
}

/// The viewer's marks for one medium, or an empty map when signed out or Percy is down.
///
/// Never an error: progress is decoration on a public catalogue page, and a page that 500s
/// because the tracker is unreachable would be a worse trade than one that renders unmarked.
pub(crate) async fn fetch(app: &AppState, session: &Option<Session>, media: &str) -> ProgressMap {
    let (Some(percy), Some(session)) = (&app.percy, session.as_ref()) else {
        return ProgressMap::default();
    };
    percy
        .release_progress(&session.discord_user_id, Some(media))
        .await
        .map(|response| response.entries)
        .unwrap_or_default()
}

/// What both mutation forms post back. `status` is only on the mark form; the unmark form
/// identifies the row by the pair alone, which is all Percy's `DELETE` takes.
#[derive(Debug, Deserialize)]
pub struct ProgressForm {
    media: String,
    /// The card's `key`, echoed verbatim — never rebuilt from parts.
    release_key: String,
    #[serde(default)]
    status: Option<String>,
    #[serde(default)]
    redirect: Option<String>,
}

/// `POST /account/progress` — mark one item read/watched.
pub async fn progress_mark(
    State(app): State<AppState>,
    session: Session,
    headers: HeaderMap,
    Form(form): Form<ProgressForm>,
) -> Response {
    let target = safe_redirect(form.redirect.as_deref());
    let Some(percy) = &app.percy else {
        return back(&headers, false, "Percy is not configured", &target);
    };
    // The medium decides the verb, not the form: a hand-posted `status` from the wrong
    // medium is a 400 from Percy, and there is no reason to forward one.
    let status = form
        .status
        .as_deref()
        .filter(|s| *s == "skipped")
        .unwrap_or_else(|| status_for(&form.media));
    let result = percy
        .put_release_progress(
            &session.discord_user_id,
            &[ProgressEntry {
                media: &form.media,
                release_key: &form.release_key,
                status,
                updated_at: None,
            }],
        )
        .await;
    respond(&headers, result.map(|_| "Marked"), &target)
}

/// `GET /account/progress` — the viewer's whole set, both media, as JSON.
///
/// What `progress.js` re-reads after a write, so the page adopts the server's set rather
/// than trusting its own optimistic one. Percy is unreachable → two empty maps, never a
/// 500: the caller's fallback is to keep what it has queued.
pub async fn progress_list(State(app): State<AppState>, session: Session) -> Response {
    let entries = match &app.percy {
        Some(percy) => percy
            .release_progress(&session.discord_user_id, None)
            .await
            .map(|response| response.entries)
            .unwrap_or_default(),
        None => ProgressMap::default(),
    };
    axum::Json(serde_json::json!({ "entries": entries })).into_response()
}

/// `POST /account/progress/remove` — undo one mark.
pub async fn progress_unmark(
    State(app): State<AppState>,
    session: Session,
    headers: HeaderMap,
    Form(form): Form<ProgressForm>,
) -> Response {
    let target = safe_redirect(form.redirect.as_deref());
    let Some(percy) = &app.percy else {
        return back(&headers, false, "Percy is not configured", &target);
    };
    let result = percy
        .delete_release_progress(&session.discord_user_id, &form.media, &form.release_key)
        .await;
    respond(&headers, result.map(|_| "Unmarked"), &target)
}

/// A same-site path to bounce back to, or the fallback.
///
/// The check is the point: `redirect` arrives in a form field, so anything accepted here is
/// an open redirect. A path must start with a single `/` (`//evil.test` is a protocol-relative
/// URL, not a path) and carry no backslash or control character, which is how the same trick
/// is smuggled past naive checks.
fn safe_redirect(raw: Option<&str>) -> String {
    raw.map(str::trim)
        .filter(|path| {
            path.starts_with('/')
                && !path.starts_with("//")
                && !path.contains('\\')
                && !path.chars().any(|c| c.is_control())
        })
        .unwrap_or(FALLBACK)
        .to_string()
}

fn respond(headers: &HeaderMap, result: Result<&'static str, percy_client::PercyError>, target: &str) -> Response {
    match result {
        Ok(message) => back(headers, true, message, target),
        Err(error) => back(headers, false, &percy_message(&error), target),
    }
}

/// JSON for the AJAX submit, a redirect back to the page for a plain form post.
///
/// The failure redirect carries `?progress=failed` so a JS-off visitor is *told* the click
/// did nothing rather than being left to infer it from a control that did not move — the
/// same trade `/account/subscriptions` makes with its `?error=1`.
fn back(headers: &HeaderMap, ok: bool, message: &str, target: &str) -> Response {
    let target = if ok {
        target.to_string()
    } else {
        let separator = if target.contains('?') { '&' } else { '?' };
        format!("{target}{separator}progress=failed")
    };
    json_or_redirect(headers, ok, message, &target)
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use percy_client::ProgressMark;
    use std::collections::HashMap;

    /// A viewer's marks for one medium. Shared with the surfaces' render tests so every
    /// one of them asserts against the shape Percy actually sends, not a hand-built one.
    pub(crate) fn map(media: &str, keys: &[&str]) -> ProgressMap {
        let marks: HashMap<String, ProgressMark> = keys
            .iter()
            .map(|key| {
                (
                    (*key).to_string(),
                    ProgressMark {
                        status: status_for(media).to_string(),
                        updated_at: Some("2026-08-05T10:00:00".into()),
                    },
                )
            })
            .collect();
        // Percy always sends both media; the fixture does too, or a test would pass on a
        // shape the server never produces.
        HashMap::from([(media.to_string(), marks), (other(media).to_string(), HashMap::new())])
    }

    fn other(media: &str) -> &'static str {
        if media == "comic" {
            "title"
        } else {
            "comic"
        }
    }

    #[test]
    fn the_status_is_paired_with_its_medium_because_percy_rejects_a_mismatch() {
        assert_eq!(status_for("comic"), "read");
        assert_eq!(status_for("title"), "watched");
    }

    #[test]
    fn a_mark_is_only_a_mark_within_its_own_medium() {
        let marks = map("comic", &["MARVEL/asm-12"]);
        assert!(is_marked(&marks, "comic", "MARVEL/asm-12"));
        // Same key, other medium: `release_key` is only unique within one.
        assert!(!is_marked(&marks, "title", "MARVEL/asm-12"));
        assert!(!is_marked(&marks, "comic", "MARVEL/asm-13"));
        // A signed-out page has no map at all and must simply render unmarked.
        assert!(!is_marked(&ProgressMap::default(), "comic", "MARVEL/asm-12"));
    }

    #[test]
    fn the_summary_counts_the_rows_on_the_page_and_not_the_stored_set() {
        let marks = map("title", &["mcu/iron-man", "mcu/thor", "mcu/a-film-since-removed"]);
        assert_eq!(
            summary(&marks, "title", ["mcu/iron-man", "mcu/thor", "mcu/hulk"]),
            (2, 3)
        );
        // ...so a mark for something no longer listed can never push `done` past `total`.
        assert_eq!(summary(&marks, "title", ["mcu/iron-man"]), (1, 1));
        assert_eq!(summary(&ProgressMap::default(), "title", ["mcu/iron-man"]), (0, 1));
    }

    #[test]
    fn a_toggle_carries_the_verb_of_its_medium_and_the_key_verbatim() {
        let comic = toggle(&map("comic", &[]), true, "comic", "MARVEL/asm-12", "/comics");
        assert_eq!((comic.label.as_str(), comic.status.as_str()), ("Mark read", "read"));
        // Brand casing survives: Percy's key is `{brand}/{slug}` with the brand as stored.
        assert_eq!(comic.release_key, "MARVEL/asm-12");
        assert!(!comic.marked);

        let title = toggle(
            &map("title", &["mcu/iron-man"]),
            true,
            "title",
            "mcu/iron-man",
            "/watchlist/mcu",
        );
        assert_eq!(
            (title.label.as_str(), title.status.as_str()),
            ("Mark watched", "watched")
        );
        assert!(title.marked);
        assert_eq!(title.marked_label, "Watched");
    }

    #[test]
    fn a_signed_out_toggle_offers_a_sign_in_that_comes_back_to_the_same_page() {
        let out = toggle(
            &ProgressMap::default(),
            false,
            "title",
            "mcu/iron-man",
            "/watchlist/mcu/iron-man",
        );
        assert!(!out.signed_in);
        assert_eq!(out.sign_in_href, "/login?next=/watchlist/mcu/iron-man");
    }

    #[test]
    fn a_redirect_must_be_a_same_site_path() {
        assert_eq!(safe_redirect(Some("/watchlist/mcu")), "/watchlist/mcu");
        assert_eq!(
            safe_redirect(Some("/comics?brand=DC&page=2")),
            "/comics?brand=DC&page=2"
        );
        // Everything below is an open redirect if honoured.
        assert_eq!(safe_redirect(Some("//evil.test/")), FALLBACK);
        assert_eq!(safe_redirect(Some("https://evil.test/")), FALLBACK);
        assert_eq!(safe_redirect(Some("/\\evil.test")), FALLBACK);
        assert_eq!(safe_redirect(Some("/ok\nLocation: x")), FALLBACK);
        assert_eq!(safe_redirect(None), FALLBACK);
        assert_eq!(safe_redirect(Some("")), FALLBACK);
    }
}
