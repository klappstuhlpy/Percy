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
use percy_client::{PercyError, ProgressMap, WatchCredit, WatchPersonCredit, WatchTitle, WatchUniverse};
use serde::Deserialize;

use crate::{
    progress,
    session::Session,
    templates::{
        OrderEra, OrderProvider, OrderRow, PersonCreditRow, PersonTemplate, ProgressSummary, ProviderGroup,
        RegionOption, TitleCredit, TitleNeighbour, TrailerView, WatchOrderTemplate, WatchTitleTemplate,
        WatchlistTemplate,
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

/// Offer types on the detail page, in the order they are worth reading: what a
/// subscription already covers first, then what costs money.
const OFFERS: &[(&str, &str)] = &[("flatrate", "Stream"), ("rent", "Rent"), ("buy", "Buy")];

/// The regions the switcher offers. Percy accepts any ISO 3166-1 alpha-2 code; this is
/// only what the `<select>` lists, kept short because a 200-row dropdown is not a control.
const REGIONS: &[(&str, &str)] = &[
    ("US", "United States"),
    ("GB", "United Kingdom"),
    ("DE", "Germany"),
    ("FR", "France"),
    ("ES", "Spain"),
    ("IT", "Italy"),
    ("CA", "Canada"),
    ("AU", "Australia"),
    ("BR", "Brazil"),
    ("JP", "Japan"),
];

/// Cast size above which the section's `<details>` starts closed. It is a `<details>`
/// either way, so the only thing this decides is whether it renders already open.
const CAST_SHOWN: usize = 10;

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

/// Percy's "not found" becomes ours; every other failure keeps its 200.
///
/// The same split `comics.rs` makes, and for the same reason: a public catalogue page that
/// answers 200 to any URL a crawler invents is a soft 404 and gets indexed as a real page.
/// A backend outage is different — the title probably exists, so the page says "try again".
fn status_for(error: &PercyError) -> StatusCode {
    match error {
        PercyError::NotFound => StatusCode::NOT_FOUND,
        _ => StatusCode::OK,
    }
}

/// `GET /watchlist` — every published universe as a card.
pub async fn watchlist_index(State(app): State<AppState>, session: Option<Session>) -> Response {
    let discord_id = session.as_ref().map(|s| s.discord_user_id.clone());
    // Both calls go out together: the ticker must not add a round trip to the page it
    // decorates, and must not take it down when it fails.
    let (universes, ticker) = match &app.percy {
        Some(percy) => {
            let (universes, ticker) = tokio::join!(
                percy.list_watch_universes(),
                crate::news::ticker(&app, discord_id.as_deref())
            );
            (universes.map(|response| response.universes).unwrap_or_default(), ticker)
        }
        None => (Vec::new(), Vec::new()),
    };

    WatchlistTemplate {
        account: viewer(&session),
        universes,
        ticker,
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

    // The marks go out with the order, not after it: a public catalogue page must not pay
    // a second round trip for its decoration.
    let (fetched, marks) = match &app.percy {
        Some(percy) => tokio::join!(
            percy.get_watch_universe(&slug, query.path.as_deref(), &region, sort),
            progress::fetch(&app, &session, "title")
        ),
        None => (
            Err(PercyError::Api("percy is not configured".into())),
            ProgressMap::default(),
        ),
    };

    // A missing page is rendered as the template's empty state rather than a 404:
    // the universe may well exist and Percy simply be down, and an error page would
    // lose the visitor entirely.
    let Ok(page) = fetched else {
        return WatchOrderTemplate {
            account: viewer(&session),
            universe: WatchUniverse {
                slug: slug.clone(),
                name: slug,
                ..WatchUniverse::default()
            },
            paths: Vec::new(),
            eras: Vec::new(),
            progress: None,
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

    let path = page.path.clone().unwrap_or_default();
    let eras = group_into_eras(
        &page.titles,
        &page.sort,
        &Marks {
            marks: &marks,
            signed_in: session.is_some(),
            universe: &page.universe.slug,
            // A JS-off mark comes back to this exact ordering — landing on the default
            // path in the default region would renumber the list under the visitor.
            here: &order_href(&page.universe.slug, &path, &page.region, &page.sort),
        },
    );
    // Counted over the keys actually rendered, so a mark for a title this path leaves
    // out can never push the count past the list. Signed out there is no bar at all.
    let (done, seen) = progress::summary(
        &marks,
        "title",
        eras.iter()
            .flat_map(|era| era.rows.iter())
            .map(|row| row.toggle.release_key.as_str()),
    );

    WatchOrderTemplate {
        account: viewer(&session),
        progress: ProgressSummary::new(session.is_some(), done, seen, "watched"),
        eras,
        path,
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

/// `GET /watchlist/:universe/:slug` — one title as a destination of its own (§6.2).
///
/// Same soft-404 split as the comic detail page, and for the same reason: an unknown slug is
/// a real 404 so a crawler never indexes an invented URL, while a Percy that is down keeps
/// its 200 and a stated empty state, because the title probably exists.
///
/// Two calls go out together — the detail itself and the universe's related news — so the
/// news strip never costs the page a round trip, and a news outage costs it a section rather
/// than the page.
pub async fn watch_title(
    State(app): State<AppState>,
    session: Option<Session>,
    Path((universe, slug)): Path<(String, String)>,
    Query(query): Query<OrderQuery>,
) -> Response {
    let region = normalise_region(query.region.as_deref());
    let sort = match query.sort.as_deref() {
        Some("release") => "release",
        _ => "story",
    };

    let (fetched, related_news, marks) = match &app.percy {
        Some(percy) => tokio::join!(
            percy.get_watch_title(&universe, &slug, &region, sort),
            crate::news::related(&app, "title", "universe", &universe),
            progress::fetch(&app, &session, "title")
        ),
        None => (
            Err(PercyError::Api("percy is not configured".into())),
            Vec::new(),
            ProgressMap::default(),
        ),
    };
    let signed_in = session.is_some();
    // This page, carrying the ordering it was reached in, so a JS-off mark does not drop
    // the visitor back onto a differently sorted list.
    let link_query = format!("?sort={sort}&region={region}");

    let detail = match fetched {
        Ok(detail) => detail,
        Err(error) => {
            let body = WatchTitleTemplate {
                universe: WatchUniverse {
                    slug: universe.clone(),
                    name: universe.clone(),
                    ..WatchUniverse::default()
                },
                order_href: format!("/watchlist/{universe}"),
                related_news_href: crate::news::subject_news_href("title", "universe", &universe),
                toggle: progress::toggle(
                    &marks,
                    signed_in,
                    "title",
                    &title_key(&universe, &slug),
                    &format!("/watchlist/{universe}/{slug}{link_query}"),
                ),
                slug,
                region,
                sort_label: sort_label(sort).to_string(),
                ..empty_title_page(viewer(&session))
            };
            return (status_for(&error), body).into_response();
        }
    };

    let title = detail.title;
    let path = detail.path.clone().unwrap_or_default();
    let path_name = detail.path.as_deref().filter(|slug| !slug.is_empty()).map(path_label);
    let (cast, crew): (Vec<_>, Vec<_>) = detail.credits.iter().partition(|credit| credit.role == "cast");

    WatchTitleTemplate {
        account: viewer(&session),
        // Percy's own universe slug and the title's own slug: the same pair the order
        // row files its mark under, so the two surfaces mark one row rather than two.
        toggle: progress::toggle(
            &marks,
            signed_in,
            "title",
            &title_key(&detail.universe.slug, &title.slug),
            &format!("/watchlist/{}/{}{link_query}", detail.universe.slug, title.slug),
        ),
        year: year_of(title.release_date.as_deref()),
        kind_label: kind_noun(&title.kind).to_string(),
        backdrop_url: title
            .backdrop_path
            .as_deref()
            .map(|path| format!("{TMDB_IMAGE_BASE}/w1280{path}")),
        poster_url: title
            .poster_path
            .as_deref()
            .map(|path| format!("{TMDB_IMAGE_BASE}/w342{path}")),
        context: non_empty(title.context.as_deref()),
        overview: non_empty(title.overview.as_deref()),
        runtime_label: runtime_label(&title),
        rating: title.tmdb_rating.map(|rating| format!("{rating:.1}")),
        votes: title.tmdb_votes.filter(|votes| *votes > 0),
        release_date: non_empty(title.release_date.as_deref()),
        studio: non_empty(title.sub_universe.as_deref()),
        era: title.era.as_deref().filter(|key| !key.is_empty()).map(era_label),
        position: detail.position,
        total: detail.total,
        importance: non_empty(title.importance.as_deref()),
        instruction: non_empty(title.instruction.as_deref()),
        milestone: title.milestone,
        sort_label: sort_label(&detail.sort).to_string(),
        order_href: order_href(&detail.universe.slug, &path, &region, &detail.sort),
        link_query: format!("?sort={}&region={region}", detail.sort),
        previous: detail.previous.as_ref().map(neighbour),
        next: detail.next.as_ref().map(neighbour),
        credits_of: detail.credits_of.as_ref().map(neighbour),
        regions: region_options(&region),
        provider_groups: provider_groups(&title),
        cast_collapsed: cast.len() > CAST_SHOWN,
        cast: cast.into_iter().map(title_credit).collect(),
        crew: crew.into_iter().map(title_credit).collect(),
        related_news,
        related_news_href: crate::news::subject_news_href("title", "universe", &detail.universe.slug),
        trailer: title.trailer.as_ref().map(|trailer| TrailerView {
            label: non_empty(trailer.name.as_deref()).unwrap_or_else(|| "Trailer".to_string()),
            site: trailer.site.clone(),
            url: trailer.url.clone(),
            embed_url: trailer.embed_url.clone(),
        }),
        // The one field the curation writes for people who have already seen it. No
        // spoiler note, no section — this page never invents one.
        spoiler: non_empty(title.spoiler.as_deref()),
        signed_in,
        path_name,
        region,
        universe: detail.universe,
        title: title.title,
        slug: title.slug,
    }
    .into_response()
}

/// The empty-state page, which every field-by-field failure path starts from.
///
/// `title` empty is the template's "nothing to show" signal, exactly as on the comic
/// detail page; the caller fills back in whatever it does know (the universe, the region
/// it asked for) so the shell still reads as *this* page rather than a generic error.
fn empty_title_page(account: Option<Viewer>) -> WatchTitleTemplate {
    WatchTitleTemplate {
        account,
        universe: WatchUniverse::default(),
        title: String::new(),
        slug: String::new(),
        // The empty state renders no action row at all, so this is only here to make the
        // struct whole; every caller that has a real key overrides it.
        toggle: progress::toggle(&ProgressMap::default(), false, "title", "", ""),
        year: None,
        kind_label: String::new(),
        backdrop_url: None,
        poster_url: None,
        context: None,
        overview: None,
        runtime_label: String::new(),
        rating: None,
        votes: None,
        release_date: None,
        studio: None,
        era: None,
        position: 0,
        total: 0,
        path_name: None,
        importance: None,
        instruction: None,
        milestone: false,
        sort_label: String::new(),
        order_href: String::new(),
        link_query: String::new(),
        previous: None,
        next: None,
        credits_of: None,
        region: DEFAULT_REGION.to_string(),
        regions: Vec::new(),
        provider_groups: Vec::new(),
        cast: Vec::new(),
        crew: Vec::new(),
        cast_collapsed: false,
        related_news: Vec::new(),
        related_news_href: String::new(),
        trailer: None,
        spoiler: None,
        signed_in: false,
    }
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
            return (status_for(&error), body).into_response();
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

// -- Title detail helpers ------------------------------------------------------

/// A trimmed non-empty string, or `None`. Percy sends `""` and `null` for the same
/// "nothing here", and a section keyed on `Some("")` renders as an empty heading.
fn non_empty(value: Option<&str>) -> Option<String> {
    value.map(str::trim).filter(|text| !text.is_empty()).map(str::to_string)
}

/// The year out of an ISO date, or `None` for an undated (announced) title.
fn year_of(release_date: Option<&str>) -> Option<String> {
    release_date.and_then(|date| date.get(..4)).map(str::to_string)
}

/// The hero's spelled-out kind. The order rows use `FILM`/`TV` tiles; a headline has room.
fn kind_noun(kind: &str) -> &'static str {
    match kind {
        "tv" => "Series",
        "special" => "Special",
        _ => "Film",
    }
}

/// A curated path's slug as a label — `complete` → `Complete`, `phase-one` → `Phase one`.
///
/// The detail payload carries the path *slug* only (the names live on the universe page),
/// so this humanises rather than looks up. Deliberately not word-by-word title case: a
/// path called `mcu-only` must not become `Mcu Only`.
fn path_label(slug: &str) -> String {
    let spaced = slug.replace(['-', '_'], " ");
    let mut chars = spaced.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
        None => spaced,
    }
}

/// A two-letter uppercase region, or the default. Percy 422s an unknown shape, and this
/// page must never turn a junk query string into an error page.
fn normalise_region(region: Option<&str>) -> String {
    region
        .map(str::trim)
        .filter(|code| code.len() == 2 && code.chars().all(|c| c.is_ascii_alphabetic()))
        .map(|code| code.to_ascii_uppercase())
        .unwrap_or_else(|| DEFAULT_REGION.to_string())
}

/// The switcher's regions, with the current one selected.
///
/// A short fixed list, not every TMDB region: this is a `<select>` in a plain GET form, and
/// a 200-entry dropdown is a worse control than a missing one. An arbitrary `?region=` still
/// reaches Percy — it is only the *offered* set that is short.
fn region_options(current: &str) -> Vec<RegionOption> {
    REGIONS
        .iter()
        .map(|&(code, name)| RegionOption {
            code: code.to_string(),
            name: name.to_string(),
            selected: code == current,
        })
        .collect()
}

/// Back to the watch order this title's position is measured in, carrying the same
/// path/region/sort — otherwise "position 3 of 42" links to a list numbered differently.
fn order_href(universe: &str, path: &str, region: &str, sort: &str) -> String {
    let mut query = vec![format!("sort={sort}"), format!("region={region}")];
    if !path.is_empty() {
        query.push(format!("path={path}"));
    }
    format!("/watchlist/{universe}?{}", query.join("&"))
}

fn neighbour(title: &WatchTitle) -> TitleNeighbour {
    TitleNeighbour {
        slug: title.slug.clone(),
        title: title.title.clone(),
        year: year_of(title.release_date.as_deref()),
        poster_url: title
            .poster_path
            .as_deref()
            .map(|path| format!("{TMDB_IMAGE_BASE}/w154{path}")),
    }
}

fn title_credit(credit: &WatchCredit) -> TitleCredit {
    TitleCredit {
        name: credit.name.clone(),
        slug: credit.slug.clone(),
        // The character is the interesting half of an acting credit; the bare role is all
        // there is for crew, and for an actor TMDB left uncredited. Same rule as `person_row`.
        role_label: non_empty(credit.character_name.as_deref()).unwrap_or_else(|| role_noun(&credit.role).to_string()),
        photo_url: credit
            .profile_path
            .as_deref()
            .map(|path| format!("{TMDB_IMAGE_BASE}/w185{path}")),
    }
}

/// Providers grouped by offer, in a fixed order, deduplicated within each group.
///
/// The order row keeps only `flatrate` because it has space for four logos and one
/// question ("can I just watch this?"). The detail page has room for the whole answer, so
/// rent and buy get their own rows — but a group with nothing in it is dropped rather than
/// rendered as an empty heading.
fn provider_groups(title: &WatchTitle) -> Vec<ProviderGroup> {
    OFFERS
        .iter()
        .filter_map(|&(offer, label)| {
            let mut seen: Vec<i64> = Vec::new();
            let providers: Vec<OrderProvider> = title
                .providers
                .iter()
                .filter(|provider| provider.offer == offer)
                .filter(|provider| {
                    let fresh = !seen.contains(&provider.provider_id);
                    seen.push(provider.provider_id);
                    fresh
                })
                .map(|provider| OrderProvider {
                    name: provider.provider_name.clone(),
                    logo_url: provider
                        .logo_path
                        .as_deref()
                        .map(|logo| format!("{TMDB_IMAGE_BASE}/w45{logo}"))
                        .unwrap_or_default(),
                })
                .collect();
            (!providers.is_empty()).then(|| ProviderGroup {
                label: label.to_string(),
                providers,
            })
        })
        .collect()
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
fn group_into_eras(titles: &[WatchTitle], sort: &str, marks: &Marks<'_>) -> Vec<OrderEra> {
    if sort != "release" {
        return vec![OrderEra {
            key: String::new(),
            label: String::new(),
            rows: titles
                .iter()
                .enumerate()
                .map(|(index, title)| order_row(title, index + 1, marks))
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
            let mut row = order_row(title, index + 1, marks);
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

/// Everything a row needs to draw its own watched control.
///
/// Threaded through `group_into_eras` rather than applied afterwards: the toggle's
/// `release_key` is what the page's summary then counts, so the two cannot disagree
/// about which titles are on the page.
struct Marks<'a> {
    marks: &'a ProgressMap,
    signed_in: bool,
    /// The universe half of every key on this page.
    universe: &'a str,
    /// Where a JS-off mark bounces back to — this page, with its path/region/sort.
    here: &'a str,
}

/// The string Percy files a title's watched mark under, and the only way one is built.
///
/// `{universe}/{slug}` — the same `key` a release card carries on `/releases`, which is
/// what makes a mark made there show up here. Never `watch_titles.id`: that is V39's
/// separate table, and `progress.rs`'s module doc says why this page does not use it.
fn title_key(universe: &str, slug: &str) -> String {
    format!("{universe}/{slug}")
}

fn order_row(title: &WatchTitle, position: usize, marks: &Marks<'_>) -> OrderRow {
    let (kind_class, kind_label) = match title.kind.as_str() {
        "tv" => ("tv", "TV"),
        "special" => ("special", "SPEC"),
        _ => ("movie", "FILM"),
    };
    OrderRow {
        toggle: progress::toggle(
            marks.marks,
            marks.signed_in,
            "title",
            &title_key(marks.universe, &title.slug),
            marks.here,
        ),
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
    use crate::templates::NewsRow;
    use percy_client::{WatchProvider, WatchTrailer};

    /// A signed-out page with nothing marked — what most of these tests are about, since
    /// the grouping rules are identical either way.
    fn unmarked() -> ProgressMap {
        ProgressMap::default()
    }

    fn marks<'a>(map: &'a ProgressMap, signed_in: bool) -> Marks<'a> {
        Marks {
            marks: map,
            signed_in,
            universe: "mcu",
            here: "/watchlist/mcu?sort=story&region=US",
        }
    }

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
        let sections = group_into_eras(&titles, "release", &marks(&unmarked(), false));

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
        let sections = group_into_eras(&titles, "story", &marks(&unmarked(), false));

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
        let sections = group_into_eras(&titles, "release", &marks(&unmarked(), false));

        // Three sections, not two: hoisting "c" up to the first Phase 1 block would
        // silently reorder the watch order the curation defined.
        assert_eq!(sections.len(), 3);
        assert_eq!(sections[2].rows[0].slug, "c");
    }

    #[test]
    fn titles_without_an_era_get_one_trailing_section() {
        let sections = group_into_eras(
            &[title("a", None), title("b", None)],
            "release",
            &marks(&unmarked(), false),
        );
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

        let film_row = order_row(&title("iron-man", None), 3, &marks(&unmarked(), false));
        assert_eq!(film_row.number, "03");
        assert_eq!(
            (film_row.kind_class.as_str(), film_row.kind_label.as_str()),
            ("movie", "FILM")
        );

        let show_row = order_row(&show, 12, &marks(&unmarked(), false));
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

    // -- Title detail page ------------------------------------------------

    fn detail_title() -> WatchTitle {
        WatchTitle {
            universe: "mcu".into(),
            slug: "iron-man".into(),
            title: "Iron Man".into(),
            kind: "movie".into(),
            runtime: 126,
            release_date: Some("2008-05-02".into()),
            poster_path: Some("/poster.jpg".into()),
            backdrop_path: Some("/backdrop.jpg".into()),
            overview: Some("Tony Stark builds a suit.".into()),
            tmdb_rating: Some(7.6),
            tmdb_votes: Some(24_000),
            era: Some("phase1".into()),
            milestone: true,
            importance: Some("essential".into()),
            instruction: Some("STOP before the credits".into()),
            context: Some("The one that starts it all.".into()),
            spoiler: Some("Tony reveals his identity.".into()),
            sub_universe: Some("Marvel Studios".into()),
            providers: vec![
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
            ],
            trailer: Some(WatchTrailer {
                site: "YouTube".into(),
                key: "8ugaeA-nMTc".into(),
                name: Some("Official Trailer".into()),
                url: "https://www.youtube.com/watch?v=8ugaeA-nMTc".into(),
                embed_url: "https://www.youtube.com/embed/8ugaeA-nMTc".into(),
            }),
            ..WatchTitle::default()
        }
    }

    fn title_credit_fixture(role: &str, character: Option<&str>) -> WatchCredit {
        WatchCredit {
            tmdb_person_id: 3223,
            name: "Robert Downey Jr.".into(),
            slug: "robert-downey-jr".into(),
            profile_path: Some("/rdj.jpg".into()),
            role: role.to_string(),
            character_name: character.map(str::to_string),
            billing_order: Some(0),
        }
    }

    /// The whole page, as the handler assembles it. Fields the handler computes are
    /// computed here too, so a change to one of those rules breaks this test rather
    /// than quietly rendering something else.
    fn title_page(source: WatchTitle, related_news: Vec<NewsRow>) -> WatchTitleTemplate {
        title_page_as(source, related_news, &unmarked(), false)
    }

    fn title_page_as(
        source: WatchTitle,
        related_news: Vec<NewsRow>,
        map: &ProgressMap,
        signed_in: bool,
    ) -> WatchTitleTemplate {
        let credits = [
            title_credit_fixture("cast", Some("Tony Stark")),
            title_credit_fixture("director", None),
        ];
        let (cast, crew): (Vec<_>, Vec<_>) = credits.iter().partition(|credit| credit.role == "cast");
        WatchTitleTemplate {
            universe: WatchUniverse {
                slug: "mcu".into(),
                name: "Marvel Cinematic Universe".into(),
                short_name: "MCU".into(),
                accent: "#E62429".into(),
                ..WatchUniverse::default()
            },
            year: year_of(source.release_date.as_deref()),
            kind_label: kind_noun(&source.kind).to_string(),
            backdrop_url: source
                .backdrop_path
                .as_deref()
                .map(|path| format!("{TMDB_IMAGE_BASE}/w1280{path}")),
            poster_url: source
                .poster_path
                .as_deref()
                .map(|path| format!("{TMDB_IMAGE_BASE}/w342{path}")),
            context: non_empty(source.context.as_deref()),
            overview: non_empty(source.overview.as_deref()),
            runtime_label: runtime_label(&source),
            rating: source.tmdb_rating.map(|rating| format!("{rating:.1}")),
            votes: source.tmdb_votes,
            release_date: non_empty(source.release_date.as_deref()),
            studio: non_empty(source.sub_universe.as_deref()),
            era: source.era.as_deref().map(era_label),
            position: 3,
            total: 42,
            path_name: Some(path_label("complete")),
            importance: non_empty(source.importance.as_deref()),
            instruction: non_empty(source.instruction.as_deref()),
            milestone: source.milestone,
            sort_label: sort_label("story").to_string(),
            order_href: order_href("mcu", "complete", "US", "story"),
            link_query: "?sort=story&region=US".into(),
            previous: Some(neighbour(&title("the-incredible-hulk", None))),
            next: Some(neighbour(&title("iron-man-2", None))),
            credits_of: None,
            regions: region_options("US"),
            provider_groups: provider_groups(&source),
            cast_collapsed: false,
            cast: cast.into_iter().map(title_credit).collect(),
            crew: crew.into_iter().map(title_credit).collect(),
            related_news,
            related_news_href: "/news?media=title&subject_type=universe&subject_value=mcu".into(),
            trailer: source.trailer.as_ref().map(|trailer| TrailerView {
                label: non_empty(trailer.name.as_deref()).unwrap_or_else(|| "Trailer".to_string()),
                site: trailer.site.clone(),
                url: trailer.url.clone(),
                embed_url: trailer.embed_url.clone(),
            }),
            spoiler: non_empty(source.spoiler.as_deref()),
            signed_in,
            account: None,
            toggle: progress::toggle(
                map,
                signed_in,
                "title",
                &title_key("mcu", &source.slug),
                &format!("/watchlist/mcu/{}?sort=story&region=US", source.slug),
            ),
            title: source.title.clone(),
            slug: source.slug.clone(),
            region: "US".into(),
        }
    }

    fn story(headline: &str) -> NewsRow {
        NewsRow {
            url: "https://example.test/story".into(),
            headline: headline.into(),
            excerpt: None,
            source_name: "Marvel.com".into(),
            published: Some("2026-08-01".into()),
            published_iso: Some("2026-08-01T09:00:00".into()),
            followed: false,
            chips: Vec::new(),
        }
    }

    fn render_title(page: WatchTitleTemplate) -> String {
        use askama::Template;
        page.render().expect("the title template renders")
    }

    #[test]
    fn the_title_page_renders_every_section_it_has_data_for() {
        let html = render_title(title_page(detail_title(), vec![story("Doomsday dated")]));

        // 1. Hero: title, year, the curator's "why it matters", and the action row.
        assert!(html.contains("Iron Man"));
        assert!(html.contains("2008"));
        assert!(html.contains("The one that starts it all."));
        assert!(
            html.contains("Sign in to track"),
            "signed out, the actions collapse to one"
        );
        assert!(
            !html.contains("Subscribe to MCU"),
            "no follow control without a session"
        );
        // 2. Fact panel — a definition grid, not prose.
        assert!(html.contains("<dt>Runtime</dt>"));
        assert!(html.contains("2h 06m"));
        assert!(html.contains("7.6"));
        // 3. Where it fits — position, path, importance, instruction, neighbours.
        assert!(html.contains("Where it fits"));
        assert!(html.contains(">3<") && html.contains("of 42"));
        assert!(html.contains("Complete path"));
        assert!(html.contains("essential"));
        assert!(html.contains("STOP before the credits"));
        assert!(html.contains("href=\"/watchlist/mcu/iron-man-2?sort=story&amp;region=US\""));
        assert!(html.contains("href=\"/watchlist/mcu?sort=story&amp;region=US&amp;path=complete\""));
        // 4. Streaming — grouped by offer, region stated, switcher is a plain GET form.
        assert!(html.contains("Stream") && html.contains("Rent"));
        assert!(html.contains("Disney Plus") && html.contains("Google Play"));
        assert!(html.contains("method=\"get\""), "the region switcher never fetches");
        // 5. Cast & crew — a <details>, so it works with JS off.
        assert!(html.contains("<details"));
        assert!(html.contains("Tony Stark"));
        assert!(html.contains("href=\"/people/robert-downey-jr\""));
        // 6b. Related news.
        assert!(html.contains("Doomsday dated"));
        // 8. Spoilers — collapsed, and never open by default.
        assert!(html.contains("Tony reveals his identity."));
        assert!(!html.contains("wl-spoiler\" open"));
        // §9: TMDB's notice is mandatory wherever their data is shown.
        assert!(html.contains("TMDB"));
    }

    #[test]
    fn the_trailer_is_a_link_with_no_iframe_in_the_html() {
        // §6.2's "click-to-load, never autoplay": the embed is not in the served markup,
        // so a visit costs nothing until someone asks for it — and the control is a real
        // link, so a JS-off click still reaches the trailer.
        let html = render_title(title_page(detail_title(), Vec::new()));

        assert!(!html.contains("<iframe"), "the embed must not ship in the initial HTML");
        assert!(html.contains("href=\"https://www.youtube.com/watch?v=8ugaeA-nMTc\""));
        assert!(html.contains("data-embed=\"https://www.youtube.com/embed/8ugaeA-nMTc\""));
        assert!(html.contains("Watch on YouTube"), "the fallback names its destination");
        // ...and the only thing that can turn it into an embed is this one script.
        assert!(html.contains("/static/js/trailer.js"));
        assert!(!html.contains("autoplay"));
    }

    #[test]
    fn a_title_with_no_trailer_omits_the_section_entirely() {
        let mut without = detail_title();
        without.trailer = None;
        let html = render_title(title_page(without, Vec::new()));

        assert!(!html.contains("wl-trailer"));
        assert!(!html.contains("data-embed"));
        // ...and the rest of the page is untouched.
        assert!(html.contains("Where it fits"));
    }

    #[test]
    fn related_news_is_absent_rather_than_empty() {
        // §6.2: no stories means no section — an empty "Related news" heading tells the
        // reader the feature is broken rather than that nothing has been written.
        let html = render_title(title_page(detail_title(), Vec::new()));
        assert!(!html.contains("Related news"));
    }

    #[test]
    fn a_title_with_no_spoiler_note_has_no_spoiler_block() {
        // The block is only ever the curation's own `spoiler` line; this page never
        // invents one, so a title without one simply has no section.
        let mut without = detail_title();
        without.spoiler = None;
        let html = render_title(title_page(without, Vec::new()));
        assert!(!html.contains("wl-spoiler"));
    }

    #[test]
    fn percy_down_is_a_stated_empty_state_that_keeps_its_200() {
        let html = render_title(WatchTitleTemplate {
            universe: WatchUniverse {
                slug: "mcu".into(),
                name: "mcu".into(),
                ..WatchUniverse::default()
            },
            ..empty_title_page(None)
        });

        assert!(html.contains("Try again in a moment."));
        assert!(html.contains("href=\"/watchlist/mcu\""), "a way back out");
        // Nothing invented: no facts, no trailer, no spoiler heading.
        assert!(!html.contains("Where it fits"));
        assert!(!html.contains("wl-trailer"));
    }

    #[test]
    fn status_for_separates_an_unknown_title_from_a_backend_outage() {
        // An unknown slug must be a real 404 or a crawler indexes every invented URL;
        // an outage keeps its 200 because the title probably exists.
        assert_eq!(status_for(&PercyError::NotFound), StatusCode::NOT_FOUND);
        assert_eq!(status_for(&PercyError::Api("boom".into())), StatusCode::OK);
    }

    #[test]
    fn signed_in_the_action_row_offers_the_subscription_page() {
        let html = render_title(title_page_as(detail_title(), Vec::new(), &unmarked(), true));

        assert!(html.contains("Subscribe to MCU"));
        assert!(html.contains("href=\"/account/subscriptions\""));
        assert!(!html.contains("Sign in to track"));
    }

    #[test]
    fn provider_groups_split_by_offer_and_drop_duplicates_within_one() {
        let groups = provider_groups(&detail_title());

        assert_eq!(groups.len(), 2, "nothing is offered to buy, so there is no Buy row");
        assert_eq!(groups[0].label, "Stream");
        assert_eq!(groups[0].providers.len(), 1);
        assert_eq!(groups[1].label, "Rent");
        // Disney Plus rents *and* streams: it belongs in both, once in each.
        assert_eq!(groups[1].providers.len(), 2);
        assert_eq!(groups[1].providers[0].name, "Disney Plus");
    }

    #[test]
    fn a_title_nobody_streams_has_no_provider_groups() {
        let mut bare = detail_title();
        bare.providers = Vec::new();
        assert!(provider_groups(&bare).is_empty());
    }

    #[test]
    fn a_junk_region_falls_back_instead_of_reaching_percy() {
        assert_eq!(normalise_region(Some("de")), "DE");
        assert_eq!(normalise_region(Some(" gb ")), "GB");
        assert_eq!(normalise_region(Some("GBR")), DEFAULT_REGION);
        assert_eq!(normalise_region(Some("1;drop")), DEFAULT_REGION);
        assert_eq!(normalise_region(None), DEFAULT_REGION);
    }

    #[test]
    fn the_order_link_carries_the_ordering_the_position_was_measured_in() {
        // Otherwise "3 of 42" lands on a list numbered differently, which is worse than
        // no link at all.
        assert_eq!(
            order_href("mcu", "complete", "DE", "release"),
            "/watchlist/mcu?sort=release&region=DE&path=complete"
        );
        // A universe with no curated path must not emit `path=`.
        assert_eq!(
            order_href("mcu", "", "US", "story"),
            "/watchlist/mcu?sort=story&region=US"
        );
    }

    #[test]
    fn a_path_slug_is_humanised_without_title_casing_every_word() {
        assert_eq!(path_label("complete"), "Complete");
        assert_eq!(path_label("essentials-only"), "Essentials only");
        // `Mcu Only` would be wrong; sentence case keeps acronyms out of trouble.
        assert_eq!(path_label("mcu_only"), "Mcu only");
    }

    // -- Progress: the watched toggle and the order page's summary (R8b) ---

    /// The order page as the handler assembles it, including the two rules that must
    /// agree — which rows carry a toggle and which keys the summary counts.
    fn order_page(titles: &[WatchTitle], map: &ProgressMap, signed_in: bool) -> WatchOrderTemplate {
        let here = order_href("mcu", "complete", "US", "story");
        let eras = group_into_eras(
            titles,
            "story",
            &Marks {
                marks: map,
                signed_in,
                universe: "mcu",
                here: &here,
            },
        );
        let (done, seen) = progress::summary(
            map,
            "title",
            eras.iter()
                .flat_map(|era| era.rows.iter())
                .map(|row| row.toggle.release_key.as_str()),
        );
        WatchOrderTemplate {
            account: None,
            universe: WatchUniverse {
                slug: "mcu".into(),
                name: "Marvel Cinematic Universe".into(),
                ..WatchUniverse::default()
            },
            paths: Vec::new(),
            progress: ProgressSummary::new(signed_in, done, seen, "watched"),
            eras,
            path: "complete".into(),
            region: "US".into(),
            sort: "story".into(),
            sort_label: sort_label("story").to_string(),
            total: titles.len(),
            hours: 2,
            essential: 0,
            movies: titles.len(),
            shows: 0,
        }
    }

    fn render_order(page: WatchOrderTemplate) -> String {
        use askama::Template;
        page.render().expect("the watch order template renders")
    }

    #[test]
    fn an_order_row_is_keyed_by_universe_and_slug_and_comes_back_to_this_ordering() {
        // Never `watch_titles.id`: this page writes `release_progress`, so the key is the
        // same string a release card on /releases carries.
        let row = &order_page(&[title("iron-man", None)], &unmarked(), true).eras[0].rows[0];
        assert_eq!(row.toggle.release_key, "mcu/iron-man");
        assert_eq!(row.toggle.media, "title");
        // ...and a JS-off mark returns to the path/region/sort the list was numbered in.
        assert_eq!(row.toggle.redirect, "/watchlist/mcu?sort=story&region=US&path=complete");
    }

    #[test]
    fn a_watched_row_renders_the_undo_and_an_unwatched_one_renders_the_mark() {
        let map = crate::progress::tests::map("title", &["mcu/iron-man"]);
        let html = render_order(order_page(&[title("iron-man", None), title("thor", None)], &map, true));

        // The marked row's form posts the *removal*, so the undo is one click and needs
        // no JS to exist.
        assert!(html.contains(r#"action="/account/progress/remove""#));
        assert!(html.contains(r#"value="mcu/iron-man""#));
        assert!(html.contains("pg--on"));
        assert!(html.contains(r#"aria-pressed="true""#));
        // ...and the unmarked one posts the mark.
        assert!(html.contains(r#"action="/account/progress""#));
        assert!(html.contains(r#"value="mcu/thor""#));
        assert!(html.contains("Mark watched"));
        // The medium's verb, which Percy 400s if it is the other one.
        assert!(html.contains(r#"name="status" value="watched""#));
    }

    #[test]
    fn signed_out_the_order_page_offers_a_sign_in_rather_than_a_dead_button() {
        let html = render_order(order_page(&[title("iron-man", None)], &unmarked(), false));

        assert!(
            !html.contains("/account/progress"),
            "no write control without a session"
        );
        assert!(html.contains("Sign in to track"));
        assert!(html.contains("/login?next=/watchlist/mcu"));
        // ...and the order itself is untouched: this page is useful logged out.
        assert!(html.contains("iron-man"));
    }

    #[test]
    fn the_summary_counts_only_the_rows_on_the_page() {
        // A mark for a title this path leaves out must not push the count past the list —
        // curated paths drop titles, and `done > total` would read as a bug.
        let map = crate::progress::tests::map("title", &["mcu/iron-man", "mcu/a-title-not-on-this-path"]);
        let html = render_order(order_page(&[title("iron-man", None), title("thor", None)], &map, true));

        assert!(html.contains("1 of 2 watched"));
        assert!(html.contains("width: 50%"));
    }

    #[test]
    fn signed_out_there_is_no_summary_and_no_keyboard_hint_promised() {
        let html = render_order(order_page(&[title("iron-man", None)], &unmarked(), false));
        assert!(!html.contains("pg-summary"), "a 0 of N bar for a visitor with no marks");
        assert!(!html.contains("pg-keys"));
    }

    #[test]
    fn a_watched_title_page_renders_the_undo_and_a_signed_out_one_renders_the_sign_in() {
        let map = crate::progress::tests::map("title", &["mcu/iron-man"]);
        let marked = render_title(title_page_as(detail_title(), Vec::new(), &map, true));
        assert!(marked.contains(r#"action="/account/progress/remove""#));
        assert!(marked.contains(r#"value="mcu/iron-man""#));
        assert!(marked.contains("pg--on"));
        // The redirect keeps the ordering the page was reached in.
        assert!(marked.contains(r#"value="/watchlist/mcu/iron-man?sort=story&amp;region=US""#));

        let out = render_title(title_page(detail_title(), Vec::new()));
        assert!(!out.contains("/account/progress"));
        assert!(out.contains("Sign in to track"));
    }

    #[test]
    fn a_credit_shows_the_character_for_acting_and_the_role_for_crew() {
        assert_eq!(
            title_credit(&title_credit_fixture("cast", Some("Tony Stark"))).role_label,
            "Tony Stark"
        );
        assert_eq!(
            title_credit(&title_credit_fixture("director", None)).role_label,
            "Director"
        );
        // An uncredited actor has no character; the role noun is all there is to say.
        assert_eq!(
            title_credit(&title_credit_fixture("cast", Some("  "))).role_label,
            "Actor"
        );
    }
}
