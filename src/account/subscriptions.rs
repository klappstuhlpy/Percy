//! `/account/subscriptions` — what the signed-in user follows across both media.
//!
//! Its own module rather than more of `userpages.rs` for the same reason `watchlist.rs`
//! and `comics.rs` are theirs: a feature area with view-model shaping and tests of its
//! own. `userpages.rs` is a 1:1 port of the monolith's user pages and stays that.
//!
//! Everything here is a proxied Percy call (D4 — the BFF never touches Percy's
//! database), and every control is a plain `<form>` submit, so the page works with JS
//! off (D9); `static/js/subscriptions.js` only debounces the picker's search.
//!
//! The load-bearing rule: `subject_value` is an **opaque key**. It goes into a hidden
//! field exactly as Percy handed it over and comes back untouched. Posting the *label*
//! instead would create a subscription the fan-out can never match.

use axum::{
    extract::{Query, State},
    http::HeaderMap,
    response::{IntoResponse, Response},
    Form, Json,
};
use percy_client::{NewSubscription, Subject, Subscription, SubscriptionPatch};
use serde::Deserialize;

use crate::{
    errors::percy_message,
    forms::json_or_redirect,
    session::Session,
    templates::{SubjectRow, SubscriptionGroup, SubscriptionRow, SubscriptionsTemplate},
    view::Viewer,
    AppState,
};

/// Where every mutation bounces back to.
const PAGE: &str = "/account/subscriptions";

/// The media, in the order the page shows them. Percy's `media` values on the left.
const MEDIA: [(&str, &str); 2] = [("comic", "Comics"), ("title", "Film & TV")];

/// How many picker suggestions a page or a search shows.
const PICKER_LIMIT: u32 = 12;

/// Query string of the page: the picker's search term, plus the `error` flag a failed
/// mutation redirects back with (the dashboard has no flash store — see `forms.rs`).
#[derive(Debug, Default, Deserialize)]
pub struct PageQuery {
    #[serde(default)]
    q: Option<String>,
    #[serde(default)]
    error: Option<String>,
}

/// The identifying triple every mutation form posts back.
#[derive(Debug, Deserialize)]
pub struct SubscriptionForm {
    media: String,
    subject_type: String,
    /// Percy's opaque key, echoed verbatim.
    subject_value: String,
    #[serde(default)]
    subject_label: Option<String>,
    #[serde(default)]
    streams: Option<String>,
    #[serde(default)]
    delivery: Option<String>,
}

/// `GET /account/subscriptions` — the subscriptions the user has, and the picker.
pub async fn subscriptions_page(
    State(app): State<AppState>,
    session: Session,
    Query(query): Query<PageQuery>,
) -> Response {
    let search = query.q.unwrap_or_default();
    let viewer = Some(Viewer::from_session(&session));

    let Some(percy) = &app.percy else {
        return unavailable(viewer, search);
    };

    // One round trip each; the picker's popular set does not depend on the follows, so
    // there is no reason to serialise them.
    let (subscriptions, subjects) = tokio::join!(
        percy.list_subscriptions(&session.discord_user_id, None),
        percy.search_subjects(&search, None, Some(PICKER_LIMIT)),
    );

    let Ok(subscriptions) = subscriptions else {
        return unavailable(viewer, search);
    };
    let followed = subscriptions.subscriptions.clone();
    let subjects = subjects.map(|response| response.subjects).unwrap_or_default();

    let groups: Vec<SubscriptionGroup> = MEDIA
        .iter()
        .map(|(media, label)| SubscriptionGroup {
            media: (*media).to_string(),
            label: (*label).to_string(),
            rows: subscriptions
                .subscriptions
                .iter()
                .filter(|row| row.media == *media)
                .map(subscription_row)
                .collect(),
        })
        .filter(|group| !group.rows.is_empty())
        .collect();

    SubscriptionsTemplate {
        account: viewer,
        total: subscriptions.subscriptions.len(),
        groups,
        query: search,
        subjects: subjects.iter().map(|s| subject_row(s, &followed)).collect(),
        unavailable: false,
        errored: query.error.is_some(),
    }
    .into_response()
}

/// `POST /account/subscriptions` — follow a subject from the picker.
pub async fn subscription_create(
    State(app): State<AppState>,
    session: Session,
    headers: HeaderMap,
    Form(form): Form<SubscriptionForm>,
) -> Response {
    let Some(percy) = &app.percy else {
        return back(&headers, false, "Percy is not configured");
    };
    let result = percy
        .put_subscription(
            &session.discord_user_id,
            &NewSubscription {
                media: &form.media,
                subject_type: &form.subject_type,
                subject_value: &form.subject_value,
                subject_label: form.subject_label.as_deref().filter(|l| !l.is_empty()),
                streams: form.streams.as_deref(),
                delivery: form.delivery.as_deref(),
                guild_id: None,
            },
        )
        .await;
    respond(&headers, result.map(|_| "Following"))
}

/// `POST /account/subscriptions/update` — change one follow's stream or delivery mode.
///
/// A `404` from Percy means the user no longer follows it (removed in another tab), and
/// is deliberately *not* retried as a create: a stale toggle must not resurrect it.
pub async fn subscription_update(
    State(app): State<AppState>,
    session: Session,
    headers: HeaderMap,
    Form(form): Form<SubscriptionForm>,
) -> Response {
    let Some(percy) = &app.percy else {
        return back(&headers, false, "Percy is not configured");
    };
    let result = percy
        .patch_subscription(
            &session.discord_user_id,
            &SubscriptionPatch {
                media: &form.media,
                subject_type: &form.subject_type,
                subject_value: &form.subject_value,
                streams: form.streams.as_deref(),
                delivery: form.delivery.as_deref(),
            },
        )
        .await;
    respond(&headers, result.map(|_| "Updated"))
}

/// `POST /account/subscriptions/remove` — unfollow.
pub async fn subscription_delete(
    State(app): State<AppState>,
    session: Session,
    headers: HeaderMap,
    Form(form): Form<SubscriptionForm>,
) -> Response {
    let Some(percy) = &app.percy else {
        return back(&headers, false, "Percy is not configured");
    };
    let result = percy
        .delete_subscription(
            &session.discord_user_id,
            &form.media,
            &form.subject_type,
            &form.subject_value,
        )
        .await;
    respond(&headers, result.map(|_| "Removed"))
}

/// `GET /account/subscriptions/search` — the picker's JSON feed for the debounced
/// search. The page itself renders without it; this only spares a full reload.
pub async fn subscription_search(
    State(app): State<AppState>,
    session: Session,
    Query(query): Query<PageQuery>,
) -> Response {
    let Some(percy) = &app.percy else {
        return Json(serde_json::json!({"subjects": []})).into_response();
    };
    let search = query.q.unwrap_or_default();

    let (subjects, subscriptions) = tokio::join!(
        percy.search_subjects(&search, None, Some(PICKER_LIMIT)),
        percy.list_subscriptions(&session.discord_user_id, None),
    );
    let followed = subscriptions.map(|r| r.subscriptions).unwrap_or_default();
    let subjects: Vec<serde_json::Value> = subjects
        .map(|r| r.subjects)
        .unwrap_or_default()
        .iter()
        .map(|subject| {
            let row = subject_row(subject, &followed);
            serde_json::json!({
                "media": row.media,
                "subject_type": row.subject_type,
                "subject_value": row.subject_value,
                "label": row.label,
                "hint": row.hint,
                "followed": row.followed,
            })
        })
        .collect();

    Json(serde_json::json!({ "subjects": subjects })).into_response()
}

/// The page with nothing on it, for a Percy that is unreachable or unconfigured.
fn unavailable(account: Option<Viewer>, query: String) -> Response {
    SubscriptionsTemplate {
        account,
        groups: Vec::new(),
        total: 0,
        query,
        subjects: Vec::new(),
        unavailable: true,
        errored: false,
    }
    .into_response()
}

/// Turns a Percy result into the response the caller expects.
fn respond(headers: &HeaderMap, result: Result<&'static str, percy_client::PercyError>) -> Response {
    match result {
        Ok(message) => back(headers, true, message),
        Err(error) => back(headers, false, &percy_message(&error)),
    }
}

/// A JSON body for an AJAX submit, a redirect back to the page for a plain form post.
///
/// The failure redirect carries `?error=1` so a plain-HTML user is told the click did
/// nothing, rather than being left to infer it from a control that did not move.
fn back(headers: &HeaderMap, ok: bool, message: &str) -> Response {
    let target = if ok {
        PAGE.to_string()
    } else {
        format!("{PAGE}?error=1")
    };
    json_or_redirect(headers, ok, message, &target)
}

fn subscription_row(subscription: &Subscription) -> SubscriptionRow {
    SubscriptionRow {
        media: subscription.media.clone(),
        type_label: type_label(&subscription.subject_type).to_string(),
        subject_type: subscription.subject_type.clone(),
        // The label is nullable and Percy falls back to the key it was subscribed with,
        // so an unlabelled follow shows its key rather than an empty row.
        label: subscription
            .subject_label
            .clone()
            .filter(|label| !label.is_empty())
            .unwrap_or_else(|| subscription.subject_value.clone()),
        subject_value: subscription.subject_value.clone(),
        streams: subscription.streams.clone(),
        muted: subscription.delivery == "off",
        delivery: subscription.delivery.clone(),
    }
}

fn subject_row(subject: &Subject, followed: &[Subscription]) -> SubjectRow {
    SubjectRow {
        followed: followed.iter().any(|row| {
            row.media == subject.media
                && row.subject_type == subject.subject_type
                && row.subject_value == subject.subject_value
        }),
        media: subject.media.clone(),
        subject_type: subject.subject_type.clone(),
        subject_value: subject.subject_value.clone(),
        label: subject.label.clone(),
        hint: subject.hint.clone(),
    }
}

/// The chip on a row. Percy's subject types are lowercase keys; these are the words.
fn type_label(subject_type: &str) -> &'static str {
    match subject_type {
        "brand" => "Publisher",
        "series" => "Series",
        "creator" => "Creator",
        "character" => "Character",
        "universe" => "Universe",
        "franchise" => "Franchise",
        "person" => "Person",
        _ => "Subject",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use askama::Template;

    fn subscription(media: &str, subject_type: &str, value: &str, label: Option<&str>) -> Subscription {
        Subscription {
            media: media.to_string(),
            subject_type: subject_type.to_string(),
            subject_value: value.to_string(),
            subject_label: label.map(str::to_string),
            streams: "releases".into(),
            delivery: "dm".into(),
            ..Subscription::default()
        }
    }

    #[test]
    fn an_unlabelled_follow_shows_its_key_rather_than_an_empty_row() {
        let row = subscription_row(&subscription("comic", "brand", "marvel", None));
        assert_eq!(row.label, "marvel");
        assert_eq!(row.type_label, "Publisher");

        let row = subscription_row(&subscription("comic", "series", "asm", Some("")));
        assert_eq!(row.label, "asm", "an empty label is as good as none");
    }

    #[test]
    fn a_picker_row_is_followed_only_on_the_whole_triple() {
        let followed = vec![subscription("title", "person", "pedro-pascal", Some("Pedro Pascal"))];
        let subject = |media: &str, subject_type: &str, value: &str| Subject {
            media: media.into(),
            subject_type: subject_type.into(),
            subject_value: value.into(),
            label: "Pedro Pascal".into(),
            hint: "person · 9 credits".into(),
        };

        assert!(subject_row(&subject("title", "person", "pedro-pascal"), &followed).followed);
        // Same key, different type/medium: a different subscription entirely.
        assert!(!subject_row(&subject("title", "character", "pedro-pascal"), &followed).followed);
        assert!(!subject_row(&subject("comic", "person", "pedro-pascal"), &followed).followed);
    }

    fn render(groups: Vec<SubscriptionGroup>, subjects: Vec<SubjectRow>, total: usize) -> String {
        SubscriptionsTemplate {
            account: None,
            groups,
            total,
            query: String::new(),
            subjects,
            unavailable: false,
            errored: false,
        }
        .render()
        .expect("the subscriptions template renders")
    }

    #[test]
    fn a_populated_page_posts_the_key_and_never_the_label() {
        let html = render(
            vec![SubscriptionGroup {
                media: "title".into(),
                label: "Film & TV".into(),
                rows: vec![subscription_row(&subscription(
                    "title",
                    "person",
                    "pedro-pascal",
                    Some("Pedro Pascal"),
                ))],
            }],
            Vec::new(),
            1,
        );

        assert!(html.contains("Pedro Pascal"), "the label is what a human reads");
        assert!(
            html.contains(r#"name="subject_value" value="pedro-pascal""#),
            "the opaque key is what goes back to Percy"
        );
        // The three stream buttons and the delivery toggle are plain submits, so the
        // page works with JS off.
        assert!(html.contains(r#"name="streams" value="both""#));
        assert!(html.contains(r#"name="delivery" value="off""#));
        assert!(html.contains(r#"formaction="/account/subscriptions/remove""#));
    }

    #[test]
    fn an_empty_page_points_at_the_two_catalogues() {
        let html = render(Vec::new(), Vec::new(), 0);
        assert!(html.contains("href=\"/comics\""));
        assert!(html.contains("href=\"/watchlist\""));
    }

    #[test]
    fn an_already_followed_subject_cannot_be_subscribed_twice_from_the_picker() {
        let html = render(
            Vec::new(),
            vec![SubjectRow {
                media: "comic".into(),
                subject_type: "series".into(),
                subject_value: "amazing-spider-man".into(),
                label: "Amazing Spider-Man".into(),
                hint: "Marvel · 12 issues".into(),
                followed: true,
            }],
            1,
        );
        assert!(html.contains("Following"));
        assert!(!html.contains(">Subscribe<"));
    }
}
