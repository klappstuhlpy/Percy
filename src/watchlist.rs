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
    http::StatusCode,
    response::{IntoResponse, Response},
};
use percy_client::{PercyError, WatchPersonCredit, WatchTitle, WatchUniverse};
use serde::Deserialize;

use crate::{
    session::Session,
    templates::{
        OrderEra, OrderProvider, OrderRow, PersonCreditRow, PersonTemplate, WatchOrderTemplate, WatchlistTemplate,
    },
    view::Viewer,
    AppState,
};

/// TMDB image CDN. Sizes are fixed buckets, not arbitrary dimensions.
const TMDB_IMAGE_BASE: &str = "https://image.tmdb.org/t/p";

/// How many streaming logos a row has room for before it starts wrapping.
const MAX_PROVIDER_LOGOS: usize = 4;

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
            movies: 0,
            shows: 0,
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
    let movies = page.titles.iter().filter(|title| title.kind == "movie").count();
    let shows = page.titles.iter().filter(|title| title.kind == "tv").count();

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
        movies,
        shows,
    }
    .into_response()
}

/// `GET /people/:slug` — one person: what they have coming, then their filmography.
///
/// Same soft-404 split the comic pages make: an unknown slug is a real 404, a backend
/// that is down is a 200 empty state. A page that answered 200 to any invented name
/// would get indexed as a real person.
pub async fn person_page(State(app): State<AppState>, session: Option<Session>, Path(slug): Path<String>) -> Response {
    let fetched = match &app.percy {
        Some(percy) => percy.get_watch_person(&slug).await,
        None => Err(PercyError::Api("percy is not configured".into())),
    };

    let page = match fetched {
        Ok(page) => page,
        Err(error) => {
            let body = PersonTemplate {
                account: viewer(&session),
                name: slug.clone(),
                slug,
                photo_url: None,
                roles: String::new(),
                credit_count: 0,
                universes: 0,
                upcoming: Vec::new(),
                filmography: Vec::new(),
            };
            let status = match error {
                PercyError::NotFound => StatusCode::NOT_FOUND,
                _ => StatusCode::OK,
            };
            return (status, body).into_response();
        }
    };

    let today = today_iso();
    let (upcoming, filmography): (Vec<_>, Vec<_>) = page
        .credits
        .iter()
        .partition(|credit| is_upcoming(credit.title.release_date.as_deref(), &today));

    let mut universes: Vec<&str> = page.credits.iter().map(|c| c.title.universe.as_str()).collect();
    universes.sort_unstable();
    universes.dedup();

    PersonTemplate {
        account: viewer(&session),
        name: page.person.name,
        slug: page.person.slug,
        photo_url: page
            .person
            .profile_path
            .as_deref()
            .map(|path| format!("{TMDB_IMAGE_BASE}/w185{path}")),
        roles: role_summary(&page.credits),
        credit_count: page.credits.len(),
        universes: universes.len(),
        upcoming: upcoming.into_iter().map(person_row).collect(),
        filmography: filmography.into_iter().map(person_row).collect(),
    }
    .into_response()
}

/// Today as `YYYY-MM-DD`, matching the format TMDB release dates come in.
///
/// Uses the `time` crate the cookie layer already pulls in rather than adding a date
/// dependency for one comparison. ISO dates compare correctly as strings, which is why
/// no parsing happens anywhere on this path.
fn today_iso() -> String {
    cookie::time::OffsetDateTime::now_utc().date().to_string()
}

/// A title counts as upcoming when it has no date yet *or* its date is still ahead.
///
/// The undated case is the important one: an announced-but-unscheduled film is exactly
/// what somebody following a person came to see, and it must not sink into the
/// filmography just because TMDB has no date for it.
fn is_upcoming(release_date: Option<&str>, today: &str) -> bool {
    match release_date {
        None => true,
        Some(date) => date.is_empty() || date > today,
    }
}

/// `Actor · Director` — the distinct roles this person is credited with, in a fixed order
/// so the same person never reads differently on two visits.
fn role_summary(credits: &[WatchPersonCredit]) -> String {
    ["cast", "director", "writer", "creator"]
        .into_iter()
        .filter(|role| credits.iter().any(|credit| credit.role == *role))
        .map(role_noun)
        .collect::<Vec<_>>()
        .join(" · ")
}

fn role_noun(role: &str) -> &'static str {
    match role {
        "cast" => "Actor",
        "director" => "Director",
        "writer" => "Writer",
        "creator" => "Creator",
        _ => "Credited",
    }
}

fn person_row(credit: &WatchPersonCredit) -> PersonCreditRow {
    let title = &credit.title;
    let kind_label = match title.kind.as_str() {
        "tv" => "TV",
        "special" => "SPEC",
        _ => "FILM",
    };
    PersonCreditRow {
        universe_slug: title.universe.clone(),
        universe_name: credit.universe_name.clone(),
        accent: credit.universe_accent.clone(),
        slug: title.slug.clone(),
        title: title.title.clone(),
        year: title
            .release_date
            .as_deref()
            .and_then(|date| date.get(..4))
            .map(str::to_string),
        kind_label: kind_label.to_string(),
        // The character is the interesting half of an acting credit; the bare role is
        // all there is for crew, and for an actor TMDB left uncredited.
        role_label: credit
            .character_name
            .clone()
            .filter(|name| !name.is_empty())
            .unwrap_or_else(|| role_noun(&credit.role).to_string()),
        poster_url: title
            .poster_path
            .as_deref()
            .map(|path| format!("{TMDB_IMAGE_BASE}/w154{path}")),
    }
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
    let (kind_class, kind_label) = match title.kind.as_str() {
        "tv" => ("tv", "TV"),
        "special" => ("special", "SPEC"),
        _ => ("movie", "FILM"),
    };
    OrderRow {
        position,
        number: format!("{position:02}"),
        kind_class: kind_class.to_string(),
        kind_label: kind_label.to_string(),
        slug: title.slug.clone(),
        title: title.title.clone(),
        year: title
            .release_date
            .as_deref()
            .and_then(|date| date.get(..4))
            .map(str::to_string),
        runtime_label: runtime_label(title),
        rating: title.tmdb_rating.map(|rating| format!("{rating:.1}")),
        era: title.era.as_deref().map(era_label),
        importance: title.importance.clone(),
        instruction: title.instruction.clone(),
        studio: title.sub_universe.clone(),
        milestone: title.milestone,
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
        if out.len() >= MAX_PROVIDER_LOGOS {
            break;
        }
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
    fn rows_carry_a_padded_number_and_their_kind() {
        let mut show = title("loki", None);
        show.kind = "tv".into();

        let film_row = order_row(&title("iron-man", None), 3);
        assert_eq!(film_row.number, "03");
        assert_eq!(
            (film_row.kind_class.as_str(), film_row.kind_label.as_str()),
            ("movie", "FILM")
        );

        let show_row = order_row(&show, 12);
        assert_eq!(show_row.number, "12");
        assert_eq!(
            (show_row.kind_class.as_str(), show_row.kind_label.as_str()),
            ("tv", "TV")
        );
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

    fn credit(role: &str, character: Option<&str>, release_date: Option<&str>) -> WatchPersonCredit {
        WatchPersonCredit {
            role: role.to_string(),
            character_name: character.map(str::to_string),
            universe_name: "Marvel Cinematic Universe".into(),
            universe_accent: "#E62429".into(),
            title: WatchTitle {
                universe: "mcu".into(),
                slug: "iron-man".into(),
                title: "Iron Man".into(),
                release_date: release_date.map(str::to_string),
                ..WatchTitle::default()
            },
            ..WatchPersonCredit::default()
        }
    }

    #[test]
    fn an_undated_title_is_upcoming_forever() {
        // The load-bearing case: TMDB announces long before it schedules, and the
        // announced film is exactly what a follower came to the page for.
        assert!(is_upcoming(None, "2026-08-04"));
        assert!(is_upcoming(Some(""), "2026-08-04"));
    }

    #[test]
    fn upcoming_is_decided_by_the_date_alone() {
        assert!(is_upcoming(Some("2026-12-18"), "2026-08-04"));
        assert!(!is_upcoming(Some("2008-05-02"), "2026-08-04"));
        // Released *today* is not upcoming -- it is out.
        assert!(!is_upcoming(Some("2026-08-04"), "2026-08-04"));
    }

    #[test]
    fn today_is_an_iso_date() {
        // is_upcoming compares strings, so the clock must produce exactly TMDB's format.
        let today = today_iso();
        assert_eq!(today.len(), 10, "{today}");
        assert_eq!(today.as_bytes()[4], b'-');
        assert_eq!(today.as_bytes()[7], b'-');
    }

    #[test]
    fn role_summary_lists_distinct_roles_in_a_fixed_order() {
        let credits = vec![
            credit("writer", None, None),
            credit("cast", Some("Tony Stark"), None),
            credit("cast", Some("Tony Stark"), None),
        ];
        assert_eq!(role_summary(&credits), "Actor \u{b7} Writer");
        assert_eq!(role_summary(&[]), "");
    }

    #[test]
    fn a_row_shows_the_character_for_acting_and_the_role_for_crew() {
        assert_eq!(
            person_row(&credit("cast", Some("Tony Stark"), None)).role_label,
            "Tony Stark"
        );
        assert_eq!(person_row(&credit("director", None, None)).role_label, "Director");
        // An uncredited actor has no character: the role noun is all there is to say.
        assert_eq!(person_row(&credit("cast", Some(""), None)).role_label, "Actor");
    }

    #[test]
    fn the_person_page_renders_both_sections() {
        use askama::Template;

        let page = PersonTemplate {
            account: None,
            name: "Robert Downey Jr.".into(),
            slug: "robert-downey-jr".into(),
            photo_url: Some("https://image.tmdb.org/t/p/w185/rdj.jpg".into()),
            roles: "Actor".into(),
            credit_count: 2,
            universes: 1,
            upcoming: vec![person_row(&credit("cast", Some("Doctor Doom"), None))],
            filmography: vec![person_row(&credit("cast", Some("Tony Stark"), Some("2008-05-02")))],
        };
        let html = page.render().expect("the person template renders");

        assert!(html.contains("Coming up"));
        assert!(html.contains("Filmography"));
        assert!(html.contains("Doctor Doom"), "the macro body reaches the loop variable");
        assert!(html.contains("href=\"/watchlist/mcu/iron-man\""));
        assert!(html.contains("2008"), "a released title keeps its year");
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
