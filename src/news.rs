//! `/news` — the stream, plus the two news fragments other pages embed.
//!
//! Public and server-rendered like `/releases`, `/comics` and `/watchlist` (D9): the filters
//! are a plain `GET` form, paging is real links, and nothing on the page needs JS. Signed in,
//! the viewer's Discord id rides along and Percy leads the *same single list* with the stories
//! matching what they follow — `personalised` says how many, so the leading rows can be
//! labelled without the page ever building two lists.
//!
//! **§9 is a hard ceiling on what may be rendered.** A story is headline + a short excerpt +
//! the link to the original + the source's name, and nothing else. Percy's payload also carries
//! `image_url` and `author` — this module deliberately never puts either in a view model, so no
//! template can reach them. The link out is `rel="noopener nofollow ugc"` for the same reason:
//! we are quoting someone else's headline, not vouching for it.
//!
//! Two fragments live here rather than in the pages that show them, so the §9 rule has one home:
//!
//! * [`ticker`] — the strip on `/releases`, `/comics` and `/watchlist` (§6.3). It returns an
//!   empty vec on *any* failure, and the partial renders nothing for an empty vec: a page whose
//!   news call failed shows no ticker rather than an empty bar.
//! * [`related_for_comic_series`] — §6.2's "6b. Related news", absent rather than empty.

use axum::{
    extract::{Query, State},
    response::{IntoResponse, Response},
};
use percy_client::{NewsItem, NewsQuery, NewsSubject};
use serde::Deserialize;

use crate::{
    comics::encode_query_value,
    releases::subject_href,
    session::Session,
    templates::{ChipRow, NewsRow, NewsTemplate, SourceOption, TickerItem},
    view::Viewer,
    AppState,
};

/// Stories per page. Percy caps `per_page` at 100; this is what the column has room for
/// before paging feels stingy, matching `/comics`' 24-card grid in spirit.
const PER_PAGE: i64 = 25;

/// Headlines in the ticker. Percy's own personalised head is capped at 12, so asking for
/// more would pad a "latest" strip with stories nobody following anything would see first.
const TICKER_LIMIT: i64 = 12;

/// §6.2's "the three most recent tagged stories".
const RELATED_LIMIT: u32 = 3;

/// The closed `media` set. Anything else is dropped rather than forwarded: Percy validates
/// the *pairing* of medium and subject type and answers `400`, which would turn a mistyped
/// URL into the outage empty state.
const MEDIA: [&str; 2] = ["comic", "title"];

/// Query string of the page. All optional; an absent `page` is `1`.
#[derive(Debug, Default, Deserialize)]
pub struct NewsParams {
    #[serde(default)]
    media: Option<String>,
    #[serde(default)]
    subject_type: Option<String>,
    #[serde(default)]
    subject_value: Option<String>,
    #[serde(default)]
    source: Option<String>,
    #[serde(default)]
    search: Option<String>,
    #[serde(default)]
    page: Option<i64>,
}

/// The filters currently in force, and the one place that knows how to put them in a URL.
///
/// A struct rather than five arguments for the reason `ComicBrowseQuery` is one: every field
/// is an optional string, so a positional `href(media, source, search, ...)` lets a caller
/// swap two of them and still compile, silently linking to the wrong filter.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
struct Filters {
    media: Option<String>,
    /// A pair: both or neither. Percy ignores a lone one, and so does this.
    subject_type: Option<String>,
    subject_value: Option<String>,
    source: Option<String>,
    search: String,
}

impl Filters {
    /// A `/news` href carrying these filters, omitting whatever is at its default so a plain
    /// "next page" link stays `?page=2` rather than `?media=&source=&search=&page=2`.
    fn href(&self, page: i64) -> String {
        let mut params: Vec<String> = Vec::new();
        for (key, value) in [
            ("media", self.media.as_deref()),
            ("subject_type", self.subject_type.as_deref()),
            ("subject_value", self.subject_value.as_deref()),
            ("source", self.source.as_deref()),
        ] {
            if let Some(value) = value {
                params.push(format!("{key}={}", encode_query_value(value)));
            }
        }
        if !self.search.is_empty() {
            params.push(format!("search={}", encode_query_value(&self.search)));
        }
        if page > 1 {
            params.push(format!("page={page}"));
        }
        if params.is_empty() {
            "/news".to_string()
        } else {
            format!("/news?{}", params.join("&"))
        }
    }
}

fn cleaned(value: Option<String>) -> Option<String> {
    value.map(|v| v.trim().to_string()).filter(|v| !v.is_empty())
}

/// `GET /news` — the stream.
pub async fn news_page(
    State(app): State<AppState>,
    session: Option<Session>,
    Query(params): Query<NewsParams>,
) -> Response {
    let media = cleaned(params.media).filter(|m| MEDIA.contains(&m.as_str()));
    // Both or neither: a page that clears its subject filter by blanking one input should
    // show everything, which is exactly what Percy does with a lone half.
    let (subject_type, subject_value) = match (cleaned(params.subject_type), cleaned(params.subject_value)) {
        (Some(t), Some(v)) => (Some(t), Some(v)),
        _ => (None, None),
    };
    let filters = Filters {
        media,
        subject_type,
        subject_value,
        source: cleaned(params.source),
        search: params.search.unwrap_or_default().trim().to_string(),
    };
    let page = params.page.filter(|p| *p > 0).unwrap_or(1);
    let discord_id = session.as_ref().map(|s| s.discord_user_id.clone());

    let (fetched, sources) = match &app.percy {
        Some(percy) => {
            let query = NewsQuery {
                media: filters.media.as_deref(),
                subject_type: filters.subject_type.as_deref(),
                subject_value: filters.subject_value.as_deref(),
                source: filters.source.as_deref(),
                search: (!filters.search.is_empty()).then_some(filters.search.as_str()),
                page: Some(page),
                per_page: Some(PER_PAGE),
                discord_id: discord_id.as_deref(),
                ..Default::default()
            };
            // The source list only fills a `<select>`; it must not add a round trip to the
            // page it decorates, and it must not take the page down when it fails.
            let (stream, sources) = tokio::join!(percy.news(&query), percy.news_sources(true));
            (
                stream.ok(),
                sources.map(|response| response.sources).unwrap_or_default(),
            )
        }
        None => (None, Vec::new()),
    };

    // An empty list has two causes that read identically and are not the same thing: nothing
    // matched, and Percy never answered. The first wants a different filter, the second wants
    // "try again" — telling someone to widen a filter that was never the problem is worse
    // than saying nothing.
    let unavailable = fetched.is_none();
    let data = fetched.unwrap_or_default();
    let (prev_page, next_page) = adjacent_pages(page, data.total_pages);

    NewsTemplate {
        account: session.as_ref().map(Viewer::from_session),
        signed_in: session.is_some(),
        rows: rows(&data.items, data.personalised),
        sources: sources
            .iter()
            .map(|source| SourceOption {
                selected: filters.source.as_deref() == Some(source.slug.as_str()),
                slug: source.slug.clone(),
                name: source.name.clone(),
            })
            .collect(),
        clear_href: Filters::default().href(1),
        prev_href: prev_page.map(|p| filters.href(p)),
        next_href: next_page.map(|p| filters.href(p)),
        media: filters.media.clone().unwrap_or_default(),
        subject_type: filters.subject_type.clone(),
        subject_value: filters.subject_value.clone(),
        source: filters.source.clone().unwrap_or_default(),
        search: filters.search.clone(),
        filtered: filters != Filters::default(),
        total: data.total,
        page,
        total_pages: data.total_pages,
        unavailable,
    }
    .into_response()
}

/// The ticker's headlines (§6.3), or an empty vec when there are none *or* the call failed.
///
/// Both collapse to the same answer on purpose: the partial renders nothing for an empty vec,
/// so a page whose news call failed is a page without a ticker rather than one with an empty
/// strip. Signed in, `discord_id` is what makes matching stories lead — the personalisation is
/// invisible in the markup, which is the point.
pub(crate) async fn ticker(app: &AppState, discord_id: Option<&str>) -> Vec<TickerItem> {
    let Some(percy) = &app.percy else {
        return Vec::new();
    };
    percy
        .news(&NewsQuery {
            per_page: Some(TICKER_LIMIT),
            discord_id,
            ..Default::default()
        })
        .await
        .map(|page| {
            page.items
                .iter()
                .map(|item| TickerItem {
                    url: item.url.clone(),
                    headline: item.headline.clone(),
                    source_name: item.source.name.clone(),
                })
                .collect()
        })
        .unwrap_or_default()
}

/// §6.2's related-news strip for one subject, empty when there is nothing.
///
/// Both detail pages come through here — a comic release by its series, a title by its
/// universe — because "the three most recent stories about this thing" is one question with
/// one answer shape. `subject_value` is the subject key verbatim: Percy normalises it on both
/// sides of the comparison, so the slug off the row is the right thing to send and a
/// prettified label would match nothing.
///
/// A failed call and "nothing tagged" both collapse to an empty vec, and the templates omit
/// the section entirely for one — §6.2 wants it absent rather than empty.
pub(crate) async fn related(app: &AppState, media: &str, subject_type: &str, subject_value: &str) -> Vec<NewsRow> {
    let Some(percy) = &app.percy else {
        return Vec::new();
    };
    percy
        .news_related(media, subject_type, subject_value, Some(RELATED_LIMIT))
        .await
        .map(|related| rows(&related.items, 0))
        .unwrap_or_default()
}

/// The `/news` href showing everything tagged with one subject — the related strip's
/// "more" link, and the only place the subject filter is reachable from.
pub(crate) fn subject_news_href(media: &str, subject_type: &str, subject_value: &str) -> String {
    Filters {
        media: Some(media.to_string()),
        subject_type: Some(subject_type.to_string()),
        subject_value: Some(subject_value.to_string()),
        ..Default::default()
    }
    .href(1)
}

/// Shapes stories into rows, marking the leading `personalised` of them as followed.
///
/// One list, as Percy sends it. The flag only lets the template put a quiet marker on the
/// stories a signed-in reader follows; it never reorders or splits anything.
fn rows(items: &[NewsItem], personalised: i64) -> Vec<NewsRow> {
    items
        .iter()
        .enumerate()
        .map(|(index, item)| row(item, (index as i64) < personalised))
        .collect()
}

fn row(item: &NewsItem, followed: bool) -> NewsRow {
    NewsRow {
        url: item.url.clone(),
        headline: item.headline.clone(),
        // Percy caps the excerpt on the way in; this is the whole of what §9 permits beyond
        // the headline, and a blank one is dropped rather than rendered as an empty line.
        excerpt: item
            .excerpt
            .as_deref()
            .map(str::trim)
            .filter(|text| !text.is_empty())
            .map(str::to_string),
        source_name: item.source.name.clone(),
        published: item.published_at.as_deref().map(published_date),
        published_iso: item.published_at.clone(),
        followed,
        chips: item.subjects.iter().map(chip).collect(),
    }
}

/// The date half of an ISO timestamp — `2026-08-05T09:30:00` becomes `2026-08-05`.
///
/// A news list is scanned, not read to the minute, and Percy stores naive UTC: printing a
/// time would be printing a timezone the reader does not live in.
fn published_date(published_at: &str) -> String {
    published_at.split('T').next().unwrap_or(published_at).to_string()
}

/// One subject chip, linked when a catalogue page for that subject exists.
///
/// Shares [`subject_href`] with the overview cards, so "where does a series chip go" has one
/// answer across the site — including the rule that a brand chip has to be uppercased for the
/// comic browse filter's exact match. A subject with no page renders as plain text; a chip
/// that looks like a link and goes nowhere is worse than a label.
fn chip(subject: &NewsSubject) -> ChipRow {
    ChipRow {
        // News tags carry no display label, only the opaque key. Showing the key is honest;
        // inventing a title-cased label would print something that is not what we filter on.
        label: subject.subject_value.clone(),
        href: subject_href(&subject.media, &subject.subject_type, &subject.subject_value),
    }
}

/// Previous/next page numbers, or `None` at either edge. `total_pages` of `0` (Percy
/// unreachable) yields neither, rather than a phantom "next" to nowhere.
fn adjacent_pages(page: i64, total_pages: i64) -> (Option<i64>, Option<i64>) {
    let prev = (page > 1).then_some(page - 1);
    let next = (total_pages > 0 && page < total_pages).then_some(page + 1);
    (prev, next)
}

#[cfg(test)]
mod tests {
    use super::*;
    use askama::Template;
    use percy_client::NewsItemSource;

    fn item(id: i64, headline: &str) -> NewsItem {
        NewsItem {
            id,
            url: format!("https://example.test/{id}"),
            headline: headline.to_string(),
            excerpt: Some("A short excerpt, as the feed gave it.".into()),
            // Present in the payload and never rendered — §9 allows headline, excerpt,
            // link and source, and nothing else.
            image_url: Some("https://example.test/hero.jpg".into()),
            author: Some("Someone Bylined".into()),
            published_at: Some("2026-08-05T09:30:00".into()),
            source: NewsItemSource {
                slug: "marvel".into(),
                name: "Marvel.com".into(),
                homepage: Some("https://marvel.com".into()),
            },
            subjects: vec![
                NewsSubject {
                    media: "comic".into(),
                    subject_type: "series".into(),
                    subject_value: "amazing-spider-man".into(),
                },
                NewsSubject {
                    media: "comic".into(),
                    subject_type: "creator".into(),
                    subject_value: "jonathan hickman".into(),
                },
            ],
            ..NewsItem::default()
        }
    }

    fn page(rows: Vec<NewsRow>, unavailable: bool, signed_in: bool) -> NewsTemplate {
        NewsTemplate {
            account: None,
            signed_in,
            rows,
            sources: vec![SourceOption {
                slug: "marvel".into(),
                name: "Marvel.com".into(),
                selected: false,
            }],
            clear_href: "/news".into(),
            prev_href: None,
            next_href: None,
            media: String::new(),
            subject_type: None,
            subject_value: None,
            source: String::new(),
            search: String::new(),
            filtered: false,
            total: 1,
            page: 1,
            total_pages: 1,
            unavailable,
        }
    }

    fn render(template: NewsTemplate) -> String {
        template.render().expect("the news template renders")
    }

    #[test]
    fn a_filter_href_omits_defaults_and_keeps_page_only_past_one() {
        assert_eq!(Filters::default().href(1), "/news");
        assert_eq!(Filters::default().href(3), "/news?page=3");
        assert_eq!(
            Filters {
                media: Some("comic".into()),
                search: "spider man".into(),
                ..Default::default()
            }
            .href(2),
            "/news?media=comic&search=spider+man&page=2"
        );
    }

    #[test]
    fn a_subject_href_carries_the_opaque_key_verbatim() {
        // The picker's key, not a label: posting "Amazing Spider-Man" would filter on a
        // subject Percy has never stored.
        assert_eq!(
            subject_news_href("comic", "series", "amazing-spider-man"),
            "/news?media=comic&subject_type=series&subject_value=amazing-spider-man"
        );
        // ...and a key with a space survives the round trip.
        assert_eq!(
            subject_news_href("comic", "creator", "jonathan hickman"),
            "/news?media=comic&subject_type=creator&subject_value=jonathan+hickman"
        );
    }

    #[test]
    fn only_the_leading_personalised_rows_are_marked_as_followed() {
        let shaped = rows(&[item(1, "One"), item(2, "Two"), item(3, "Three")], 2);
        assert_eq!(
            shaped.iter().map(|row| row.followed).collect::<Vec<_>>(),
            vec![true, true, false]
        );
        // Signed out, `personalised` is 0 and nothing is marked.
        assert!(rows(&[item(1, "One")], 0).iter().all(|row| !row.followed));
    }

    #[test]
    fn a_row_keeps_only_what_section_9_allows() {
        let row = row(&item(1, "Spider-Man returns"), false);
        assert_eq!(row.headline, "Spider-Man returns");
        assert_eq!(row.url, "https://example.test/1");
        assert_eq!(row.source_name, "Marvel.com");
        assert_eq!(row.published.as_deref(), Some("2026-08-05"));
        assert!(row.excerpt.is_some());
    }

    #[test]
    fn a_subject_chip_links_where_a_catalogue_page_exists_and_is_text_otherwise() {
        let row = row(&item(1, "Anything"), false);
        assert_eq!(row.chips[0].href.as_deref(), Some("/comics/series/amazing-spider-man"));
        assert_eq!(row.chips[1].href, None, "no creator page exists");
        assert_eq!(row.chips[1].label, "jonathan hickman");
    }

    #[test]
    fn adjacent_pages_has_no_phantom_next_when_percy_is_down() {
        assert_eq!(adjacent_pages(1, 0), (None, None));
        assert_eq!(adjacent_pages(2, 5), (Some(1), Some(3)));
        assert_eq!(adjacent_pages(5, 5), (Some(4), None));
    }

    #[test]
    fn signed_out_the_page_lists_stories_with_their_source_and_a_link_out() {
        let html = render(page(rows(&[item(1, "Spider-Man returns")], 0), false, false));

        assert!(html.contains("Spider-Man returns"));
        // Every story names its source and links the original — §9 makes both a condition
        // of showing it at all.
        assert!(html.contains("Marvel.com"));
        assert!(html.contains(r#"href="https://example.test/1""#));
        // ...and nothing beyond headline + excerpt + link + source is rendered, however
        // much of it the payload happens to carry.
        assert!(!html.contains("hero.jpg"), "no rehosted or hotlinked image (§9)");
        assert!(!html.contains("Someone Bylined"), "no byline (§9)");
        // The filters are a plain GET form, so the page works with JS off.
        assert!(html.contains(r#"<form class="nx-filters" method="get" action="/news""#));
        assert!(!html.contains("nx-row__followed"), "nothing is followed signed out");
    }

    #[test]
    fn signed_in_the_followed_stories_lead_the_one_list_and_are_marked() {
        let html = render(page(
            rows(&[item(1, "The one I follow"), item(2, "General latest")], 1),
            false,
            true,
        ));

        let followed = html.find("The one I follow").expect("the followed story renders");
        let latest = html.find("General latest").expect("the general story renders");
        assert!(followed < latest, "what the viewer follows leads the list");
        // One list, not two: the marker is a badge on a row, not a second section.
        assert_eq!(html.matches("nx-row__followed").count(), 1);
        assert_eq!(html.matches(r#"class="nx-list""#).count(), 1);
    }

    #[test]
    fn percy_down_is_a_stated_empty_state_rather_than_a_bare_empty_list() {
        let html = render(page(Vec::new(), true, false));
        assert!(html.contains("Try again in a moment."));
        // ...and not the "nothing matched" wording, which would send someone off adjusting
        // a filter that was never the problem.
        assert!(!html.contains("No stories match"));
    }

    #[test]
    fn an_empty_result_is_a_different_empty_state_from_an_outage() {
        let mut empty = page(Vec::new(), false, false);
        empty.total = 0;
        let html = render(empty);
        assert!(html.contains("No stories match"));
        assert!(!html.contains("Try again in a moment."));
    }
}
