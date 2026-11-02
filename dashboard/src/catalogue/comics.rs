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
use percy_client::{ComicBrowseQuery, ComicCharacter, ComicCredit, ComicRelease, PercyError, ProgressMap};
use serde::Deserialize;

use crate::{
    progress,
    session::Session,
    templates::{
        BrandTab, CharacterLink, ComicBrowseTemplate, ComicCard, ComicDetailTemplate, ComicSeriesIndexTemplate,
        ComicSeriesTemplate, CreditGroup, CreditLink, FilterChip,
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
    #[serde(default)]
    creator: Option<String>,
    #[serde(default)]
    character: Option<String>,
    #[serde(default)]
    series: Option<String>,
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
    let creator = params.creator.filter(|v| !v.is_empty());
    let character = params.character.filter(|v| !v.is_empty());
    let series = params.series.filter(|v| !v.is_empty());
    let filters = BrowseFilters {
        brand: brand.as_deref(),
        search: &search,
        creator: creator.as_deref(),
        character: character.as_deref(),
        series: series.as_deref(),
    };

    let discord_id = session.as_ref().map(|s| s.discord_user_id.clone());
    // All three calls go out together: neither the ticker nor the read marks may add a
    // round trip to the grid they decorate, and neither may take it down when it fails.
    let (fetched, ticker, marks) = match &app.percy {
        Some(percy) => {
            let query = ComicBrowseQuery {
                brand: brand.as_deref(),
                search: (!search.is_empty()).then_some(search.as_str()),
                creator: creator.as_deref(),
                character: character.as_deref(),
                series: series.as_deref(),
                page: Some(page),
                per_page: Some(PER_PAGE),
                ..Default::default()
            };
            let (releases, ticker, marks) = tokio::join!(
                percy.list_comics(&query),
                crate::news::ticker(&app, discord_id.as_deref()),
                progress::fetch(&app, &session, "comic")
            );
            (releases.ok(), ticker, marks)
        }
        None => (None, Vec::new(), ProgressMap::default()),
    };
    // An empty grid has two causes that read identically but are not the same thing. Telling
    // someone their filter matched nothing, when really the backend is down, sends them off
    // adjusting a filter that was never the problem.
    let unavailable = fetched.is_none();
    let data = fetched.unwrap_or_default();

    let (prev_page, next_page) = adjacent_pages(page, data.total_pages);
    // A JS-off mark bounces back to *this* filtered page, not to a bare `/comics` that
    // has lost the brand, the search, the creator/character/series filters and the page
    // the visitor was reading. Built by the same helper as the pagination links, so the
    // two can never disagree.
    let here = browse_href(&filters, page);
    let signed_in = session.is_some();

    ComicBrowseTemplate {
        account: viewer(&session),
        items: data
            .items
            .iter()
            .map(|release| {
                let mut card = comic_card(release);
                card.toggle = Some(progress::toggle(
                    &marks,
                    signed_in,
                    "comic",
                    &release_key(&release.brand, &release.slug),
                    &here,
                ));
                card
            })
            .collect(),
        ticker,
        brand_tabs: BRANDS
            .iter()
            .map(|&(value, label)| {
                let value = (!value.is_empty()).then_some(value);
                BrandTab {
                    label: label.to_string(),
                    href: browse_href(
                        &BrowseFilters {
                            brand: value,
                            ..filters
                        },
                        1,
                    ),
                    active: brand.as_deref() == value,
                }
            })
            .collect(),
        prev_href: prev_page.map(|p| browse_href(&filters, p)),
        next_href: next_page.map(|p| browse_href(&filters, p)),
        filter_chips: filter_chips(&filters),
        brand,
        search,
        creator,
        character,
        series,
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
                latest: None,
                issues: Vec::new(),
                comic_copyright: String::new(),
            };
            return (status_for(&error), body).into_response();
        }
    };

    ComicSeriesTemplate {
        account: viewer(&session),
        series_name: page.series_name.unwrap_or_else(|| page.series_slug.clone()),
        series_slug: page.series_slug,
        brand_label: brand_label(&page.brand).to_string(),
        accent: brand_accent(&page.brand).to_string(),
        publisher: page.publisher,
        issue_count: page.issue_count,
        latest: latest_release_date(&page.issues),
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
    // The marks ride along with the release rather than behind it: progress is decoration
    // on a public page and must never add a round trip to it.
    let (fetched, marks) = match &app.percy {
        Some(percy) => tokio::join!(percy.get_comic(&brand, &slug), progress::fetch(&app, &session, "comic")),
        None => (
            Err(PercyError::Api("percy is not configured".into())),
            ProgressMap::default(),
        ),
    };
    let signed_in = session.is_some();
    let here = format!("/comics/{brand}/{slug}");

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
                toggle: progress::toggle(&marks, signed_in, "comic", &release_key(&brand, &slug), &here),
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
                source_url: None,
                description: None,
                first_seen: None,
                updated_at: None,
                related_news: Vec::new(),
                related_news_href: String::new(),
                comic_copyright: String::new(),
            };
            return (status_for(&error), body).into_response();
        }
    };

    let series = detail.series.clone();
    let release = detail;
    // §6.2's "6b. Related news": the stories tagged with this release's series. The subject
    // key is the series slug verbatim — Percy normalises both sides of the comparison, so a
    // prettified label would match nothing. No series, no section.
    let series_subject = release.series_slug.clone().unwrap_or_default();
    let related_news = if series_subject.is_empty() {
        Vec::new()
    } else {
        crate::news::related(&app, "comic", "series", &series_subject).await
    };
    ComicDetailTemplate {
        account: viewer(&session),
        // Percy's own brand and slug, not the ones off the URL: the key is stored with the
        // brand cased as the catalogue holds it, and a mark filed under `/comics/marvel/…`
        // would never match the one the browse grid writes.
        toggle: progress::toggle(
            &marks,
            signed_in,
            "comic",
            &release_key(&release.brand, &release.slug),
            &here,
        ),
        brand_label: brand_label(&release.brand).to_string(),
        accent: brand_accent(&release.brand).to_string(),
        issue_label: issue_label(release.issue_number.as_deref(), &release.title),
        price_label: price_label(release.price),
        previous: series.previous.as_ref().map(comic_card),
        next: series.next.as_ref().map(comic_card),
        more_from_series: series.more_from_series.iter().map(comic_card).collect(),
        credit_groups: group_credits_by_role(&release.credits, &release.brand),
        characters: release
            .characters
            .iter()
            .map(|character| character_link(character, &release.brand))
            .collect(),
        source_url: release.url.clone(),
        first_seen: release.first_seen.as_deref().map(date_only),
        updated_at: release.updated_at.as_deref().map(date_only),
        related_news,
        related_news_href: crate::news::subject_news_href("comic", "series", &series_subject),
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
/// depending on how League of Comic Geeks ordered its own credit list. Each
/// credit becomes its own link to the browse page filtered to that name and
/// this release's brand.
fn group_credits_by_role(credits: &[ComicCredit], brand: &str) -> Vec<CreditGroup> {
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
        let link = CreditLink {
            name: name.to_string(),
            href: creator_href(name, brand),
        };
        match groups.iter_mut().find(|group| group.role == role) {
            Some(group) => group.credits.push(link),
            None => groups.push(CreditGroup {
                role,
                credits: vec![link],
            }),
        }
    }
    groups
}

/// A character, shaped for the detail page's linked list — same name/kind Percy
/// sent, plus a link to the browse page filtered to that character and brand.
fn character_link(character: &ComicCharacter, brand: &str) -> CharacterLink {
    CharacterLink {
        name: character.name.clone(),
        kind: character.kind.clone(),
        href: character_href(&character.name, brand),
    }
}

/// `/comics?creator={urlencoded name}&brand={brand}` — where a credit on the detail
/// page links to.
fn creator_href(name: &str, brand: &str) -> String {
    format!("/comics?creator={}&brand={}", encode_query_value(name), brand)
}

/// `/comics?character={urlencoded name}&brand={brand}` — where a character on the
/// detail page links to.
fn character_href(name: &str, brand: &str) -> String {
    format!("/comics?character={}&brand={}", encode_query_value(name), brand)
}

/// The previous/next page numbers relative to `page`, or `None` at either
/// edge. `total_pages` of `0` (Percy unreachable, or no results) yields
/// `(None, None)` rather than a phantom "next" to nowhere.
fn adjacent_pages(page: i64, total_pages: i64) -> (Option<i64>, Option<i64>) {
    let prev = (page > 1).then_some(page - 1);
    let next = (total_pages > 0 && page < total_pages).then_some(page + 1);
    (prev, next)
}

/// The date half of a full ISO timestamp — `2026-08-05T09:30:00` becomes `2026-08-05`.
/// `ComicRelease.first_seen`/`updated_at` are `TIMESTAMP` columns, unlike `release_date`
/// (a plain `date`), so they carry a time component a reader has no use for. Same trim
/// `news.rs::published_date` already applies to a story's `published_at`.
fn date_only(value: &str) -> String {
    value.split('T').next().unwrap_or(value).to_string()
}

/// The most recent issue's release date across a series' issues, for the header figure.
/// See `ComicSeriesTemplate::latest`'s doc comment for why this duplicates, rather than
/// reuses, the index card's `ComicSeriesSummary.latest` — and for the two reasons the two
/// figures CAN legitimately disagree (an unscoped `brand` merging publishers on a colliding
/// slug, and the index's 300s cache lagging this endpoint's live read). This figure is
/// authoritative only for exactly the issues this page renders below it; the index's is
/// authoritative per brand. Issues without a date don't count; an all-undated series
/// (nothing solicited yet) has no "latest" to show.
fn latest_release_date(issues: &[ComicRelease]) -> Option<String> {
    issues
        .iter()
        .filter_map(|issue| issue.release_date.as_deref())
        .max()
        .map(str::to_string)
}

/// The string Percy files a comic's read mark under, and the only way one is built.
///
/// `{brand}/{slug}` with the brand cased exactly as the catalogue stores it (uppercase) —
/// it is the same `key` a release card on `/releases` carries, which is what makes a mark
/// made on one surface visible on the other.
fn release_key(brand: &str, slug: &str) -> String {
    format!("{brand}/{slug}")
}

/// A release, shaped for a card — the browse grid, a series' issue list, and
/// the detail page's previous/next/more-from-series links all reuse it.
///
/// The toggle is left off here and filled in by the surfaces that offer one, so a
/// navigation thumbnail never grows a control the visitor did not come for.
fn comic_card(release: &ComicRelease) -> ComicCard {
    ComicCard {
        toggle: None,
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
/// the catalogue pages need it for (`news.rs` builds its filter hrefs with it too).
pub(crate) fn encode_query_value(value: &str) -> String {
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

/// Every filter the browse page carries between the brand tabs, the search box, the
/// filter chips and the pager — one bag so those four controls can never disagree
/// about what is currently active.
#[derive(Debug, Clone, Copy, Default)]
struct BrowseFilters<'a> {
    brand: Option<&'a str>,
    search: &'a str,
    creator: Option<&'a str>,
    character: Option<&'a str>,
    series: Option<&'a str>,
}

/// Builds a `/comics` href carrying the given filters, omitting whatever is
/// at its default so a plain "next page, no filters" link stays `?page=2`
/// instead of `?brand=&search=&page=2`.
fn browse_href(filters: &BrowseFilters<'_>, page: i64) -> String {
    let mut params = Vec::new();
    if let Some(brand) = filters.brand {
        params.push(format!("brand={brand}"));
    }
    if !filters.search.is_empty() {
        params.push(format!("search={}", encode_query_value(filters.search)));
    }
    if let Some(creator) = filters.creator {
        params.push(format!("creator={}", encode_query_value(creator)));
    }
    if let Some(character) = filters.character {
        params.push(format!("character={}", encode_query_value(character)));
    }
    if let Some(series) = filters.series {
        params.push(format!("series={}", encode_query_value(series)));
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

/// The removable chips shown above the grid for whichever of `creator`/`character`/
/// `series` are active. Each chip's × drops just that one filter and resets to page
/// 1 (the result set changed, so the visitor's old page number no longer means
/// anything) while keeping everything else -- brand, search, and the other filters.
fn filter_chips(filters: &BrowseFilters<'_>) -> Vec<FilterChip> {
    let mut chips = Vec::new();
    if let Some(value) = filters.creator {
        chips.push(FilterChip {
            label: format!("Creator: {value}"),
            remove_href: browse_href(
                &BrowseFilters {
                    creator: None,
                    ..*filters
                },
                1,
            ),
        });
    }
    if let Some(value) = filters.character {
        chips.push(FilterChip {
            label: format!("Character: {value}"),
            remove_href: browse_href(
                &BrowseFilters {
                    character: None,
                    ..*filters
                },
                1,
            ),
        });
    }
    if let Some(value) = filters.series {
        chips.push(FilterChip {
            label: format!("Series: {value}"),
            remove_href: browse_href(
                &BrowseFilters {
                    series: None,
                    ..*filters
                },
                1,
            ),
        });
    }
    chips
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
        let groups = group_credits_by_role(&credits, "MARVEL");

        assert_eq!(groups.len(), 2);
        assert_eq!(groups[0].role, "Writer");
        assert_eq!(names(&groups[0]), vec!["Jonathan Hickman", "Jason Aaron"]);
        assert_eq!(groups[1].role, "Artist");
        assert_eq!(names(&groups[1]), vec!["Esad Ribic"]);
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
        let groups = group_credits_by_role(&credits, "MARVEL");

        assert_eq!(groups.len(), 1);
        assert_eq!(groups[0].role, "Contributor");
        assert_eq!(names(&groups[0]), vec!["Someone"]);
    }

    #[test]
    fn group_credits_by_role_links_each_credit_to_the_filtered_browse_page() {
        let credits = vec![ComicCredit {
            name: "Jim Lee".into(),
            role: "Artist".into(),
        }];
        let groups = group_credits_by_role(&credits, "DC");
        assert_eq!(groups[0].credits[0].href, "/comics?creator=Jim+Lee&brand=DC");
    }

    #[test]
    fn character_link_urlencodes_the_name_and_keeps_the_brand() {
        let character = ComicCharacter {
            name: "Doctor Doom & Sons".into(),
            kind: Some("Villain".into()),
        };
        let link = character_link(&character, "MARVEL");
        assert_eq!(link.href, "/comics?character=Doctor+Doom+%26+Sons&brand=MARVEL");
        assert_eq!(link.kind, Some("Villain".to_string()));
    }

    /// Test-only helper: pulls the plain names back out of a `CreditGroup` so the
    /// existing assertions don't have to know about `CreditLink`'s `href`.
    fn names(group: &CreditGroup) -> Vec<&str> {
        group.credits.iter().map(|c| c.name.as_str()).collect()
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
        assert_eq!(browse_href(&BrowseFilters::default(), 1), "/comics");
        assert_eq!(
            browse_href(
                &BrowseFilters {
                    brand: Some("MARVEL"),
                    ..Default::default()
                },
                1
            ),
            "/comics?brand=MARVEL"
        );
        assert_eq!(
            browse_href(
                &BrowseFilters {
                    search: "spider man",
                    ..Default::default()
                },
                2
            ),
            "/comics?search=spider+man&page=2"
        );
        assert_eq!(
            browse_href(
                &BrowseFilters {
                    brand: Some("DC"),
                    search: "bat",
                    ..Default::default()
                },
                3
            ),
            "/comics?brand=DC&search=bat&page=3"
        );
    }

    #[test]
    fn browse_href_carries_creator_character_and_series_filters() {
        let filters = BrowseFilters {
            brand: Some("DC"),
            creator: Some("Jim Lee"),
            character: Some("Batman"),
            series: Some("batman"),
            ..Default::default()
        };
        assert_eq!(
            browse_href(&filters, 1),
            "/comics?brand=DC&creator=Jim+Lee&character=Batman&series=batman"
        );
    }

    #[test]
    fn filter_chips_are_present_only_for_active_filters_and_each_drops_just_its_own() {
        let filters = BrowseFilters {
            brand: Some("DC"),
            creator: Some("Jim Lee"),
            ..Default::default()
        };
        let chips = filter_chips(&filters);
        assert_eq!(chips.len(), 1);
        assert_eq!(chips[0].label, "Creator: Jim Lee");
        // Removing the chip keeps the brand but drops the creator and resets to page 1.
        assert_eq!(chips[0].remove_href, "/comics?brand=DC");

        assert!(filter_chips(&BrowseFilters::default()).is_empty());
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

    // -- Progress: the read toggle on the two comic surfaces (R8b) ---------

    /// The browse page as the handler assembles it, with the two things that must agree:
    /// the toggle's key and the `redirect` the pagination links are built from.
    fn browse_page(
        releases: &[ComicRelease],
        map: &ProgressMap,
        signed_in: bool,
        brand: Option<&str>,
        search: &str,
    ) -> ComicBrowseTemplate {
        let filters = BrowseFilters {
            brand,
            search,
            ..Default::default()
        };
        let here = browse_href(&filters, 2);
        ComicBrowseTemplate {
            account: None,
            items: releases
                .iter()
                .map(|release| {
                    let mut card = comic_card(release);
                    card.toggle = Some(progress::toggle(
                        map,
                        signed_in,
                        "comic",
                        &release_key(&release.brand, &release.slug),
                        &here,
                    ));
                    card
                })
                .collect(),
            brand_tabs: Vec::new(),
            ticker: Vec::new(),
            brand: brand.map(str::to_string),
            search: search.to_string(),
            creator: None,
            character: None,
            series: None,
            filter_chips: Vec::new(),
            total: releases.len() as i64,
            page: 2,
            total_pages: 3,
            prev_href: None,
            next_href: None,
            unavailable: false,
            comic_copyright: String::new(),
        }
    }

    fn detail_page(map: &ProgressMap, signed_in: bool) -> ComicDetailTemplate {
        let source = release("MARVEL", "amazing-spider-man-1");
        ComicDetailTemplate {
            account: None,
            brand_label: brand_label(&source.brand).to_string(),
            accent: brand_accent(&source.brand).to_string(),
            toggle: progress::toggle(
                map,
                signed_in,
                "comic",
                &release_key(&source.brand, &source.slug),
                "/comics/MARVEL/amazing-spider-man-1",
            ),
            title: "Amazing Spider-Man #1".into(),
            issue_label: None,
            release_date: Some("2026-08-01".into()),
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
            source_url: None,
            description: None,
            first_seen: None,
            updated_at: None,
            related_news: Vec::new(),
            related_news_href: String::new(),
            comic_copyright: publisher_copyright(&source.brand).to_string(),
            brand: source.brand,
            slug: source.slug,
        }
    }

    fn render<T: askama::Template>(page: T) -> String {
        page.render().expect("the comic template renders")
    }

    #[test]
    fn a_read_row_renders_the_undo_and_an_unread_one_renders_the_mark() {
        let map = crate::progress::tests::map("comic", &["MARVEL/amazing-spider-man-1"]);
        let html = render(browse_page(
            &[release("MARVEL", "amazing-spider-man-1"), release("DC", "batman-2")],
            &map,
            true,
            None,
            "",
        ));

        assert!(html.contains(r#"action="/account/progress/remove""#));
        // The brand keeps the casing the catalogue stores it in: a key built from a
        // lowercased brand would file the mark against nothing.
        assert!(html.contains(r#"value="MARVEL/amazing-spider-man-1""#));
        assert!(html.contains("pg--on"));
        assert!(html.contains(r#"action="/account/progress""#));
        assert!(html.contains(r#"value="DC/batman-2""#));
        assert!(html.contains("Mark read"));
        assert!(html.contains(r#"name="status" value="read""#));
    }

    #[test]
    fn a_browse_row_comes_back_to_the_page_the_visitor_was_reading() {
        // Otherwise a JS-off click drops them on an unfiltered page one, having lost the
        // brand, the search and their place.
        let html = render(browse_page(
            &[release("MARVEL", "amazing-spider-man-1")],
            &ProgressMap::default(),
            true,
            Some("MARVEL"),
            "spider man",
        ));
        assert!(html.contains(r#"name="redirect" value="/comics?brand=MARVEL&amp;search=spider+man&amp;page=2""#));
    }

    #[test]
    fn signed_out_the_browse_grid_offers_a_sign_in_rather_than_a_dead_button() {
        let html = render(browse_page(
            &[release("MARVEL", "amazing-spider-man-1")],
            &ProgressMap::default(),
            false,
            None,
            "",
        ));

        assert!(
            !html.contains("/account/progress"),
            "no write control without a session"
        );
        assert!(html.contains("Sign in to track"));
        // ...and the catalogue is untouched: this page is useful logged out.
        assert!(html.contains("/comics/MARVEL/amazing-spider-man-1"));
    }

    #[test]
    fn the_detail_page_marks_the_release_under_the_same_key_the_grid_uses() {
        let map = crate::progress::tests::map("comic", &["MARVEL/amazing-spider-man-1"]);
        let marked = render(detail_page(&map, true));
        assert!(marked.contains(r#"action="/account/progress/remove""#));
        assert!(marked.contains(r#"value="MARVEL/amazing-spider-man-1""#));
        assert!(marked.contains(r#"name="redirect" value="/comics/MARVEL/amazing-spider-man-1""#));
        assert!(marked.contains("Read</span>"));

        let out = render(detail_page(&ProgressMap::default(), false));
        assert!(!out.contains("/account/progress"));
        assert!(out.contains("Sign in to track"));
    }

    #[test]
    fn the_detail_page_links_every_credit_and_character_and_the_source() {
        let mut page = detail_page(&ProgressMap::default(), false);
        page.credit_groups = group_credits_by_role(
            &[
                ComicCredit {
                    name: "Jonathan Hickman".into(),
                    role: "Writer".into(),
                },
                ComicCredit {
                    name: "Jason Aaron".into(),
                    role: "Writer".into(),
                },
            ],
            "MARVEL",
        );
        page.characters = vec![character_link(
            &ComicCharacter {
                name: "Doctor Doom".into(),
                kind: Some("Villain".into()),
            },
            "MARVEL",
        )];
        page.source_url = Some("https://leagueofcomicgeeks.com/comic/123/amazing-spider-man-1".into());

        let html = render(page);

        // Every creator in a group is its own link, not one joined string. Askama HTML-escapes
        // the `&` between query params, so the rendered attribute carries `&amp;`.
        assert!(html.contains(r#"<a href="/comics?creator=Jonathan+Hickman&amp;brand=MARVEL">Jonathan Hickman</a>"#));
        assert!(html.contains(r#"<a href="/comics?creator=Jason+Aaron&amp;brand=MARVEL">Jason Aaron</a>"#));
        assert!(html.contains(r#"<a href="/comics?character=Doctor+Doom&amp;brand=MARVEL">Doctor Doom</a>"#));
        assert!(html.contains(
            r#"<a href="https://leagueofcomicgeeks.com/comic/123/amazing-spider-man-1" target="_blank" rel="noopener">View original listing</a>"#
        ));
    }

    #[test]
    fn the_detail_page_omits_the_source_row_when_percy_carries_no_url() {
        let html = render(detail_page(&ProgressMap::default(), false));
        assert!(!html.contains("<dt>Source</dt>"));
    }

    #[test]
    fn date_only_trims_a_timestamp_and_passes_a_bare_date_through() {
        assert_eq!(date_only("2026-08-05T09:30:00"), "2026-08-05");
        assert_eq!(date_only("2026-08-05"), "2026-08-05");
    }

    #[test]
    fn latest_release_date_is_the_maximum_dated_issue_and_none_when_all_undated() {
        let mut earlier = release("MARVEL", "one");
        earlier.release_date = Some("2026-08-01".into());
        let mut later = release("MARVEL", "two");
        later.release_date = Some("2026-09-01".into());
        let undated = release("MARVEL", "three");

        assert_eq!(
            latest_release_date(&[earlier, later, undated.clone()]),
            Some("2026-09-01".to_string())
        );
        assert_eq!(latest_release_date(&[undated]), None);
        assert_eq!(latest_release_date(&[]), None);
    }

    #[test]
    fn the_detail_page_renders_first_seen_and_updated_when_percy_carries_them() {
        let mut page = detail_page(&ProgressMap::default(), false);
        page.first_seen = Some("2026-06-01".into());
        page.updated_at = Some("2026-08-10".into());

        let html = render(page);
        assert!(html.contains("<dt>First tracked</dt><dd>2026-06-01</dd>"));
        assert!(html.contains("<dt>Updated</dt><dd>2026-08-10</dd>"));
    }

    #[test]
    fn the_detail_page_omits_first_seen_and_updated_labels_when_percy_carries_neither() {
        // detail_page() leaves both at None: the omit-when-absent case, not a blank value.
        let html = render(detail_page(&ProgressMap::default(), false));
        assert!(!html.contains("<dt>First tracked</dt>"));
        assert!(!html.contains("<dt>Updated</dt>"));
    }

    fn series_page(latest: Option<String>) -> ComicSeriesTemplate {
        ComicSeriesTemplate {
            account: None,
            series_slug: "amazing-spider-man".into(),
            series_name: "Amazing Spider-Man".into(),
            brand: "MARVEL".into(),
            brand_label: brand_label("MARVEL").to_string(),
            accent: brand_accent("MARVEL").to_string(),
            publisher: None,
            issue_count: 2,
            latest,
            issues: Vec::new(),
            comic_copyright: publisher_copyright("MARVEL").to_string(),
        }
    }

    #[test]
    fn the_series_page_renders_the_latest_figure_when_percy_carries_it() {
        let html = render(series_page(Some("2026-08-01".into())));
        assert!(html.contains(r#"<span class="cx-figure__value">2026-08-01</span>"#));
        assert!(html.contains(r#"<span class="cx-figure__label">Latest</span>"#));
    }

    #[test]
    fn the_series_page_omits_the_latest_figure_when_no_issue_has_a_date() {
        // Not just an empty value -- the whole figure (value and label) is gone.
        let html = render(series_page(None));
        assert!(!html.contains(r#"<span class="cx-figure__label">Latest</span>"#));
    }

    fn series_summary(latest: Option<String>) -> percy_client::ComicSeriesSummary {
        percy_client::ComicSeriesSummary {
            series_slug: "amazing-spider-man".into(),
            series_name: Some("Amazing Spider-Man".into()),
            brand: "MARVEL".into(),
            issue_count: 12,
            latest,
        }
    }

    #[test]
    fn the_series_index_card_shows_the_latest_issue_date_when_percy_carries_it() {
        let html = render(ComicSeriesIndexTemplate {
            account: None,
            series: vec![series_summary(Some("2026-08-01".into()))],
            comic_copyright: String::new(),
        });
        assert!(html.contains("12 issues"));
        assert!(html.contains("Latest 2026-08-01"));
    }

    #[test]
    fn the_series_index_card_omits_the_latest_label_when_percy_carries_no_date() {
        // series_summary(None) is the real shape of a series with no dated issue yet --
        // not just a blank value, the "Latest" text itself must not appear.
        let html = render(ComicSeriesIndexTemplate {
            account: None,
            series: vec![series_summary(None)],
            comic_copyright: String::new(),
        });
        assert!(html.contains("12 issues"));
        assert!(!html.contains("Latest"));
    }

    #[test]
    fn the_browse_page_shows_a_removable_chip_for_each_active_filter() {
        let filters = BrowseFilters {
            brand: Some("MARVEL"),
            creator: Some("Jim Lee"),
            ..Default::default()
        };
        let mut page = browse_page(&[], &ProgressMap::default(), false, Some("MARVEL"), "");
        page.creator = Some("Jim Lee".to_string());
        page.filter_chips = filter_chips(&filters);

        let html = render(page);
        assert!(html.contains(r#"<span class="cx-filter-chip">"#));
        assert!(html.contains("Creator: Jim Lee"));
        assert!(html.contains(r#"class="cx-filter-chip__remove" href="/comics?brand=MARVEL""#));
        // The hidden field keeps the filter alive across a fresh search submit.
        assert!(html.contains(r#"<input type="hidden" name="creator" value="Jim Lee">"#));
    }

    #[test]
    fn the_browse_page_shows_no_chip_row_with_no_active_filters() {
        let html = render(browse_page(&[], &ProgressMap::default(), false, None, ""));
        assert!(!html.contains("cx-filters"));
    }
}
