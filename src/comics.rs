//! Public comic-catalogue pages — browse, one series, one release.
//!
//! Same shape as `watchlist.rs`: no session, no JS state, everything shaped
//! server-side from Percy's public comic catalogue endpoints. When Percy is
//! unreachable the pages degrade to a stated empty state rather than a 500.

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
};
use percy_client::{ComicBrowseQuery, ComicCredit, ComicRelease, PercyError};
use serde::Deserialize;

use crate::{
    session::Session,
    templates::{
        BrandTab, ComicBrowseTemplate, ComicCard, ComicDetailTemplate, ComicSeriesIndexTemplate, ComicSeriesTemplate,
        CreditGroup,
    },
    view::Viewer,
    AppState,
};

/// Releases per browse page. Percy caps `per_page` at 100; this is just what
/// the grid has room for before paging feels stingy.
const PER_PAGE: i64 = 24;

/// Brand filter tabs, in display order. Empty string means "no filter".
const BRANDS: &[(&str, &str)] = &[("", "All"), ("MARVEL", "Marvel"), ("DC", "DC"), ("MANGA", "Manga")];

/// Percy's "not found" becomes ours; every other failure is a 200 empty state.
///
/// The distinction is the point: a catalogue page that answers 200 to any URL a crawler
/// invents is a soft 404 and gets indexed as a real page. A backend outage is different --
/// the release probably exists, so the page says "try again" and keeps its 200.
fn status_for(error: &PercyError) -> StatusCode {
    match error {
        PercyError::NotFound => StatusCode::NOT_FOUND,
        _ => StatusCode::OK,
    }
}

fn viewer(session: &Option<Session>) -> Option<Viewer> {
    session.as_ref().map(Viewer::from_session)
}

/// Query string of the browse page. All optional; an absent `page` is `1`.
#[derive(Debug, Default, Deserialize)]
pub struct ComicBrowseParams {
    #[serde(default)]
    brand: Option<String>,
    #[serde(default)]
    search: Option<String>,
    #[serde(default)]
    page: Option<i64>,
}

/// `GET /comics` — the release browser.
pub async fn comic_browse(
    State(app): State<AppState>,
    session: Option<Session>,
    Query(params): Query<ComicBrowseParams>,
) -> Response {
    let brand = params.brand.filter(|b| !b.is_empty());
    let search = params.search.unwrap_or_default();
    let page = params.page.filter(|p| *p > 0).unwrap_or(1);

    let fetched = match &app.percy {
        Some(percy) => percy
            .list_comics(&ComicBrowseQuery {
                brand: brand.as_deref(),
                search: (!search.is_empty()).then_some(search.as_str()),
                page: Some(page),
                per_page: Some(PER_PAGE),
                ..Default::default()
            })
            .await
            .ok(),
        None => None,
    };
    // An empty grid has two causes that read identically but are not the same thing. Telling
    // someone their filter matched nothing, when really the backend is down, sends them off
    // adjusting a filter that was never the problem.
    let unavailable = fetched.is_none();
    let data = fetched.unwrap_or_default();

    let (prev_page, next_page) = adjacent_pages(page, data.total_pages);

    ComicBrowseTemplate {
        account: viewer(&session),
        items: data.items.iter().map(comic_card).collect(),
        brand_tabs: BRANDS
            .iter()
            .map(|&(value, label)| {
                let value = (!value.is_empty()).then_some(value);
                BrandTab {
                    label: label.to_string(),
                    href: browse_href(value, &search, 1),
                    active: brand.as_deref() == value,
                }
            })
            .collect(),
        prev_href: prev_page.map(|p| browse_href(brand.as_deref(), &search, p)),
        next_href: next_page.map(|p| browse_href(brand.as_deref(), &search, p)),
        brand,
        search,
        total: data.total,
        page,
        total_pages: data.total_pages,
        unavailable,
        comic_copyright: String::new(),
    }
    .into_response()
}

/// `GET /comics/series` — every series as a card.
pub async fn comic_series_index(State(app): State<AppState>, session: Option<Session>) -> Response {
    let series = match &app.percy {
        Some(percy) => percy
            .list_comic_series(None)
            .await
            .map(|response| response.series)
            .unwrap_or_default(),
        None => Vec::new(),
    };

    ComicSeriesIndexTemplate {
        account: viewer(&session),
        series,
        comic_copyright: String::new(),
    }
    .into_response()
}

/// Query string of the series page — `brand` disambiguates a slug two
/// publishers happen to collide on.
#[derive(Debug, Default, Deserialize)]
pub struct SeriesDetailParams {
    #[serde(default)]
    brand: Option<String>,
}

/// `GET /comics/series/:slug` — one series, issues in reading order.
pub async fn comic_series_detail(
    State(app): State<AppState>,
    session: Option<Session>,
    Path(slug): Path<String>,
    Query(params): Query<SeriesDetailParams>,
) -> Response {
    let brand_filter = params.brand.filter(|b| !b.is_empty());

    let fetched = match &app.percy {
        Some(percy) => percy.get_comic_series(&slug, brand_filter.as_deref()).await,
        None => Err(PercyError::Api("percy is not configured".into())),
    };

    // "No such series" and "Percy is unreachable" are different answers and must not share a
    // status code: a public catalogue that returns 200 for every nonsense URL is a soft 404,
    // which search engines index. Percy's 404 becomes ours; anything else stays a 200 empty
    // state, because the series may well exist and only the backend be down.
    let page = match fetched {
        Ok(page) => page,
        Err(error) => {
            let body = ComicSeriesTemplate {
                account: viewer(&session),
                series_slug: slug.clone(),
                series_name: slug,
                brand: String::new(),
                brand_label: brand_label("").to_string(),
                accent: brand_accent("").to_string(),
                publisher: None,
                issue_count: 0,
                issues: Vec::new(),
                comic_copyright: String::new(),
            };
            return (status_for(&error), body).into_response();
        }
    };

    ComicSeriesTemplate {
        account: viewer(&session),
        series_slug: page.series_slug,
        series_name: page.series_name,
        brand_label: brand_label(&page.brand).to_string(),
        accent: brand_accent(&page.brand).to_string(),
        publisher: page.publisher,
        issue_count: page.issue_count,
        issues: page.issues.iter().map(comic_card).collect(),
        comic_copyright: publisher_copyright(&page.brand).to_string(),
        brand: page.brand,
    }
    .into_response()
}

/// `GET /comics/:brand/:slug` — one release.
pub async fn comic_detail(
    State(app): State<AppState>,
    session: Option<Session>,
    Path((brand, slug)): Path<(String, String)>,
) -> Response {
    let fetched = match &app.percy {
        Some(percy) => percy.get_comic(&brand, &slug).await,
        None => Err(PercyError::Api("percy is not configured".into())),
    };

    // Same split as the series page: an unknown release is a real 404, a backend that is down
    // is a 200 empty state (checked in the template via an empty `title`, the way
    // `watch_order.html` checks `eras.is_empty()`).
    let detail = match fetched {
        Ok(detail) => detail,
        Err(error) => {
            let body = ComicDetailTemplate {
                account: viewer(&session),
                brand: brand.clone(),
                brand_label: brand_label(&brand).to_string(),
                accent: brand_accent(&brand).to_string(),
                title: String::new(),
                slug,
                issue_label: None,
                release_date: None,
                cover_url: None,
                format: None,
                page_count: None,
                price_label: None,
                publisher: None,
                series_name: None,
                series_slug: None,
                previous: None,
                next: None,
                more_from_series: Vec::new(),
                credit_groups: Vec::new(),
                characters: Vec::new(),
                description: None,
                comic_copyright: String::new(),
            };
            return (status_for(&error), body).into_response();
        }
    };

    let release = detail.release;
    ComicDetailTemplate {
        account: viewer(&session),
        brand_label: brand_label(&release.brand).to_string(),
        accent: brand_accent(&release.brand).to_string(),
        issue_label: issue_label(release.issue_number.as_deref(), &release.title),
        price_label: price_label(release.price),
        previous: detail.series.previous.as_ref().map(comic_card),
        next: detail.series.next.as_ref().map(comic_card),
        more_from_series: detail.series.more_from_series.iter().map(comic_card).collect(),
        credit_groups: group_credits_by_role(&release.credits),
        comic_copyright: publisher_copyright(&release.brand).to_string(),
        brand: release.brand,
        title: release.title,
        slug: release.slug,
        release_date: release.release_date,
        cover_url: release.cover_url,
        format: release.format,
        page_count: release.page_count,
        publisher: release.publisher,
        series_name: release.series_name,
        series_slug: release.series_slug,
        characters: release.characters,
        description: release.description,
    }
    .into_response()
}

/// A publisher's own brand colour, the one `--comic-accent` a page may use.
/// Manga has no usable League/VIZ brand colour, so it falls back to the
/// site's own token rather than inventing one.
fn brand_accent(brand: &str) -> &'static str {
    match brand {
        "MARVEL" => "#EC1D24",
        "DC" => "#0074E8",
        _ => "var(--branding)",
    }
}

/// Display label for a brand code.
fn brand_label(brand: &str) -> &'static str {
    match brand {
        "MARVEL" => "Marvel",
        "DC" => "DC",
        "MANGA" => "Manga",
        _ => "Comics",
    }
}

/// The publisher copyright line the attribution partial shows for a brand.
fn publisher_copyright(brand: &str) -> &'static str {
    match brand {
        "MARVEL" => "© Marvel",
        "DC" => "© DC Comics",
        "MANGA" => "© VIZ Media, LLC",
        _ => "",
    }
}

/// `#12` for a numbered issue; `None` when the release carries no issue number
/// (a one-shot or a collected edition).
fn issue_label(issue_number: Option<&str>, title: &str) -> Option<String> {
    let label = issue_number
        .map(str::trim)
        .filter(|n| !n.is_empty())
        .map(|n| format!("#{n}"))?;
    // locg-api titles carry the issue number already ("Batgirl #22"), and Percy's ingest
    // parses it back out into its own column. Showing both renders "Batgirl #22 #22", so the
    // chip is only worth drawing when the title does not already end with it.
    if title.trim_end().ends_with(&label) {
        return None;
    }
    Some(label)
}

/// The series name, unless the title already opens with it.
///
/// A card showing "Batgirl #22" over "Batgirl" spends a line saying nothing: for an ongoing
/// series the title *is* the series plus an issue number. Collected editions and one-shots,
/// whose title and series diverge, still get the line.
fn card_series_name(series_name: Option<&str>, title: &str) -> Option<String> {
    let series = series_name.map(str::trim).filter(|s| !s.is_empty())?;
    if title.trim().starts_with(series) {
        return None;
    }
    Some(series.to_string())
}

/// `$4.99`, or `Free` — a fair number of tracked releases are $0.00 digital or promo issues
/// ("DC Connect"), and a price tag reading "$0.00" looks like missing data rather than a fact.
fn price_label(price: Option<f64>) -> Option<String> {
    price.map(|p| {
        if p <= 0.0 {
            "Free".to_string()
        } else {
            format!("${p:.2}")
        }
    })
}

/// Groups a release's credits by role, preserving the order roles first
/// appear in — so "Writer" doesn't jump around the page between releases
/// depending on how League of Comic Geeks ordered its own credit list.
fn group_credits_by_role(credits: &[ComicCredit]) -> Vec<CreditGroup> {
    let mut groups: Vec<CreditGroup> = Vec::new();
    for credit in credits {
        let name = credit.name.trim();
        if name.is_empty() {
            continue;
        }
        let role = if credit.role.trim().is_empty() {
            "Contributor".to_string()
        } else {
            credit.role.trim().to_string()
        };
        match groups.iter_mut().find(|group| group.role == role) {
            Some(group) => group.names.push(name.to_string()),
            None => groups.push(CreditGroup {
                role,
                names: vec![name.to_string()],
            }),
        }
    }
    groups
}

/// The previous/next page numbers relative to `page`, or `None` at either
/// edge. `total_pages` of `0` (Percy unreachable, or no results) yields
/// `(None, None)` rather than a phantom "next" to nowhere.
fn adjacent_pages(page: i64, total_pages: i64) -> (Option<i64>, Option<i64>) {
    let prev = (page > 1).then_some(page - 1);
    let next = (total_pages > 0 && page < total_pages).then_some(page + 1);
    (prev, next)
}

/// A release, shaped for a card — the browse grid, a series' issue list, and
/// the detail page's previous/next/more-from-series links all reuse it.
fn comic_card(release: &ComicRelease) -> ComicCard {
    ComicCard {
        brand: release.brand.clone(),
        slug: release.slug.clone(),
        title: release.title.clone(),
        issue_label: issue_label(release.issue_number.as_deref(), &release.title),
        series_name: card_series_name(release.series_name.as_deref(), &release.title),
        release_date: release.release_date.clone(),
        cover_url: release.cover_url.clone(),
        price_label: price_label(release.price),
    }
}

/// `application/x-www-form-urlencoded`-style percent-encoding for a query
/// value (space -> `+`, everything outside the unreserved set -> `%XX`).
/// No `urlencoding` crate in this workspace, and this is the entire surface
/// this module needs it for.
fn encode_query_value(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => out.push(byte as char),
            b' ' => out.push('+'),
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

/// Builds a `/comics` href carrying the given filters, omitting whatever is
/// at its default so a plain "next page, no filters" link stays `?page=2`
/// instead of `?brand=&search=&page=2`.
fn browse_href(brand: Option<&str>, search: &str, page: i64) -> String {
    let mut params = Vec::new();
    if let Some(brand) = brand {
        params.push(format!("brand={brand}"));
    }
    if !search.is_empty() {
        params.push(format!("search={}", encode_query_value(search)));
    }
    if page > 1 {
        params.push(format!("page={page}"));
    }
    if params.is_empty() {
        "/comics".to_string()
    } else {
        format!("/comics?{}", params.join("&"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn release(brand: &str, slug: &str) -> ComicRelease {
        ComicRelease {
            brand: brand.to_string(),
            slug: slug.to_string(),
            title: slug.to_string(),
            ..ComicRelease::default()
        }
    }

    #[test]
    fn brand_accent_uses_publisher_colours_and_falls_back_for_manga() {
        assert_eq!(brand_accent("MARVEL"), "#EC1D24");
        assert_eq!(brand_accent("DC"), "#0074E8");
        assert_eq!(brand_accent("MANGA"), "var(--branding)");
        assert_eq!(brand_accent("UNKNOWN"), "var(--branding)");
    }

    #[test]
    fn brand_label_formats_known_brands_and_falls_back() {
        assert_eq!(brand_label("MARVEL"), "Marvel");
        assert_eq!(brand_label("DC"), "DC");
        assert_eq!(brand_label("MANGA"), "Manga");
        assert_eq!(brand_label(""), "Comics");
    }

    #[test]
    fn issue_label_prefixes_the_number_and_omits_when_absent() {
        assert_eq!(
            issue_label(Some("12"), "Some Collected Edition"),
            Some("#12".to_string())
        );
        assert_eq!(issue_label(Some("  "), "Whatever"), None);
        assert_eq!(issue_label(None, "Whatever"), None);
    }

    #[test]
    fn issue_label_is_dropped_when_the_title_already_carries_it() {
        // The common case: every locg-api title ends with its own issue number.
        assert_eq!(issue_label(Some("22"), "Batgirl #22"), None);
        assert_eq!(issue_label(Some("22"), "Batgirl #22 "), None);
        // ...but a number that merely *appears* in the title is not the same thing.
        assert_eq!(issue_label(Some("2"), "100 Bullets #12"), Some("#2".to_string()));
    }

    #[test]
    fn card_series_name_is_dropped_when_the_title_opens_with_it() {
        assert_eq!(card_series_name(Some("Batgirl"), "Batgirl #22"), None);
        assert_eq!(
            card_series_name(Some("Batman"), "Batman by Tom King Omnibus Vol. 2 HC"),
            None
        );
        assert_eq!(
            card_series_name(Some("Amazing Spider-Man"), "Spider-Verse #1"),
            Some("Amazing Spider-Man".to_string())
        );
        assert_eq!(card_series_name(None, "Anything"), None);
        assert_eq!(card_series_name(Some("  "), "Anything"), None);
    }

    #[test]
    fn price_label_formats_two_decimals() {
        assert_eq!(price_label(Some(4.9)), Some("$4.90".to_string()));
        assert_eq!(price_label(None), None);
        // Free digital/promo issues are common; "$0.00" reads as missing data.
        assert_eq!(price_label(Some(0.0)), Some("Free".to_string()));
    }

    #[test]
    fn group_credits_by_role_preserves_first_appearance_order() {
        let credits = vec![
            ComicCredit {
                name: "Jonathan Hickman".into(),
                role: "Writer".into(),
            },
            ComicCredit {
                name: "Esad Ribic".into(),
                role: "Artist".into(),
            },
            ComicCredit {
                name: "Jason Aaron".into(),
                role: "Writer".into(),
            },
        ];
        let groups = group_credits_by_role(&credits);

        assert_eq!(groups.len(), 2);
        assert_eq!(groups[0].role, "Writer");
        assert_eq!(groups[0].names, vec!["Jonathan Hickman", "Jason Aaron"]);
        assert_eq!(groups[1].role, "Artist");
        assert_eq!(groups[1].names, vec!["Esad Ribic"]);
    }

    #[test]
    fn group_credits_by_role_skips_blank_names_and_labels_blank_roles() {
        let credits = vec![
            ComicCredit {
                name: "".into(),
                role: "Writer".into(),
            },
            ComicCredit {
                name: "Someone".into(),
                role: "  ".into(),
            },
        ];
        let groups = group_credits_by_role(&credits);

        assert_eq!(groups.len(), 1);
        assert_eq!(groups[0].role, "Contributor");
        assert_eq!(groups[0].names, vec!["Someone"]);
    }

    #[test]
    fn adjacent_pages_middle_page_has_both_neighbours() {
        assert_eq!(adjacent_pages(3, 5), (Some(2), Some(4)));
    }

    #[test]
    fn adjacent_pages_first_page_has_no_previous() {
        assert_eq!(adjacent_pages(1, 5), (None, Some(2)));
    }

    #[test]
    fn adjacent_pages_last_page_has_no_next() {
        assert_eq!(adjacent_pages(5, 5), (Some(4), None));
    }

    #[test]
    fn adjacent_pages_zero_total_pages_has_neither() {
        // Percy unreachable, or a genuinely empty result set.
        assert_eq!(adjacent_pages(1, 0), (None, None));
    }

    #[test]
    fn encode_query_value_escapes_spaces_and_reserved_characters() {
        assert_eq!(encode_query_value("spider man"), "spider+man");
        assert_eq!(encode_query_value("a&b"), "a%26b");
        assert_eq!(encode_query_value("100%"), "100%25");
    }

    #[test]
    fn browse_href_omits_defaults_and_keeps_page_only_past_one() {
        assert_eq!(browse_href(None, "", 1), "/comics");
        assert_eq!(browse_href(Some("MARVEL"), "", 1), "/comics?brand=MARVEL");
        assert_eq!(browse_href(None, "spider man", 2), "/comics?search=spider+man&page=2");
        assert_eq!(browse_href(Some("DC"), "bat", 3), "/comics?brand=DC&search=bat&page=3");
    }

    #[test]
    fn comic_card_maps_release_fields_into_the_view_model() {
        let mut source = release("MARVEL", "amazing-spider-man-1");
        source.issue_number = Some("1".into());
        source.price = Some(3.99);
        source.series_name = Some("Amazing Spider-Man".into());

        let card = comic_card(&source);
        assert_eq!(card.brand, "MARVEL");
        assert_eq!(card.slug, "amazing-spider-man-1");
        // The fixture's title does not carry the issue number, so both survive.
        assert_eq!(card.issue_label, Some("#1".to_string()));
        assert_eq!(card.price_label, Some("$3.99".to_string()));
        assert_eq!(card.series_name, Some("Amazing Spider-Man".to_string()));
    }

    #[test]
    fn status_for_separates_a_missing_release_from_a_backend_outage() {
        assert_eq!(status_for(&PercyError::NotFound), StatusCode::NOT_FOUND);
        assert_eq!(status_for(&PercyError::Api("boom".into())), StatusCode::OK);
    }

    #[test]
    fn publisher_copyright_is_present_for_every_known_brand() {
        assert_eq!(publisher_copyright("MARVEL"), "© Marvel");
        assert_eq!(publisher_copyright("DC"), "© DC Comics");
        assert_eq!(publisher_copyright("MANGA"), "© VIZ Media, LLC");
    }
}
