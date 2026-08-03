//! Public watch-order pages — the universe index and one universe's ordered catalogue.
//!
//! These are the only Percy pages that are fully useful logged out: no session, no
//! JS state, no progress. Everything is server-rendered from a single Percy call
//! (`get_watch_universe` carries the universe, its paths, its eras and every title
//! with that region's providers), which is what makes the page indexable.
//!
//! Percy owns the catalogue and its caching; this module only shapes rows for the
//! template. When Percy is unreachable the pages degrade to a stated empty state
//! rather than a 500 — a watch order that renders "unavailable" is still a page.

use axum::{
    extract::{Path, Query, State},
    response::{IntoResponse, Response},
};
use percy_client::{WatchTitle, WatchUniverse};
use serde::Deserialize;

use crate::{
    session::Session,
    templates::{OrderEra, OrderProvider, OrderRow, WatchOrderTemplate, WatchlistTemplate},
    view::Viewer,
    AppState,
};

/// TMDB image CDN. Sizes are fixed buckets, not arbitrary dimensions.
const TMDB_IMAGE_BASE: &str = "https://image.tmdb.org/t/p";

/// Fallback region when the visitor has expressed no preference.
const DEFAULT_REGION: &str = "US";

/// Query string of the order page. All three are optional; Percy applies its own
/// defaults (the universe's default path, `story` ordering) when they are absent.
#[derive(Debug, Default, Deserialize)]
pub struct OrderQuery {
    #[serde(default)]
    path: Option<String>,
    #[serde(default)]
    region: Option<String>,
    #[serde(default)]
    sort: Option<String>,
}

fn viewer(session: &Option<Session>) -> Option<Viewer> {
    session.as_ref().map(Viewer::from_session)
}

/// `GET /watchlist` — every published universe as a card.
pub async fn watchlist_index(State(app): State<AppState>, session: Option<Session>) -> Response {
    let universes = match &app.percy {
        Some(percy) => percy
            .list_watch_universes()
            .await
            .map(|response| response.universes)
            .unwrap_or_default(),
        None => Vec::new(),
    };

    WatchlistTemplate {
        account: viewer(&session),
        universes,
    }
    .into_response()
}

/// `GET /watchlist/:slug` — one universe's ordered catalogue.
pub async fn watch_order(
    State(app): State<AppState>,
    session: Option<Session>,
    Path(slug): Path<String>,
    Query(query): Query<OrderQuery>,
) -> Response {
    let region = query.region.as_deref().unwrap_or(DEFAULT_REGION).to_string();
    let sort = match query.sort.as_deref() {
        Some("release") => "release",
        _ => "story",
    };

    let page = match &app.percy {
        Some(percy) => percy
            .get_watch_universe(&slug, query.path.as_deref(), &region, sort)
            .await
            .ok(),
        None => None,
    };

    // A missing page is rendered as the template's empty state rather than a 404:
    // the universe may well exist and Percy simply be down, and an error page would
    // lose the visitor entirely.
    let Some(page) = page else {
        return WatchOrderTemplate {
            account: viewer(&session),
            universe: WatchUniverse {
                slug: slug.clone(),
                name: slug,
                ..WatchUniverse::default()
            },
            paths: Vec::new(),
            eras: Vec::new(),
            path: String::new(),
            region,
            sort: sort.to_string(),
            sort_label: sort_label(sort).to_string(),
            total: 0,
            hours: 0,
            essential: 0,
        }
        .into_response();
    };

    let total = page.titles.len();
    let runtime: i64 = page.titles.iter().map(|title| title.runtime).sum();
    let essential = page
        .titles
        .iter()
        .filter(|title| title.importance.as_deref() == Some("essential"))
        .count();

    WatchOrderTemplate {
        account: viewer(&session),
        eras: group_into_eras(&page.titles, &page.sort),
        path: page.path.clone().unwrap_or_default(),
        paths: page.paths,
        region: page.region,
        sort_label: sort_label(&page.sort).to_string(),
        sort: page.sort,
        universe: page.universe,
        total,
        hours: runtime / 60,
        essential,
    }
    .into_response()
}

fn sort_label(sort: &str) -> &'static str {
    match sort {
        "release" => "release order",
        _ => "story order",
    }
}

/// Groups the already-ordered titles into sections.
///
/// Eras are a *release-order* concept: the MCU phases are contiguous only when the
/// films are in the order they came out. Story order deliberately interleaves them --
/// grouping by era there produced twelve sections with "Phase 1" appearing three
/// times, which reads as a bug rather than as chronology. So story order renders one
/// unlabelled section and each row carries its era as a chip instead.
///
/// In release order the walk starts a new section whenever the era key changes, which
/// is also what keeps a universe that genuinely revisits an era honest.
fn group_into_eras(titles: &[WatchTitle], sort: &str) -> Vec<OrderEra> {
    if sort != "release" {
        return vec![OrderEra {
            key: String::new(),
            label: String::new(),
            rows: titles
                .iter()
                .enumerate()
                .map(|(index, title)| order_row(title, index + 1))
                .collect(),
        }];
    }

    let mut sections: Vec<OrderEra> = Vec::new();
    for (index, title) in titles.iter().enumerate() {
        let key = title.era.clone().unwrap_or_default();
        if sections.last().map(|section| section.key != key).unwrap_or(true) {
            sections.push(OrderEra {
                label: era_label(&key),
                key,
                rows: Vec::new(),
            });
        }
        if let Some(section) = sections.last_mut() {
            // No era chip here: the section heading right above already says it.
            let mut row = order_row(title, index + 1);
            row.era = None;
            section.rows.push(row);
        }
    }
    sections
}

/// A human label for an era key. Keys are curation slugs (`phase1`), so `phase1`
/// becomes `Phase 1`; anything else is title-cased as-is.
fn era_label(key: &str) -> String {
    if key.is_empty() {
        return "Also in this universe".to_string();
    }
    if let Some(number) = key.strip_prefix("phase") {
        if number.chars().all(|c| c.is_ascii_digit()) && !number.is_empty() {
            return format!("Phase {number}");
        }
    }
    let mut chars = key.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
        None => key.to_string(),
    }
}

fn order_row(title: &WatchTitle, position: usize) -> OrderRow {
    OrderRow {
        position,
        slug: title.slug.clone(),
        title: title.title.clone(),
        year: title
            .release_date
            .as_deref()
            .and_then(|date| date.get(..4))
            .map(str::to_string),
        runtime_label: runtime_label(title),
        rating: title.tmdb_rating.map(|rating| format!("{rating:.1}")),
        poster_url: title
            .poster_path
            .as_deref()
            .map(|path| format!("{TMDB_IMAGE_BASE}/w92{path}")),
        era: title.era.as_deref().map(era_label),
        importance: title.importance.clone(),
        instruction: title.instruction.clone(),
        milestone: title.milestone,
        is_show: title.kind == "tv",
        providers: providers(title),
    }
}

/// `2h 06m` for a film, `6 episodes · 5h 00m` for a series.
fn runtime_label(title: &WatchTitle) -> String {
    let duration = if title.runtime > 0 {
        format!("{}h {:02}m", title.runtime / 60, title.runtime % 60)
    } else {
        String::new()
    };
    match title.episodes {
        Some(episodes) if episodes > 0 && !duration.is_empty() => {
            format!("{episodes} episodes · {duration}")
        }
        Some(episodes) if episodes > 0 => format!("{episodes} episodes"),
        _ => duration,
    }
}

/// Streaming providers only, deduplicated by provider.
///
/// Percy returns one row per offer type, so a service that both rents and sells a
/// film appears twice; the row only has space for a handful of logos, so this keeps
/// subscription (`flatrate`) offers — the ones that answer "can I just watch this?"
fn providers(title: &WatchTitle) -> Vec<OrderProvider> {
    let mut seen: Vec<i64> = Vec::new();
    let mut out = Vec::new();
    for provider in title.providers.iter().filter(|p| p.offer == "flatrate") {
        if seen.contains(&provider.provider_id) {
            continue;
        }
        seen.push(provider.provider_id);
        if let Some(logo) = provider.logo_path.as_deref() {
            out.push(OrderProvider {
                name: provider.provider_name.clone(),
                logo_url: format!("{TMDB_IMAGE_BASE}/w45{logo}"),
            });
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use percy_client::WatchProvider;

    fn title(slug: &str, era: Option<&str>) -> WatchTitle {
        WatchTitle {
            slug: slug.to_string(),
            title: slug.to_string(),
            era: era.map(str::to_string),
            runtime: 126,
            ..WatchTitle::default()
        }
    }

    #[test]
    fn release_order_groups_titles_into_era_sections() {
        let titles = vec![
            title("a", Some("phase1")),
            title("b", Some("phase1")),
            title("c", Some("phase2")),
        ];
        let sections = group_into_eras(&titles, "release");

        assert_eq!(sections.len(), 2);
        assert_eq!(sections[0].label, "Phase 1");
        assert!(sections[0].rows[0].era.is_none(), "the heading already names the era");
        assert_eq!(sections[1].label, "Phase 2");
        // Positions are the position in the whole order, not within the era.
        assert_eq!(sections[1].rows[0].position, 3);
    }

    #[test]
    fn story_order_is_one_flat_section_with_the_era_on_each_row() {
        let titles = vec![
            title("a", Some("phase1")),
            title("b", Some("phase3")),
            title("c", Some("phase1")),
        ];
        let sections = group_into_eras(&titles, "story");

        // One section: the MCU story order revisits phases constantly, and a heading
        // per switch would repeat "Phase 1" three times for these three films.
        assert_eq!(sections.len(), 1);
        assert!(sections[0].label.is_empty());
        assert_eq!(sections[0].rows.len(), 3);
        assert_eq!(sections[0].rows[1].era.as_deref(), Some("Phase 3"));
    }

    #[test]
    fn a_revisited_era_keeps_its_place_in_release_order() {
        let titles = vec![
            title("a", Some("phase1")),
            title("b", Some("phase2")),
            title("c", Some("phase1")),
        ];
        let sections = group_into_eras(&titles, "release");

        // Three sections, not two: hoisting "c" up to the first Phase 1 block would
        // silently reorder the watch order the curation defined.
        assert_eq!(sections.len(), 3);
        assert_eq!(sections[2].rows[0].slug, "c");
    }

    #[test]
    fn titles_without_an_era_get_one_trailing_section() {
        let sections = group_into_eras(&[title("a", None), title("b", None)], "release");
        assert_eq!(sections.len(), 1);
        assert_eq!(sections[0].label, "Also in this universe");
    }

    #[test]
    fn era_label_falls_back_to_the_key() {
        assert_eq!(era_label("phase12"), "Phase 12");
        assert_eq!(era_label("infinity-saga"), "Infinity-saga");
        assert_eq!(era_label("phaseX"), "PhaseX");
    }

    #[test]
    fn runtime_label_covers_films_and_series() {
        let mut film = title("a", None);
        film.runtime = 181;
        assert_eq!(runtime_label(&film), "3h 01m");

        let mut series = title("b", None);
        series.runtime = 300;
        series.episodes = Some(6);
        assert_eq!(runtime_label(&series), "6 episodes · 5h 00m");

        let mut unknown = title("c", None);
        unknown.runtime = 0;
        assert_eq!(runtime_label(&unknown), "");
    }

    #[test]
    fn providers_keep_one_logo_per_subscription_service() {
        let mut entry = title("a", None);
        entry.providers = vec![
            WatchProvider {
                provider_id: 337,
                provider_name: "Disney Plus".into(),
                logo_path: Some("/d.jpg".into()),
                offer: "flatrate".into(),
            },
            WatchProvider {
                provider_id: 337,
                provider_name: "Disney Plus".into(),
                logo_path: Some("/d.jpg".into()),
                offer: "rent".into(),
            },
            WatchProvider {
                provider_id: 3,
                provider_name: "Google Play".into(),
                logo_path: Some("/g.jpg".into()),
                offer: "rent".into(),
            },
        ];

        let logos = providers(&entry);
        assert_eq!(logos.len(), 1, "rent/buy duplicates must not reach the row");
        assert_eq!(logos[0].name, "Disney Plus");
        assert_eq!(logos[0].logo_url, "https://image.tmdb.org/t/p/w45/d.jpg");
    }
}
