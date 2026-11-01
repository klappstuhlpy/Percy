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
use percy_client::{PercyError, ProgressMap, WatchCredit, WatchPersonCredit, WatchStats, WatchTitle, WatchUniverse};
use serde::Deserialize;

use crate::{
    progress,
    session::Session,
    templates::{
        OrderEra, OrderProvider, OrderRow, PersonCreditRow, PersonRoleGroup, PersonTemplate, PersonUniverseLink,
        ProgressSummary, ProviderGroup, RegionOption, SeasonRow, TitleCredit, TitleNeighbour, TrailerView,
        WatchOrderTemplate, WatchTitleTemplate, WatchlistTemplate,
    },
    view::Viewer,
    AppState,
};

/// Role order a person's filmography is grouped and rendered in — cast first, since
/// that is what most visitors came for, then the behind-the-camera credits.
const ROLE_ORDER: [&str; 4] = ["cast", "director", "writer", "creator"];

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

    // All three go out together: neither the marks nor the header's figures may cost a
    // public catalogue page a serial round trip.
    let (fetched, stats, marks) = match &app.percy {
        Some(percy) => tokio::join!(
            percy.get_watch_universe(&slug, query.path.as_deref(), &region, sort),
            percy.get_watch_universe_stats(&slug, query.path.as_deref()),
            progress::fetch(&app, &session, "title")
        ),
        None => (
            Err(PercyError::Api("percy is not configured".into())),
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
            stats: WatchStats::default(),
        }
        .into_response();
    };

    // Which rows are series containers rather than entries. Derived once, here, because
    // it is a property of the whole payload — the template can only see one row.
    let containers = container_ids(&page.titles);
    let path = page.path.clone().unwrap_or_default();
    let eras = group_into_eras(
        &page.titles,
        &page.sort,
        &containers,
        &Marks {
            marks: &marks,
            signed_in: session.is_some(),
            universe: &page.universe.slug,
            // A JS-off mark comes back to this exact ordering — landing on the default
            // path in the default region would renumber the list under the visitor.
            here: &order_href(&page.universe.slug, &path, &page.region, &page.sort),
        },
    );
    // Counted over the keys actually rendered *as entries*, so a mark for a title this
    // path leaves out can never push the count past the list, and a container — which has
    // no toggle on the page — can never inflate the denominator past what Percy counted.
    // Signed out there is no bar at all.
    let (done, seen) = progress::summary(
        &marks,
        "title",
        eras.iter()
            .flat_map(|era| era.rows.iter())
            .filter(|row| !row.container)
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
        // Percy's figures, not ours — it serves all five importance/kind numbers from one
        // endpoint and excludes containers, which is the same definition of "an entry" the
        // list above renders. A failed second call falls back to the same arithmetic over
        // the rows already in hand, because a header that says 0 next to 144 rows is worse
        // than a header nobody had to fetch twice.
        stats: stats.unwrap_or_else(|_| derive_stats(&page.titles, &containers)),
    }
    .into_response()
}

/// `GET /watchlist/:universe/:slug` — one title as a destination of its own (§6.2).
///
/// Same soft-404 split as the comic detail page, and for the same reason: an unknown slug is
/// a real 404 so a crawler never indexes an invented URL, while a Percy that is down keeps
/// its 200 and a stated empty state, because the title probably exists.
///
/// Everything goes out together — the detail, the ordering it sits in, the universe's related
/// news and the visitor's marks — so no decoration costs the page a serial round trip, and a
/// failure in any one of them costs a section rather than the page.
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

    let (fetched, ordering, related_news, marks) = match &app.percy {
        Some(percy) => tokio::join!(
            percy.get_watch_title(&universe, &slug, &region, sort),
            // The ordering itself, because Percy's detail payload counts and walks *rows*
            // rather than entries — see `place`. Deliberately `None` for the path: that is
            // what `get_title` passes internally, so this is the same default path in the
            // same order, off the same 300 s cache, rather than a second opinion.
            percy.get_watch_universe(&universe, None, &region, sort),
            crate::news::related(&app, "title", "universe", &universe),
            progress::fetch(&app, &session, "title")
        ),
        None => (
            Err(PercyError::Api("percy is not configured".into())),
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
    // This page, in the ordering it was reached in — where a JS-off mark bounces back to,
    // whether it was made on this title or on one of its seasons.
    let here = format!("/watchlist/{}/{}{link_query}", detail.universe.slug, title.slug);
    let placing = ordering
        .ok()
        .and_then(|page| place(&page.titles, title.id))
        // Percy's own numbers, and only when the ordering did not arrive: they count the
        // containers, but a strip that is off by the container count still tells a reader
        // roughly where they are, and a blank one tells them nothing.
        .unwrap_or_else(|| Placing {
            position: Some(detail.position),
            total: detail.total,
            previous: detail.previous.as_ref().map(neighbour),
            next: detail.next.as_ref().map(neighbour),
        });
    let season_marks = Marks {
        marks: &marks,
        signed_in,
        universe: &detail.universe.slug,
        here: &here,
    };

    WatchTitleTemplate {
        account: viewer(&session),
        // Percy's own universe slug and the title's own slug: the same pair the order
        // row files its mark under, so the two surfaces mark one row rather than two.
        // Rendered only when this row is an entry: a container's mark would be filed under
        // a key no count on any surface looks at.
        toggle: progress::toggle(
            &marks,
            signed_in,
            "title",
            &title_key(&detail.universe.slug, &title.slug),
            &here,
        ),
        season_label: non_empty(title.season_name.as_deref()),
        parent: detail.parent.as_ref().map(neighbour),
        seasons: detail.seasons.iter().map(|s| season_row(s, &season_marks)).collect(),
        tmdb_url: tmdb_url(&title),
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
        position: placing.position,
        total: placing.total,
        importance: non_empty(title.importance.as_deref()),
        instruction: non_empty(title.instruction.as_deref()),
        milestone: title.milestone,
        sort_label: sort_label(&detail.sort).to_string(),
        order_href: order_href(&detail.universe.slug, &path, &region, &detail.sort),
        link_query: format!("?sort={}&region={region}", detail.sort),
        previous: placing.previous,
        next: placing.next,
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
        position: None,
        total: 0,
        season_label: None,
        parent: None,
        seasons: Vec::new(),
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
        tmdb_url: None,
    }
}

/// `GET /people/:slug` — one person: what they have coming, then their filmography.
///
/// Same soft-404 split the comic pages make: an unknown slug is a real 404, a backend
/// that is down is a 200 empty state. A page that answered 200 to any invented name
/// would get indexed as a real person.
pub async fn person_page(State(app): State<AppState>, session: Option<Session>, Path(slug): Path<String>) -> Response {
    let (fetched, related_news) = match &app.percy {
        Some(percy) => tokio::join!(
            percy.get_watch_person(&slug),
            crate::news::related(&app, "title", "person", &slug)
        ),
        None => (Err(PercyError::Api("percy is not configured".into())), Vec::new()),
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
                universe_links: Vec::new(),
                upcoming: Vec::new(),
                role_groups: Vec::new(),
                related_news: Vec::new(),
                related_news_href: String::new(),
                tmdb_person_id: None,
            };
            return (status_for(&error), body).into_response();
        }
    };

    let today = today_iso();
    let (upcoming, filmography): (Vec<_>, Vec<_>) = page
        .credits
        .iter()
        .partition(|credit| is_upcoming(credit.title.release_date.as_deref(), &today));

    // A person with no id gets no TMDB link rather than one pointing at `/0`.
    PersonTemplate {
        account: viewer(&session),
        name: page.person.name,
        photo_url: page
            .person
            .profile_path
            .as_deref()
            .map(|path| format!("{TMDB_IMAGE_BASE}/w185{path}")),
        roles: role_summary(&page.credits),
        credit_count: page.credits.len(),
        universe_links: universe_links(&page.credits),
        upcoming: upcoming.into_iter().map(person_row).collect(),
        role_groups: group_by_role(&filmography),
        related_news,
        related_news_href: crate::news::subject_news_href("title", "person", &page.person.slug),
        tmdb_person_id: (page.person.tmdb_person_id != 0).then_some(page.person.tmdb_person_id),
        slug: page.person.slug,
    }
    .into_response()
}

/// The universes a person spans, deduplicated in the order they first appear in the
/// credits — never alphabetical, never by count, so the header reads in the same order
/// as the filmography below it.
fn universe_links(credits: &[WatchPersonCredit]) -> Vec<PersonUniverseLink> {
    let mut links: Vec<PersonUniverseLink> = Vec::new();
    for credit in credits {
        if links.iter().any(|link| link.slug == credit.title.universe) {
            continue;
        }
        links.push(PersonUniverseLink {
            slug: credit.title.universe.clone(),
            name: credit.universe_name.clone(),
        });
    }
    links
}

/// Splits the (already-released) filmography into one section per role present, in
/// `ROLE_ORDER`, preserving Percy's newest-first ordering within each.
fn group_by_role(filmography: &[&WatchPersonCredit]) -> Vec<PersonRoleGroup> {
    ROLE_ORDER
        .into_iter()
        .filter_map(|role| {
            let rows: Vec<PersonCreditRow> = filmography
                .iter()
                .filter(|credit| credit.role == role)
                .map(|credit| person_row(credit))
                .collect();
            if rows.is_empty() {
                None
            } else {
                Some(PersonRoleGroup {
                    role_label: role_section_label(role).to_string(),
                    rows,
                })
            }
        })
        .collect()
}

/// The heading for a role's filmography section.
fn role_section_label(role: &str) -> &'static str {
    match role {
        "cast" => "Cast",
        "director" => "Director",
        "writer" => "Writer",
        "creator" => "Creator",
        _ => "Credited",
    }
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
        role_label: role_noun(&credit.role).to_string(),
        // The character is the interesting half of an acting credit; crew and an actor
        // TMDB left uncredited get nothing here, so the row falls back to `role_label`.
        character: credit.character_name.clone().filter(|name| !name.is_empty()),
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

/// Where a title sits among the **entries** of its ordering, and which entries flank it.
struct Placing {
    /// `None` for a series container — it heads its seasons rather than holding a place
    /// among them, so there is no number to draw.
    position: Option<i64>,
    total: i64,
    previous: Option<TitleNeighbour>,
    next: Option<TitleNeighbour>,
}

/// Re-numbers the detail page over the rows a reader can actually watch.
///
/// Percy's `/watchlist/titles/{universe}/{slug}` numbers `position`/`total` over *every* row
/// of the universe page — `index + 1` and `len(titles)` — and hands back the raw adjacent
/// rows as `previous`/`next`. `/watchlist/universes/{slug}/stats`, which is what the order
/// page's header renders, excludes any row another row names as its `parent_id`. So the two
/// disagreed by exactly the container count (live MCU: 159 rows against 144 entries) and a
/// detail page could read "5 of 5" under a header that said 4, while "Next" could land a
/// reader on a heading with nothing to watch and nothing to mark.
///
/// Counted here over the same definition of "an entry" the order page's rows, its progress
/// bar and Percy's own figures all use, so the four cannot disagree. `None` only when the
/// title is not in the ordering at all, which leaves the caller on Percy's numbers.
fn place(titles: &[WatchTitle], id: i64) -> Option<Placing> {
    let containers = container_ids(titles);
    let entry = |title: &&WatchTitle| !containers.contains(&title.id);
    let here = titles.iter().position(|title| title.id == id)?;
    Some(Placing {
        position: entry(&&titles[here]).then(|| titles[..here].iter().filter(entry).count() as i64 + 1),
        total: titles.iter().filter(entry).count() as i64,
        // A container's neighbours are the entries either side of the group it heads: the
        // last entry before it, and its own first season.
        previous: titles[..here].iter().rev().find(entry).map(neighbour),
        next: titles[here + 1..].iter().find(entry).map(neighbour),
    })
}

/// One season under a series container, with the mark the container itself does not carry.
fn season_row(season: &WatchTitle, marks: &Marks<'_>) -> SeasonRow {
    SeasonRow {
        // TMDB's own name (`Season 2`, `Specials`) rather than the row's full title, which
        // repeats the series name this list already sits under.
        label: non_empty(season.season_name.as_deref()).unwrap_or_else(|| season.title.clone()),
        poster_url: season
            .poster_path
            .as_deref()
            .map(|path| format!("{TMDB_IMAGE_BASE}/w92{path}")),
        // Joined here rather than in the template: a season that has aired nothing yet has
        // no runtime *and* no year, and stitching them template-side leaves a bare "·".
        meta: [
            runtime_label(season),
            year_of(season.release_date.as_deref()).unwrap_or_default(),
        ]
        .iter()
        .filter(|part| !part.is_empty())
        .cloned()
        .collect::<Vec<_>>()
        .join(" · "),
        toggle: progress::toggle(
            marks.marks,
            marks.signed_in,
            "title",
            &title_key(marks.universe, &season.slug),
            marks.here,
        ),
        slug: season.slug.clone(),
    }
}

/// `https://www.themoviedb.org/{movie|tv}/{id}` — the outbound attribution §9 asks for on any
/// page showing TMDB data, which `person.html` has always carried and this page never did.
///
/// A season borrows its series' `tmdb_id`/`tmdb_type` (TMDB issues no id of its own for one),
/// so it deep-links to the season page TMDB does publish rather than dropping the reader on
/// the series and leaving them to find it.
fn tmdb_url(title: &WatchTitle) -> Option<String> {
    if title.tmdb_id <= 0 || title.tmdb_type.is_empty() {
        return None;
    }
    let base = format!("https://www.themoviedb.org/{}/{}", title.tmdb_type, title.tmdb_id);
    Some(match title.season_number {
        Some(number) if title.tmdb_type == "tv" => format!("{base}/season/{number}"),
        _ => base,
    })
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

/// The ids of the rows that are series **containers** rather than watch entries.
///
/// `V45` explodes a multi-season show into one row per season *plus* the series row, and the
/// series row is then a container — the migration's own words: "it keeps its identity, its
/// aggregate runtime/episodes, its series-level cast and its trailer, and the seasons are
/// what a reader watches and marks". Percy's whole read side agrees: `list_universes`,
/// `universe_stats` and `titles_releasing_between` all exclude a row another row names as
/// its `parent_id`. The universe *page* payload still returns them, so rendering them as
/// entries here would put the list 15 rows above the figures in its own header (live MCU:
/// 159 rows, 39 seasons under 15 containers, 144 counted entries).
///
/// A row is a container exactly when another row in **this** payload names its `id` — the
/// relation itself, never a title-string prefix, which would mis-group any show whose real
/// name starts with another's. A single-season show is never exploded, so it has no children
/// and stays an ordinary entry reading "Hawkeye", not "Hawkeye: Season 1".
fn container_ids(titles: &[WatchTitle]) -> Vec<i64> {
    let mut containers: Vec<i64> = Vec::new();
    for parent in titles.iter().filter_map(|title| title.parent_id) {
        if !containers.contains(&parent) {
            containers.push(parent);
        }
    }
    containers
}

/// The header's figures, over the same rows the list renders as entries.
///
/// Only reached when Percy answered the page but not `/stats` — it is the same arithmetic
/// `app/services/watchlist/payload.py::universe_stats` does, container exclusion included,
/// so the two sources can only ever agree.
fn derive_stats(titles: &[WatchTitle], containers: &[i64]) -> WatchStats {
    let entries = || titles.iter().filter(|title| !containers.contains(&title.id));
    let importance = |name: &str| entries().filter(|t| t.importance.as_deref() == Some(name)).count() as i64;
    let runtime: i64 = entries().map(|title| title.runtime).sum();
    WatchStats {
        total: entries().count() as i64,
        movies: entries().filter(|title| title.kind == "movie").count() as i64,
        shows: entries().filter(|title| title.kind == "tv").count() as i64,
        runtime,
        hours: (runtime as f64 / 6.0).round() / 10.0,
        essential: importance("essential"),
        recommended: importance("recommended"),
        optional: importance("optional"),
        ..WatchStats::default()
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
fn group_into_eras(titles: &[WatchTitle], sort: &str, containers: &[i64], marks: &Marks<'_>) -> Vec<OrderEra> {
    // A container consumes no position: the numbers down the left of the list have to count
    // what a reader watches, which is the same set Percy's `total` counts.
    let mut position = 0usize;
    let mut rows: Vec<OrderRow> = Vec::with_capacity(titles.len());
    for title in titles {
        let container = containers.contains(&title.id);
        position += usize::from(!container);
        rows.push(order_row(title, position, container, marks));
    }

    if sort != "release" {
        return vec![OrderEra {
            key: String::new(),
            label: String::new(),
            entries: rows.iter().filter(|row| !row.container).count(),
            rows,
        }];
    }

    let mut sections: Vec<OrderEra> = Vec::new();
    for (title, mut row) in titles.iter().zip(rows) {
        let key = title.era.clone().unwrap_or_default();
        if sections.last().map(|section| section.key != key).unwrap_or(true) {
            sections.push(OrderEra {
                label: era_label(&key),
                key,
                rows: Vec::new(),
                entries: 0,
            });
        }
        if let Some(section) = sections.last_mut() {
            // No era chip here: the section heading right above already says it.
            row.era = None;
            section.entries += usize::from(!row.container);
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

fn order_row(title: &WatchTitle, position: usize, container: bool, marks: &Marks<'_>) -> OrderRow {
    let (kind_class, kind_label) = match title.kind.as_str() {
        "tv" => ("tv", "TV"),
        "special" => ("special", "SPEC"),
        _ => ("movie", "FILM"),
    };
    OrderRow {
        // Built for every row, rendered only for entries: `watch_order.html` calls the
        // toggle macro in the entry branch alone, and a container's key never reaches the
        // progress summary either.
        toggle: progress::toggle(
            marks.marks,
            marks.signed_in,
            "title",
            &title_key(marks.universe, &title.slug),
            marks.here,
        ),
        container,
        position: if container { 0 } else { position },
        number: if container {
            String::new()
        } else {
            format!("{position:02}")
        },
        kind_class: kind_class.to_string(),
        kind_label: kind_label.to_string(),
        slug: title.slug.clone(),
        title: title.title.clone(),
        year: title
            .release_date
            .as_deref()
            .and_then(|date| date.get(..4))
            .map(str::to_string),
        poster_url: title
            .poster_path
            .as_deref()
            .map(|path| format!("{TMDB_IMAGE_BASE}/w154{path}")),
        runtime_label: runtime_label(title),
        rating: title.tmdb_rating.map(|rating| format!("{rating:.1}")),
        era: title.era.as_deref().map(era_label),
        // Rank and landmark belong to entries. A container can still carry a curated
        // `importance` row of its own, but drawing its marker and badge on a heading Percy
        // does not count would announce a rank the "N essential" figure never counted.
        importance: (!container).then(|| title.importance.clone()).flatten(),
        instruction: title.instruction.clone(),
        studio: title.sub_universe.clone(),
        milestone: !container && title.milestone,
        // Kept on a heading: TMDB answers "where can I stream this" per series, so for an
        // exploded show the container is often where the only answer lives.
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
    use percy_client::{WatchProvider, WatchTrailer, WatchUniverseSummary};

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
        let sections = group_into_eras(&titles, "release", &[], &marks(&unmarked(), false));

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
        let sections = group_into_eras(&titles, "story", &[], &marks(&unmarked(), false));

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
        let sections = group_into_eras(&titles, "release", &[], &marks(&unmarked(), false));

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
            &[],
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

        let film_row = order_row(&title("iron-man", None), 3, false, &marks(&unmarked(), false));
        assert_eq!(film_row.number, "03");
        assert_eq!(
            (film_row.kind_class.as_str(), film_row.kind_label.as_str()),
            ("movie", "FILM")
        );

        let show_row = order_row(&show, 12, false, &marks(&unmarked(), false));
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
    fn a_row_keeps_the_role_noun_and_the_character_separate() {
        let acted = person_row(&credit("cast", Some("Tony Stark"), None));
        assert_eq!(acted.role_label, "Actor");
        assert_eq!(acted.character.as_deref(), Some("Tony Stark"));

        let directed = person_row(&credit("director", None, None));
        assert_eq!(directed.role_label, "Director");
        assert_eq!(directed.character, None);

        // An uncredited actor has no character: the role noun is all there is to say.
        assert_eq!(person_row(&credit("cast", Some(""), None)).character, None);
    }

    #[test]
    fn universe_links_dedupe_in_first_appearance_order() {
        let mut second = credit("cast", None, None);
        second.universe_name = "DC Extended Universe".into();
        second.title.universe = "dceu".into();

        let mut repeat = credit("director", None, None);
        repeat.title.universe = "dceu".into();
        repeat.universe_name = "DC Extended Universe".into();

        let credits = vec![credit("cast", None, None), second, repeat];
        let links = universe_links(&credits);

        assert_eq!(links.len(), 2, "the repeated dceu credit is not a second link");
        assert_eq!(links[0].slug, "mcu");
        assert_eq!(links[1].slug, "dceu");
    }

    #[test]
    fn filmography_groups_by_role_in_a_fixed_order_with_only_present_roles() {
        let cast = credit("cast", Some("Tony Stark"), Some("2008-05-02"));
        let directed = credit("director", None, Some("2012-05-04"));
        let refs = vec![&cast, &directed];

        let groups = group_by_role(&refs);

        assert_eq!(groups.len(), 2, "no writer or creator credits, no section for them");
        assert_eq!(groups[0].role_label, "Cast");
        assert_eq!(groups[0].rows.len(), 1);
        assert_eq!(groups[1].role_label, "Director");
    }

    #[test]
    fn the_person_page_renders_role_grouped_sections() {
        use askama::Template;

        let cast_credit = credit("cast", Some("Tony Stark"), Some("2008-05-02"));
        let page = PersonTemplate {
            account: None,
            name: "Robert Downey Jr.".into(),
            slug: "robert-downey-jr".into(),
            photo_url: Some("https://image.tmdb.org/t/p/w185/rdj.jpg".into()),
            roles: "Actor".into(),
            credit_count: 2,
            universe_links: vec![PersonUniverseLink {
                slug: "mcu".into(),
                name: "Marvel Cinematic Universe".into(),
            }],
            upcoming: vec![person_row(&credit("cast", Some("Doctor Doom"), None))],
            role_groups: group_by_role(&[&cast_credit]),
            related_news: Vec::new(),
            related_news_href: String::new(),
            tmdb_person_id: Some(3223),
        };
        let html = page.render().expect("the person template renders");

        assert!(html.contains("Coming up"));
        assert!(html.contains("Cast"));
        assert!(html.contains("Doctor Doom"), "the macro body reaches the loop variable");
        assert!(
            html.contains("as Tony Stark"),
            "a cast row shows the character it played"
        );
        assert!(html.contains("href=\"/watchlist/mcu/iron-man\""));
        assert!(
            html.contains("href=\"/watchlist/mcu\""),
            "the header links the universe"
        );
        assert!(html.contains("2008"), "a released title keeps its year");
        assert!(html.contains("https://www.themoviedb.org/person/3223"));
    }

    #[test]
    fn a_person_with_no_tmdb_id_gets_no_attribution_link() {
        use askama::Template;

        let page = PersonTemplate {
            account: None,
            name: "unknown".into(),
            slug: "unknown".into(),
            photo_url: None,
            roles: String::new(),
            credit_count: 0,
            universe_links: Vec::new(),
            upcoming: Vec::new(),
            role_groups: Vec::new(),
            related_news: Vec::new(),
            related_news_href: String::new(),
            tmdb_person_id: None,
        };
        let html = page.render().expect("the empty-state person template renders");
        assert!(!html.contains("themoviedb.org/person"));
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
                link: None,
            },
            WatchProvider {
                provider_id: 337,
                provider_name: "Disney Plus".into(),
                logo_path: Some("/d.jpg".into()),
                offer: "rent".into(),
                link: None,
            },
            WatchProvider {
                provider_id: 3,
                provider_name: "Google Play".into(),
                logo_path: Some("/g.jpg".into()),
                offer: "rent".into(),
                link: None,
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
            tmdb_type: "movie".into(),
            tmdb_id: 1726,
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
                    link: None,
                },
                WatchProvider {
                    provider_id: 337,
                    provider_name: "Disney Plus".into(),
                    logo_path: Some("/d.jpg".into()),
                    offer: "rent".into(),
                    link: None,
                },
                WatchProvider {
                    provider_id: 3,
                    provider_name: "Google Play".into(),
                    logo_path: Some("/g.jpg".into()),
                    offer: "rent".into(),
                    link: None,
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
            position: Some(3),
            total: 42,
            season_label: non_empty(source.season_name.as_deref()),
            parent: None,
            seasons: Vec::new(),
            tmdb_url: tmdb_url(&source),
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
            image_url: None,
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

    /// A film, a two-season show `V45` exploded into a container plus its seasons, and one
    /// more film — the smallest ordering in which counting *rows* and counting *entries*
    /// give different answers (5 rows, 4 entries).
    fn seasoned_universe() -> Vec<WatchTitle> {
        let row = |id: i64, slug: &str, parent: Option<i64>, season: Option<i64>| WatchTitle {
            id,
            universe: "mcu".into(),
            tmdb_type: if season.is_some() { "tv" } else { "movie" }.into(),
            tmdb_id: 1234,
            slug: slug.into(),
            title: slug.into(),
            kind: if season.is_some() { "tv" } else { "movie" }.into(),
            parent_id: parent,
            season_number: season,
            season_name: season.map(|number| format!("Season {number}")),
            ..WatchTitle::default()
        };
        vec![
            row(1, "iron-man", None, None),
            row(2, "daredevil", None, None),
            row(3, "daredevil-season-1", Some(2), Some(1)),
            row(4, "daredevil-season-2", Some(2), Some(2)),
            row(5, "the-avengers", None, None),
        ]
    }

    #[test]
    fn the_position_strip_counts_entries_rather_than_rows() {
        let titles = seasoned_universe();

        // Percy's own payload would call the last film "5 of 5"; the header it links to
        // counts 4, because the container is not an entry.
        let last = place(&titles, 5).expect("the film is in the ordering");
        assert_eq!(last.position, Some(4));
        assert_eq!(last.total, 4);

        let first_season = place(&titles, 3).expect("the season is in the ordering");
        assert_eq!(first_season.position, Some(2), "a season is an entry and holds a place");
        assert_eq!(first_season.total, 4);
    }

    #[test]
    fn a_container_has_no_place_among_the_entries_it_heads() {
        let titles = seasoned_universe();
        let placing = place(&titles, 2).expect("the container is in the ordering");

        assert_eq!(placing.position, None, "a heading holds no place in what it heads");
        assert_eq!(placing.total, 4, "and it is not counted in the total either");
        // Its neighbours are the entries either side of the group: the film before it, and
        // its own first season.
        assert_eq!(placing.previous.map(|item| item.slug), Some("iron-man".to_string()));
        assert_eq!(
            placing.next.map(|item| item.slug),
            Some("daredevil-season-1".to_string())
        );
    }

    #[test]
    fn walking_previous_and_next_never_stops_on_a_container() {
        let titles = seasoned_universe();

        // Percy's raw neighbour for the film before the show is the container itself.
        let before = place(&titles, 1).expect("the film is in the ordering");
        assert_eq!(
            before.next.map(|item| item.slug),
            Some("daredevil-season-1".to_string())
        );
        assert!(before.previous.is_none(), "the first entry has nothing before it");

        // ...and walking back off the film after the show skips it in the other direction.
        let after = place(&titles, 5).expect("the film is in the ordering");
        assert_eq!(
            after.previous.map(|item| item.slug),
            Some("daredevil-season-2".to_string())
        );
        assert!(after.next.is_none());
    }

    #[test]
    fn a_title_missing_from_the_ordering_leaves_the_caller_on_percys_numbers() {
        assert!(place(&seasoned_universe(), 99).is_none());
    }

    /// The container's own page: a heading over its seasons, matching the treatment its row
    /// gets on the order page — no number, no rank, and no mark of its own.
    #[test]
    fn a_series_page_renders_its_seasons_and_offers_no_mark_of_its_own() {
        let titles = seasoned_universe();
        let map = unmarked();
        let marks = Marks {
            marks: &map,
            signed_in: true,
            universe: "mcu",
            here: "/watchlist/mcu/daredevil?sort=story&region=US",
        };
        let placing = place(&titles, 2).expect("the container is in the ordering");
        let mut page = title_page_as(titles[1].clone(), Vec::new(), &map, true);
        page.position = placing.position;
        page.total = placing.total;
        page.seasons = titles[2..4].iter().map(|row| season_row(row, &marks)).collect();
        let html = render_title(page);

        assert!(
            !html.contains("wl-fits__position"),
            "a heading is drawn no place number"
        );
        assert!(
            html.contains("wl-tag--series"),
            "the order page's Series tag stands there instead"
        );
        assert!(html.contains("Its 2 seasons are the entries"));
        assert!(html.contains("href=\"/watchlist/mcu/daredevil-season-2?sort=story&amp;region=US\""));
        assert!(html.contains("Season 1") && html.contains("Season 2"));
        // One control per season and none for the heading: a mark filed under a container's
        // key is counted by nothing on any surface.
        assert_eq!(
            html.matches("data-progress").count(),
            2,
            "the seasons carry the toggles the container does not"
        );
    }

    /// A season TMDB has dated and counted, and one it has not — the optional halves have to
    /// collapse to nothing rather than to a stray separator.
    #[test]
    fn a_season_line_carries_only_the_facts_percy_has() {
        let mut aired = seasoned_universe()[2].clone();
        aired.episodes = Some(13);
        aired.runtime = 700;
        aired.release_date = Some("2015-04-10".into());
        let map = unmarked();
        let marks = Marks {
            marks: &map,
            signed_in: false,
            universe: "mcu",
            here: "/watchlist/mcu/daredevil",
        };

        assert_eq!(season_row(&aired, &marks).meta, "13 episodes · 11h 40m · 2015");
        // Nothing announced yet: no runtime, no air date, and so no line at all.
        assert_eq!(season_row(&seasoned_universe()[3], &marks).meta, "");
    }

    #[test]
    fn a_season_names_the_series_it_belongs_to_and_links_it() {
        let titles = seasoned_universe();
        let mut page = title_page(titles[3].clone(), Vec::new());
        page.parent = Some(neighbour(&titles[1]));
        let html = render_title(page);

        assert!(html.contains("<dt>Season</dt>"));
        assert!(html.contains("Season 2 of"));
        assert!(html.contains("href=\"/watchlist/mcu/daredevil?sort=story&amp;region=US\""));
        // A season borrows its series' TMDB id, so the attribution deep-links to the season
        // TMDB does publish rather than dropping the reader on the series.
        assert!(html.contains("https://www.themoviedb.org/tv/1234/season/2"));
    }

    #[test]
    fn a_film_links_out_to_tmdb_and_a_row_without_an_id_does_not() {
        let html = render_title(title_page(detail_title(), Vec::new()));
        assert!(html.contains("href=\"https://www.themoviedb.org/movie/1726\""));
        assert!(!html.contains("/season/"), "a film has no season to deep-link");
        assert!(html.contains("rel=\"noopener\""));

        let mut unknown = detail_title();
        unknown.tmdb_id = 0;
        let html = render_title(title_page(unknown, Vec::new()));
        assert!(!html.contains("View on TMDB"), "absent, not an empty attribution line");
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
        assert!(html.contains("/static/js/catalogue/trailer.js"));
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
        let containers = container_ids(titles);
        let eras = group_into_eras(
            titles,
            "story",
            &containers,
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
                .filter(|row| !row.container)
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
            // Percy's `/stats` shape, derived over the same rows Percy derives it over — the
            // point of the container tests below is that this number and the rendered list
            // cannot disagree.
            stats: derive_stats(titles, &containers),
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

    // -- The universe index --------------------------------------------------

    fn summary(logo: Option<&str>, backdrop: Option<&str>) -> WatchUniverseSummary {
        WatchUniverseSummary {
            slug: "mcu".into(),
            name: "Marvel Cinematic Universe".into(),
            accent: "#E62429".into(),
            logo_url: logo.map(str::to_string),
            backdrop_url: backdrop.map(str::to_string),
            title_count: 144,
            total_runtime: 22_000,
            ..WatchUniverseSummary::default()
        }
    }

    fn render_index(universes: Vec<WatchUniverseSummary>) -> String {
        use askama::Template;
        WatchlistTemplate {
            account: None,
            universes,
            ticker: Vec::new(),
        }
        .render()
        .expect("the watchlist index renders")
    }

    /// The contents of a card's `<h2>`, so an assertion about the heading cannot be
    /// satisfied by the name appearing anywhere else on the page.
    fn heading(html: &str) -> &str {
        html.split_once("wl-card__name")
            .and_then(|(_, rest)| rest.split_once("</h2>"))
            .expect("the card renders a heading")
            .0
    }

    #[test]
    fn the_index_renders_the_art_percy_has_and_nothing_where_it_has_none() {
        // Today's real state: every seed's art columns are empty. The card must then be the
        // plain card it has always been -- no modifier, no backdrop layer, no image element
        // reserving a band -- with the name carrying the heading as text.
        let bare = render_index(vec![summary(None, None)]);
        assert!(!bare.contains("wl-card--art"), "no scrim without a photo to scrim");
        assert!(!bare.contains("wl-card__backdrop"));
        assert!(!bare.contains("wl-card__logo"));
        // ...and the name is what the heading itself carries, not something the card only
        // mentions elsewhere: a tile with neither art nor a name is a blank tile.
        assert!(heading(&bare).contains("Marvel Cinematic Universe"));

        let art = render_index(vec![summary(Some("/logo.png"), Some("/backdrop.jpg"))]);
        assert!(art.contains("wl-card--art"));
        assert!(art.contains("url('/backdrop.jpg')"));
        // The wordmark *is* the heading, so the name is its alt text rather than beside it.
        assert!(art.contains(r#"alt="Marvel Cinematic Universe""#));
        assert!(art.contains(r#"aria-hidden="true""#));
    }

    // -- Series containers (`V45`) -----------------------------------------

    /// A multi-season show as `V45` stores it and the universe payload returns it: the series
    /// container first, then one row per season naming the series' `id` as its parent. Order
    /// matches Percy's (`ORDER BY … COALESCE(season_number, -1), id`).
    fn exploded(id: i64, slug: &str, seasons: i64) -> Vec<WatchTitle> {
        let mut rows = vec![WatchTitle {
            id,
            kind: "tv".into(),
            episodes: Some(seasons * 8),
            runtime: seasons * 400,
            ..title(slug, None)
        }];
        rows.extend((1..=seasons).map(|number| WatchTitle {
            id: id * 100 + number,
            parent_id: Some(id),
            season_number: Some(number),
            season_name: Some(format!("Season {number}")),
            kind: "tv".into(),
            episodes: Some(8),
            runtime: 400,
            ..title(&format!("{slug}-season-{number}"), None)
        }));
        rows
    }

    /// Film, then a two-season show exploded into a container + its seasons, then a
    /// single-season show — which `V45` never explodes, so it has no children.
    fn with_a_container() -> Vec<WatchTitle> {
        let mut titles = vec![WatchTitle {
            id: 1,
            kind: "movie".into(),
            importance: Some("essential".into()),
            ..title("iron-man", None)
        }];
        titles.extend(exploded(2, "daredevil", 2));
        titles.push(WatchTitle {
            id: 5,
            kind: "tv".into(),
            ..title("hawkeye", None)
        });
        titles
    }

    #[test]
    fn a_container_is_a_heading_and_its_seasons_are_the_entries() {
        let titles = with_a_container();
        let containers = container_ids(&titles);
        assert_eq!(containers, vec![2], "only the row its seasons name as parent");

        let rows = &group_into_eras(&titles, "story", &containers, &marks(&unmarked(), false))[0].rows;
        assert_eq!(rows.len(), 5, "every payload row still renders");

        // The container: a heading, so no number and no position of its own...
        let series = rows.iter().find(|row| row.slug == "daredevil").unwrap();
        assert!(series.container);
        assert_eq!(series.number, "");
        assert_eq!(series.position, 0);

        // ...and the numbers down the left count only what a reader watches, so the season
        // after the one film is 02 rather than 03.
        let numbers: Vec<&str> = rows.iter().map(|row| row.number.as_str()).collect();
        assert_eq!(numbers, ["01", "", "02", "03", "04"]);

        // A single-season show is not a container and reads as itself.
        let single = rows.iter().find(|row| row.slug == "hawkeye").unwrap();
        assert!(!single.container);
        assert_eq!(single.number, "04");
    }

    #[test]
    fn the_figures_count_exactly_the_rows_rendered_as_entries() {
        // The whole point of the container rule: Percy's `/stats` excludes containers, so a
        // page that rendered them as entries would print 5 next to 4 watchable rows.
        let titles = with_a_container();
        let containers = container_ids(&titles);
        let eras = group_into_eras(&titles, "story", &containers, &marks(&unmarked(), false));
        let entries = eras
            .iter()
            .flat_map(|era| era.rows.iter())
            .filter(|row| !row.container)
            .count();

        let stats = derive_stats(&titles, &containers);
        assert_eq!(stats.total, 4);
        assert_eq!(stats.total as usize, entries, "the header and the list must agree");
        assert_eq!(eras[0].entries, entries, "and so must the section's own badge");
        // The container's runtime is its seasons' sum, so counting it would double the show.
        assert_eq!(stats.runtime, 126 + 400 + 400 + 126);
        assert_eq!(stats.movies, 1);
        assert_eq!(stats.shows, 3, "two seasons and the unexploded show");
        assert_eq!(stats.essential, 1);
    }

    #[test]
    fn watching_every_entry_reaches_the_end_of_the_bar() {
        // A container in the denominator would strand a completist at 4 of 5 forever.
        let map = crate::progress::tests::map(
            "title",
            &[
                "mcu/iron-man",
                "mcu/daredevil-season-1",
                "mcu/daredevil-season-2",
                "mcu/hawkeye",
            ],
        );
        let html = render_order(order_page(&with_a_container(), &map, true));

        assert!(html.contains("4 of 4 watched"), "{html}");
        assert!(html.contains("width: 100%"));
    }

    #[test]
    fn a_container_carries_no_data_entry_and_no_toggle() {
        // progress.js drives j/k and `w` off `[data-entry]` and `form[data-progress]`: a
        // heading with either would be a stop with nothing to toggle, and a `w` there would
        // post a mark for a row Percy does not count.
        let html = render_order(order_page(&with_a_container(), &unmarked(), true));

        assert_eq!(
            html.matches("data-entry").count(),
            4,
            "one per entry, none for the series"
        );
        assert_eq!(html.matches("data-progress").count(), 4);
        assert!(
            !html.contains(r#"value="mcu/daredevil""#),
            "no mark is keyed to the series"
        );

        // The heading is still there, and still a link to the series' own page — that is
        // where its series-level cast and trailer live.
        assert!(html.contains("wl-entry--series"));
        assert!(html.contains(r#"href="/watchlist/mcu/daredevil""#));
        assert!(html.contains(">Series<"));
    }

    #[test]
    fn a_container_shows_no_rank_marker_it_was_never_counted_under() {
        let mut titles = with_a_container();
        titles[1].importance = Some("essential".into());
        titles[1].milestone = true;
        let containers = container_ids(&titles);
        let rows = &group_into_eras(&titles, "story", &containers, &marks(&unmarked(), false))[0].rows;

        let series = rows.iter().find(|row| row.slug == "daredevil").unwrap();
        assert!(series.importance.is_none(), "a rank the essential figure never counted");
        assert!(!series.milestone);
        // The film's own rank is untouched.
        assert_eq!(rows[0].importance.as_deref(), Some("essential"));
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
