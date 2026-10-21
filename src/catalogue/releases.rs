//! `/releases` — the front door: what just landed and what is imminent, across both media.
//!
//! Its own module rather than more of `subscriptions.rs`: that page is the signed-in
//! account surface and is all mutations, while this one is a *public* catalogue page with
//! no session requirement and no writes — the same shape as `comics.rs` and `watchlist.rs`,
//! which is where it belongs. Everything is server-rendered from a single proxied Percy
//! call (D4, D9), so the page is indexable and works with JS off; the `days` control is a
//! plain `GET` form.
//!
//! **The window is asymmetric and the page says so.** Percy's `days` is a half-width, but
//! only the film/TV half fills both sides of it: comics are matched backwards, on when Percy
//! first saw them, so they only ever occupy `[start, today]`. Presenting one "this week"
//! heading over both lists would promise comics the response cannot contain, so each section
//! states its own span.
//!
//! Signed in, the viewer's Discord id goes along and Percy fills `followed` — a *subset* of
//! the two lists, never a wider query. It leads the page when it is non-empty and is absent
//! entirely otherwise, because an empty "Following" box is worse than no box.

use std::collections::BTreeMap;

use axum::{
    extract::{Query, State},
    response::{IntoResponse, Response},
};
use percy_client::{CardSubject, ProgressMap, ReleaseCard};
use serde::Deserialize;

use crate::{
    session::Session,
    templates::{ChipRow, MonthGroup, ReleaseCardRow, ReleasesTemplate},
    view::Viewer,
    AppState,
};

/// TMDB image CDN — `cover_url` is a relative `poster_path` for titles, and the renderer
/// owns the prefix (same rule, same bucket, as `watchlist.rs`).
const TMDB_IMAGE_BASE: &str = "https://image.tmdb.org/t/p";

/// The windows the `days` control offers. Percy caps `days` at 30.
const DAY_CHOICES: [u32; 3] = [7, 14, 30];

/// Default half-width, matching Percy's own.
const DEFAULT_DAYS: u32 = 7;

/// Cards per list. The grid is four wide at desktop width, so this is six clean rows.
const LIMIT: u32 = 24;

/// Query string of the page: the window width and the sort order.
#[derive(Debug, Default, Deserialize)]
pub struct ReleasesQuery {
    #[serde(default)]
    days: Option<u32>,
    #[serde(default)]
    sort: Option<String>,
}

/// The order the three lists render in.
///
/// Sorting happens here, not on Percy: `GET /releases/overview` takes no `sort` and its
/// response sits behind a TTL cache, so a Percy-side parameter would multiply the cache key
/// for no reason — the cards already in the response are enough to reorder client-side.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Sort {
    /// Release date descending, unknown dates last. The default.
    Date,
    /// Release date ascending, unknown dates last.
    DateAsc,
    /// Display name, case-insensitive ascending.
    Name,
}

impl Sort {
    /// The offered value nearest what was asked for; unrecognised or absent falls back to
    /// [`Sort::Date`], same as an out-of-range `days` falls back to [`DEFAULT_DAYS`].
    fn parse(value: Option<&str>) -> Self {
        match value {
            Some("date_asc") => Sort::DateAsc,
            Some("name") => Sort::Name,
            _ => Sort::Date,
        }
    }

    /// The `value` attribute this variant's button carries, and what a hidden field
    /// preserving the current sort must echo back.
    fn as_str(self) -> &'static str {
        match self {
            Sort::Date => "date",
            Sort::DateAsc => "date_asc",
            Sort::Name => "name",
        }
    }
}

/// Order `cards` in place per `sort`. Stable (`sort_by`, never `sort_unstable_by`) so cards
/// Percy returned in the same order stay in that order when their sort keys tie — otherwise
/// the same request could render the list differently on a second load.
///
/// Unknown release dates land last in *both* date directions, which a bare `Option`
/// comparison does not give you: `None` sorts first ascending under `Ord`'s derived rule, so
/// the ascending branch inverts the comparison and then reverses it back rather than just
/// flipping `a.cmp(b)`.
fn sort_cards(cards: &mut [ReleaseCard], sort: Sort) {
    match sort {
        Sort::Date => cards.sort_by(|a, b| b.release_date.cmp(&a.release_date)),
        Sort::DateAsc => cards.sort_by(|a, b| match (&a.release_date, &b.release_date) {
            (None, None) => std::cmp::Ordering::Equal,
            (None, Some(_)) => std::cmp::Ordering::Greater,
            (Some(_), None) => std::cmp::Ordering::Less,
            (Some(a), Some(b)) => a.cmp(b),
        }),
        Sort::Name => cards.sort_by_key(|a| a.name.to_lowercase()),
    }
}

/// The terminal bucket for a card with no parseable release date — an announced-but-
/// undated title, not a dropped one.
const UNDATED_LABEL: &str = "Date to be announced";

const MONTH_NAMES: [&str; 12] = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
];

/// The sortable `"YYYY-MM"` key and its display label ("August 2026") for an ISO
/// `YYYY-MM-DD` release date. `None` if `date` doesn't parse that way — defensive, since
/// every date Percy hands back does; an unparseable one falls into the same terminal
/// bucket as no date at all rather than panicking the page.
fn month_key(date: &str) -> Option<(String, String)> {
    let year = date.get(0..4)?;
    let month: usize = date.get(5..7)?.parse().ok()?;
    let name = MONTH_NAMES.get(month.checked_sub(1)?)?;
    Some((format!("{year}-{month:02}"), format!("{name} {year}")))
}

/// Bucket `cards` into month groups for the roadmap.
///
/// Months come out newest-first unless `oldest_first` (the "Oldest" sort control is the
/// only thing that flips it). A card with no parseable release date lands in the
/// terminal [`UNDATED_LABEL`] bucket, which is **always last** regardless of direction —
/// an unreleased film isn't "older" than anything, it just has no date yet. A month with
/// no cards in it is never constructed, so "omit an empty month" needs no separate check
/// downstream: there is nothing to omit.
///
/// Grouping never reorders cards *within* a bucket — they arrive already ordered by
/// `sort_cards` and keep that relative order, which is what lets `Sort::Name` still read
/// alphabetically inside each month instead of being re-shuffled here.
fn group_by_month(cards: Vec<ReleaseCardRow>, oldest_first: bool) -> Vec<MonthGroup> {
    let mut dated: BTreeMap<String, MonthGroup> = BTreeMap::new();
    let mut undated = Vec::new();

    for card in cards {
        match card.release_date.as_deref().and_then(month_key) {
            Some((key, label)) => {
                dated
                    .entry(key)
                    .or_insert_with(|| MonthGroup {
                        label,
                        cards: Vec::new(),
                    })
                    .cards
                    .push(card);
            }
            None => undated.push(card),
        }
    }

    // BTreeMap already yields ascending (oldest-first) key order; newest-first is the
    // default, so that's the branch that has to do work.
    let mut groups: Vec<MonthGroup> = dated.into_values().collect();
    if !oldest_first {
        groups.reverse();
    }
    if !undated.is_empty() {
        groups.push(MonthGroup {
            label: UNDATED_LABEL.to_string(),
            cards: undated,
        });
    }
    groups
}

/// `GET /releases` — the overview.
pub async fn releases_page(
    State(app): State<AppState>,
    session: Option<Session>,
    Query(query): Query<ReleasesQuery>,
) -> Response {
    let days = normalise_days(query.days);
    let sort = Sort::parse(query.sort.as_deref());
    let account = session.as_ref().map(Viewer::from_session);
    let discord_id = session.as_ref().map(|s| s.discord_user_id.clone());

    // Two independent calls, so they go out together: the ticker must not add a round trip
    // to the page it decorates, and it must not take the page down when it fails.
    let (fetched, ticker) = match &app.percy {
        Some(percy) => {
            let (overview, ticker) = tokio::join!(
                percy.releases_overview(Some(days), discord_id.as_deref(), Some(LIMIT)),
                crate::news::ticker(&app, discord_id.as_deref())
            );
            (overview.ok(), ticker)
        }
        None => (None, Vec::new()),
    };
    // An empty page has two causes that read identically and are not the same thing: a
    // catalogue with nothing in this window, and a Percy that never answered. The first
    // wants "come back later", the second wants "try again".
    let unavailable = fetched.is_none();
    let mut overview = fetched.unwrap_or_default();

    // Applied to all three lists, so the control means one thing everywhere rather than
    // ordering "what you follow" differently from the catalogue underneath it.
    sort_cards(&mut overview.followed, sort);
    sort_cards(&mut overview.comics, sort);
    sort_cards(&mut overview.titles, sort);

    // Progress rides *beside* the cards in the same response — no second call, and no
    // per-user field on a card, which is what keeps the card half shareable across
    // visitors. Signed out it is two empty maps, so the row builder needs no branch.
    let signed_in = account.is_some();
    let progress = overview.progress;
    let row = |card: &ReleaseCard| card_row(card, &progress, signed_in);

    // The "Oldest" control is the only thing that reverses month order; "Newest" and
    // "Name" both read newest-month-first, since a roadmap defaults to what's coming up.
    let oldest_first = sort == Sort::DateAsc;
    let group = |cards: &[ReleaseCard]| group_by_month(cards.iter().map(row).collect(), oldest_first);

    ReleasesTemplate {
        days,
        sort: sort.as_str(),
        window_start: overview.window.start,
        window_end: overview.window.end,
        followed: group(&overview.followed),
        comics: group(&overview.comics),
        titles: group(&overview.titles),
        ticker,
        signed_in,
        account,
        unavailable,
        comic_copyright: String::new(),
    }
    .into_response()
}

/// The offered window nearest what was asked for, defaulting when absent or nonsense.
///
/// The control only offers three values, so an arbitrary `?days=` is snapped onto one of
/// them rather than passed through: otherwise the page would render a window no button on
/// it can reproduce, and every button would look unselected.
fn normalise_days(days: Option<u32>) -> u32 {
    match days {
        Some(days) if DAY_CHOICES.contains(&days) => days,
        _ => DEFAULT_DAYS,
    }
}

/// One card, with its art resolved and its link pointed at this site.
///
/// `card.key` goes into the toggle verbatim: it *is* the `release_key` Percy stores
/// progress under, so nothing here composes one from the brand and slug.
fn card_row(card: &ReleaseCard, progress: &ProgressMap, signed_in: bool) -> ReleaseCardRow {
    let title = card.media == "title";
    ReleaseCardRow {
        toggle: crate::progress::toggle(progress, signed_in, &card.media, &card.key, "/releases"),
        media_label: if title { "Film & TV" } else { "Comic" }.to_string(),
        href: card_href(&card.media, &card.key),
        name: card.name.clone(),
        subtitle: card.subtitle.clone(),
        release_date: card.release_date.clone(),
        // Comics carry an absolute cover; titles carry TMDB's relative `poster_path`.
        cover_url: card.cover_url.as_deref().map(|url| {
            if title {
                format!("{TMDB_IMAGE_BASE}/w342{url}")
            } else {
                url.to_string()
            }
        }),
        chips: card.subjects.iter().map(|subject| chip(&card.media, subject)).collect(),
        media: card.media.clone(),
    }
}

/// Where a card links on this site — the single place that answers it.
///
/// Both keys are already `{scope}/{slug}` and both detail routes take exactly that, so a
/// card's href is its key under the right prefix. The comic's own `url` (the source
/// listing) is deliberately not used: it belongs in the attribution, not on the card.
fn card_href(media: &str, key: &str) -> String {
    if media == "title" {
        format!("/watchlist/{key}")
    } else {
        format!("/comics/{key}")
    }
}

/// Where a subject key's catalogue page lives on this site, or `None` when it has none.
///
/// Shared with `news.rs`, which tags its stories with the same keys: "where does a series
/// chip go" must have one answer, or the two surfaces drift.
///
/// `subject_value` is Percy's opaque key and is normalised (casefolded), which matters for
/// exactly one of these: the comic browse filter matches `brand` **exactly** against the
/// stored uppercase column, so a `?brand=marvel` link would quietly return nothing.
/// Creators, characters and franchises have no page of their own.
pub(crate) fn subject_href(media: &str, subject_type: &str, subject_value: &str) -> Option<String> {
    match (media, subject_type) {
        ("comic", "brand") => Some(format!("/comics?brand={}", subject_value.to_uppercase())),
        ("comic", "series") => Some(format!("/comics/series/{subject_value}")),
        ("title", "universe") => Some(format!("/watchlist/{subject_value}")),
        _ => None,
    }
}

/// One subject chip, linked when a catalogue page for that subject exists.
///
/// A subject with no page renders as plain text, because a chip that looks like a link and
/// goes nowhere is worse than a label.
fn chip(media: &str, subject: &CardSubject) -> ChipRow {
    let value = subject.subject_value.as_str();
    let href = subject_href(media, &subject.subject_type, value);
    ChipRow {
        label: if subject.label.is_empty() {
            value.to_string()
        } else {
            subject.label.clone()
        },
        href,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::templates::TickerItem;
    use askama::Template;

    /// A signed-out row. Most of these assertions are about the card half — art, links,
    /// chips — which is identical either way; the toggle's own behaviour is
    /// `progress.rs`'s business and is tested there.
    fn row(card: &ReleaseCard) -> ReleaseCardRow {
        card_row(card, &ProgressMap::default(), false)
    }

    fn subject(subject_type: &str, value: &str, label: &str) -> CardSubject {
        CardSubject {
            subject_type: subject_type.into(),
            subject_value: value.into(),
            label: label.into(),
        }
    }

    fn comic() -> ReleaseCard {
        ReleaseCard {
            media: "comic".into(),
            key: "MARVEL/amazing-spider-man-1".into(),
            name: "Amazing Spider-Man #1".into(),
            subtitle: Some("Amazing Spider-Man #1".into()),
            release_date: Some("2026-08-01".into()),
            cover_url: Some("https://cdn.example/asm1.jpg".into()),
            url: Some("https://leagueofcomicgeeks.com/comic/1".into()),
            subjects: vec![
                subject("brand", "marvel", "Marvel"),
                subject("series", "amazing-spider-man", "Amazing Spider-Man"),
            ],
        }
    }

    fn title() -> ReleaseCard {
        ReleaseCard {
            media: "title".into(),
            key: "mcu/avengers-doomsday".into(),
            name: "Avengers: Doomsday".into(),
            subtitle: Some("Marvel Cinematic Universe · Avengers".into()),
            release_date: Some("2026-08-20".into()),
            cover_url: Some("/poster.jpg".into()),
            url: None,
            subjects: vec![
                subject("universe", "mcu", "Marvel Cinematic Universe"),
                subject("franchise", "avengers", "Avengers"),
            ],
        }
    }

    /// A minimal card for the sort tests — only `name` and `release_date` matter to
    /// `sort_cards`, so nothing else here is populated.
    fn card_named(name: &str, release_date: Option<&str>) -> ReleaseCard {
        ReleaseCard {
            media: "title".into(),
            key: name.to_lowercase(),
            name: name.into(),
            subtitle: None,
            release_date: release_date.map(Into::into),
            cover_url: None,
            url: None,
            subjects: Vec::new(),
        }
    }

    fn names(cards: &[ReleaseCard]) -> Vec<&str> {
        cards.iter().map(|c| c.name.as_str()).collect()
    }

    fn row_names(cards: &[ReleaseCardRow]) -> Vec<&str> {
        cards.iter().map(|c| c.name.as_str()).collect()
    }

    #[test]
    fn sort_parses_the_three_values_and_falls_back_to_date_on_anything_else() {
        assert_eq!(Sort::parse(Some("date")), Sort::Date);
        assert_eq!(Sort::parse(Some("date_asc")), Sort::DateAsc);
        assert_eq!(Sort::parse(Some("name")), Sort::Name);
        assert_eq!(Sort::parse(Some("bogus")), Sort::Date);
        assert_eq!(Sort::parse(None), Sort::Date);
    }

    #[test]
    fn date_sort_is_descending_with_unknown_dates_last() {
        let mut cards = vec![
            card_named("Undated", None),
            card_named("Early", Some("2026-08-01")),
            card_named("Late", Some("2026-08-20")),
            card_named("Middle", Some("2026-08-10")),
        ];
        sort_cards(&mut cards, Sort::Date);
        assert_eq!(names(&cards), ["Late", "Middle", "Early", "Undated"]);
    }

    #[test]
    fn date_asc_sort_is_ascending_with_unknown_dates_still_last() {
        // Same set as the descending case — proves it isn't just a mirror-image bug where
        // `None` would land first under the derived `Option<String>` ordering.
        let mut cards = vec![
            card_named("Undated", None),
            card_named("Early", Some("2026-08-01")),
            card_named("Late", Some("2026-08-20")),
            card_named("Middle", Some("2026-08-10")),
        ];
        sort_cards(&mut cards, Sort::DateAsc);
        assert_eq!(names(&cards), ["Early", "Middle", "Late", "Undated"]);
    }

    #[test]
    fn name_sort_is_case_insensitive_ascending() {
        // "Banana" vs "apple": case-*sensitive* ordering would put "Banana" first
        // ('B' = 66 < 'a' = 97), so this pair only comes out "apple" first if the
        // comparison actually folds case — unlike zebra/Avengers/bat-man, where the
        // case-sensitive order happens to match the case-insensitive one and the
        // assertion would pass even with `.to_lowercase()` deleted from `Sort::Name`.
        let mut cards = vec![
            card_named("Banana", Some("2026-08-01")),
            card_named("apple", Some("2026-08-20")),
            card_named("cherry", None),
        ];
        sort_cards(&mut cards, Sort::Name);
        assert_eq!(names(&cards), ["apple", "Banana", "cherry"]);
    }

    #[test]
    fn sort_is_stable_so_equal_keys_keep_percys_order() {
        // Two undated cards: if the comparator ever regressed to `sort_unstable_by`, this
        // couldn't tell — the point of `sort_by` is that a tie leaves relative order alone,
        // which is only observable with a stable sort and equal keys.
        let mut cards = vec![
            card_named("First", None),
            card_named("Second", None),
            card_named("Dated", Some("2026-08-01")),
        ];
        sort_cards(&mut cards, Sort::Date);
        assert_eq!(names(&cards), ["Dated", "First", "Second"]);

        let mut cards = vec![card_named("First", None), card_named("Second", None)];
        sort_cards(&mut cards, Sort::DateAsc);
        assert_eq!(names(&cards), ["First", "Second"]);
    }

    #[test]
    fn a_days_outside_the_offered_set_snaps_back_to_the_default() {
        assert_eq!(normalise_days(Some(30)), 30);
        assert_eq!(normalise_days(Some(9)), DEFAULT_DAYS, "no button could reproduce 9");
        assert_eq!(normalise_days(Some(0)), DEFAULT_DAYS);
        assert_eq!(normalise_days(None), DEFAULT_DAYS);
    }

    #[test]
    fn an_undated_card_lands_in_the_terminal_bucket_not_first() {
        // A naive `Option<String>` sort key would put `None` *before* `Some` (the derived
        // `Ord` for `Option` sorts `None` least), so a grouping bug that reused that
        // ordering directly would land "Undated" first rather than last. Two distinct
        // months are also here so the bucketing itself is doing real work, not just
        // separating one dated card from one undated one.
        let cards = vec![
            row(&card_named("Undated", None)),
            row(&card_named("September", Some("2026-09-05"))),
            row(&card_named("July", Some("2026-07-10"))),
        ];
        let groups = group_by_month(cards, false);

        assert_eq!(groups.len(), 3, "two dated months plus the terminal bucket");
        assert_eq!(groups[0].label, "September 2026");
        assert_eq!(groups[1].label, "July 2026");
        assert_eq!(groups[2].label, UNDATED_LABEL);
        assert_eq!(row_names(&groups[2].cards), ["Undated"]);
    }

    #[test]
    fn cards_bucket_by_month_newest_first_and_keep_their_order_within_a_bucket() {
        let cards = vec![
            row(&card_named("Late July", Some("2026-07-20"))),
            row(&card_named("Early July", Some("2026-07-01"))),
            row(&card_named("August", Some("2026-08-05"))),
        ];
        let groups = group_by_month(cards, false);

        assert_eq!(groups.len(), 2, "the two July cards share one bucket, not two");
        assert_eq!(groups[0].label, "August 2026");
        assert_eq!(groups[1].label, "July 2026");
        // Grouping must not re-sort within a bucket — the cards arrive pre-sorted by
        // `sort_cards` and keep that order verbatim.
        assert_eq!(row_names(&groups[1].cards), ["Late July", "Early July"]);
    }

    #[test]
    fn the_oldest_first_direction_reverses_month_order_but_never_the_terminal_bucket() {
        let cards = vec![
            row(&card_named("July", Some("2026-07-01"))),
            row(&card_named("August", Some("2026-08-01"))),
            row(&card_named("TBA", None)),
        ];
        let groups = group_by_month(cards, true);

        assert_eq!(groups[0].label, "July 2026");
        assert_eq!(groups[1].label, "August 2026");
        assert_eq!(
            groups[2].label, UNDATED_LABEL,
            "the terminal bucket stays last in either direction"
        );
    }

    #[test]
    fn a_comic_keeps_its_absolute_cover_and_a_title_gets_the_tmdb_prefix() {
        assert_eq!(row(&comic()).cover_url.unwrap(), "https://cdn.example/asm1.jpg");
        assert_eq!(
            row(&title()).cover_url.unwrap(),
            "https://image.tmdb.org/t/p/w342/poster.jpg"
        );
    }

    #[test]
    fn a_card_links_on_site_and_never_at_the_source_listing() {
        assert_eq!(card_href("comic", "MARVEL/asm-1"), "/comics/MARVEL/asm-1");
        // A title's key is `{universe}/{slug}`, which is the detail route verbatim — the
        // card lands on the title, not on its universe's whole watch order.
        assert_eq!(
            card_href("title", "mcu/avengers-doomsday"),
            "/watchlist/mcu/avengers-doomsday"
        );
        assert!(!row(&comic()).href.contains("leagueofcomicgeeks"));
    }

    #[test]
    fn a_brand_chip_is_uppercased_because_the_browse_filter_matches_exactly() {
        // Percy normalises subject values to lowercase; `comic_releases.brand` is stored
        // uppercase and filtered with `=`, so the lowercase key would match nothing.
        let chips = row(&comic()).chips;
        assert_eq!(chips[0].href.as_deref(), Some("/comics?brand=MARVEL"));
        assert_eq!(chips[0].label, "Marvel");
        assert_eq!(chips[1].href.as_deref(), Some("/comics/series/amazing-spider-man"));
    }

    #[test]
    fn a_chip_with_nowhere_to_go_renders_as_text() {
        let chips = row(&title()).chips;
        assert_eq!(chips[0].href.as_deref(), Some("/watchlist/mcu"));
        assert_eq!(chips[1].href, None, "no franchise page exists");
        assert_eq!(
            chip("comic", &subject("creator", "jonathan hickman", "")).label,
            "jonathan hickman"
        );
    }

    fn render(template: ReleasesTemplate) -> String {
        template.render().expect("the releases template renders")
    }

    /// Wraps a flat list of cards into month groups through the real grouping function,
    /// so template tests exercise the same path production does rather than a stubbed
    /// shape — grouping-specific behaviour has its own dedicated tests below.
    fn grouped(cards: Vec<ReleaseCard>) -> Vec<MonthGroup> {
        group_by_month(cards.iter().map(row).collect(), false)
    }

    fn page(followed: Vec<ReleaseCard>, unavailable: bool, signed_in: bool) -> ReleasesTemplate {
        ReleasesTemplate {
            account: None,
            days: DEFAULT_DAYS,
            sort: Sort::Date.as_str(),
            window_start: "2026-07-28".into(),
            window_end: "2026-08-11".into(),
            followed: grouped(followed),
            comics: if unavailable {
                Vec::new()
            } else {
                grouped(vec![comic()])
            },
            titles: if unavailable {
                Vec::new()
            } else {
                grouped(vec![title()])
            },
            ticker: Vec::new(),
            signed_in,
            unavailable,
            comic_copyright: String::new(),
        }
    }

    fn headline(headline: &str) -> TickerItem {
        TickerItem {
            url: "https://example.test/story".into(),
            headline: headline.into(),
            source_name: "Marvel.com".into(),
        }
    }

    #[test]
    fn the_ticker_renders_its_headlines_into_the_html_with_no_script_and_no_marquee() {
        let mut with_news = page(Vec::new(), false, false);
        with_news.ticker = vec![headline("Spider-Man returns"), headline("Doomsday dated")];
        let html = render(with_news);

        // Server-rendered with its content already in the HTML: never an empty bar, never
        // a layout shift (§6.3).
        assert!(html.contains("Spider-Man returns"));
        assert!(html.contains("Doomsday dated"));
        // ...and every headline is a real link out to the original.
        assert!(html.contains(r#"href="https://example.test/story""#));
        assert!(html.contains(r#"href="/news""#), "the All news → target");
        // The motion is CSS transform on a duplicated track — not <marquee>, not a
        // requestAnimationFrame loop, so it costs nothing off-screen.
        assert!(!html.contains("<marquee"));
        assert!(!html.to_lowercase().contains("requestanimationframe"));
        // Two runs, and the copy is out of the tab order and hidden from screen readers so
        // no headline is announced or focused twice.
        assert_eq!(html.matches("nx-ticker__run").count(), 2);
        assert_eq!(html.matches(r#"tabindex="-1""#).count(), 2);
    }

    #[test]
    fn a_page_whose_news_call_failed_renders_without_a_ticker_rather_than_an_empty_strip() {
        // `news::ticker` collapses "no stories" and "the call failed" to the same empty
        // vec on purpose; either way the strip is absent, not blank.
        let html = render(page(Vec::new(), false, false));
        assert!(!html.contains("nx-ticker"));
        // ...and the page it decorates is otherwise untouched.
        assert!(html.contains("Amazing Spider-Man"));
    }

    #[test]
    fn signed_out_the_page_shows_both_catalogues_and_no_following_section() {
        let html = render(page(Vec::new(), false, false));

        assert!(html.contains("Amazing Spider-Man"));
        assert!(html.contains("Avengers: Doomsday"));
        assert!(
            !html.contains("id=\"following\""),
            "an empty Following box is worse than none"
        );
        // The window's asymmetry is stated per section, not hidden behind one heading.
        assert!(html.contains("2026-07-28"));
        assert!(html.contains("2026-08-11"));
        // The control is a plain GET form, so it works with JS off.
        assert!(html.contains(r#"name="days" value="30""#));
        // Both attributions are mandatory wherever their data is shown.
        assert!(html.contains("League of Comic Geeks"));
        assert!(html.contains("TMDB"));
    }

    #[test]
    fn the_window_and_sort_forms_each_carry_the_others_current_value() {
        let mut template = page(Vec::new(), false, false);
        template.days = 14;
        template.sort = Sort::Name.as_str();
        let html = render(template);

        // The sort form's hidden field keeps the chosen window...
        assert!(html.contains(r#"<input type="hidden" name="days" value="14">"#));
        // ...and the window form's hidden field keeps the chosen sort, so picking one
        // control never resets the other.
        assert!(html.contains(r#"<input type="hidden" name="sort" value="name">"#));
        // The active sort option is marked, same as the window control (the attribute
        // wraps onto its own line in the markup, so check name/value and aria-pressed
        // separately rather than as one contiguous string).
        assert!(html.contains(r#"name="sort" value="name""#));
        assert_eq!(
            html.matches("aria-pressed=\"true\"").count(),
            2,
            "one pressed window option and one pressed sort option"
        );
    }

    #[test]
    fn signed_in_with_follows_the_following_section_leads_the_page() {
        let html = render(page(vec![title()], false, true));
        let following = html
            .find("id=\"following\"")
            .expect("the Following section is rendered");
        let comics = html.find("id=\"comics\"").expect("the Comics section is rendered");
        assert!(following < comics, "what the viewer follows comes first");
    }

    /* One cell, one item. The toggle is a <form> and the chips are links, so neither may
    nest inside the card's <a> — the card is the container and the anchor is a stretched
    link. This test is the guard on that containment, and on the tab order it produces:
    card link, then toggle, then chips, all inside one card. */
    #[test]
    fn a_card_is_one_element_holding_the_link_the_toggle_and_the_chips() {
        let mut template = page(Vec::new(), false, true);
        // A signed-in row, so the toggle is the real <form> rather than the sign-in link.
        template.comics = vec![MonthGroup {
            label: "August 2026".into(),
            cards: vec![card_row(&comic(), &ProgressMap::default(), true)],
        }];
        let html = render(template);
        let comics = html.find("id=\"comics\"").expect("the Comics section is rendered");
        let titles = html.find("id=\"titles\"").expect("the Film & TV section is rendered");
        let cell = &html[comics..titles];

        assert_eq!(cell.matches("cx-card cx-card--stretch").count(), 1, "one card per item");
        let card = cell.find("cx-card--stretch").expect("the card opens");
        let link = cell.find("cx-card__link").expect("the stretched link");
        let toggle = cell.find("data-progress").expect("the read toggle");
        let chips = cell.find("rx-chips").expect("the subject chips");
        assert!(
            card < link && link < toggle && toggle < chips,
            "all three sit inside the card"
        );

        // The old shape — a bare cell wrapping card + orphaned furniture — is gone.
        assert!(!html.contains("rx-cell"));
    }

    #[test]
    fn a_card_without_a_poster_renders_no_image_and_reserves_no_placeholder_hole() {
        let mut without_poster = title();
        without_poster.cover_url = None;
        let mut template = page(Vec::new(), false, false);
        // The comics section's own card keeps its cover in `page()`'s default fixture —
        // zero it out so the only `<img class="cx-card__cover">` candidate left is the
        // poster-less title this test is actually about.
        template.comics = Vec::new();
        template.titles = vec![MonthGroup {
            label: "August 2026".into(),
            cards: vec![row(&without_poster)],
        }];
        let html = render(template);

        // Same rule as the news image row: no reserved box shaped like a missing poster,
        // just no image element at all.
        assert!(!html.contains("cx-card__cover--placeholder"));
        assert!(!html.contains("<img class=\"cx-card__cover\""));
    }

    #[test]
    fn the_roadmap_headings_one_per_month_and_an_empty_month_is_never_rendered() {
        let mut template = page(Vec::new(), false, false);
        // The comics section's own default fixture would add a third heading — zero it
        // out so the count below is only about the titles timeline under test.
        template.comics = Vec::new();
        template.titles = group_by_month(
            vec![row(&title()), row(&card_named("Later", Some("2026-09-01")))],
            false,
        );
        let html = render(template);

        assert!(html.contains("August 2026"));
        assert!(html.contains("September 2026"));
        // Exactly the two real buckets — nothing for the months with nothing in them.
        assert_eq!(html.matches("rx-month__label").count(), 2);
    }

    #[test]
    fn percy_down_is_a_stated_empty_state_rather_than_a_bare_empty_grid() {
        let html = render(page(Vec::new(), true, false));
        assert!(html.contains("Try again in a moment."));
        // ...and not the "nothing shipped" wording, which would send someone off waiting
        // for a catalogue that is actually fine.
        assert!(!html.contains("href=\"/comics\">comic catalogue"));
    }

    #[test]
    fn the_four_catalogue_surfaces_sit_under_one_discover_menu_that_needs_no_script() {
        let html = render(page(Vec::new(), false, false));

        // A <details>, so the menu opens with the keyboard and with JS off; nav.js only
        // adds outside-click and Escape dismissal on top.
        assert!(html.contains("<details class=\"percy-nav-menu\""));
        for href in ["/releases", "/news", "/watchlist", "/comics"] {
            assert!(
                html.contains(&format!("href=\"{href}\" class=\"percy-dropdown-item")),
                "{href} belongs in the Discover menu"
            );
        }
        // The page marks only its own item; the summary lights up via :has(.active) in CSS,
        // which is why no page declares a second "discover" block.
        assert!(html.contains("href=\"/releases\" class=\"percy-dropdown-item active\""));
        assert!(html.contains("href=\"/news\" class=\"percy-dropdown-item\""));
    }

    #[test]
    fn an_empty_catalogue_points_at_the_two_catalogues() {
        let mut empty = page(Vec::new(), false, false);
        empty.comics = Vec::new();
        empty.titles = Vec::new();
        let html = render(empty);
        assert!(html.contains("comic catalogue"));
        assert!(html.contains("watch order"));
    }
}
