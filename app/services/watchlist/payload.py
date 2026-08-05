"""Pure shaping of watchlist DB rows into the JSON payloads the internal API serves.

Nothing here touches the database, discord.py or the network -- callers hand in the rows
they fetched (via :class:`~app.database.repositories.watchlist.WatchlistRepository`) and get
back plain JSON-safe dicts. That keeps the row-to-payload contract unit-testable without a
pool, which matters because the dashboard's Rust models are generated against these keys.

JSON-safety rules applied here: ``NUMERIC`` comes back from asyncpg as :class:`decimal.Decimal`
and ``DATE``/``TIMESTAMP`` as :mod:`datetime` objects -- both are converted (float / ISO string)
rather than left for FastAPI's encoder, so the shape is identical in tests and in production.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.services.watchlist.trailers import trailer_urls

if TYPE_CHECKING:
    import datetime
    from collections.abc import Iterable, Mapping, Sequence

__all__ = (
    'credit_payloads',
    'era_payloads',
    'path_payloads',
    'person_payload',
    'person_summary',
    'title_payload',
    'trailer_payload',
    'universe_payload',
    'universe_stats',
    'universe_summary',
)

#: Title columns copied straight through to the payload (JSON-safe as-is).
_TITLE_PASSTHROUGH = (
    'id', 'universe', 'tmdb_type', 'tmdb_id', 'slug', 'title', 'kind', 'runtime', 'episodes',
    'poster_path', 'backdrop_path', 'overview', 'tmdb_votes', 'era', 'era_order', 'story_order',
    'release_order', 'milestone', 'instruction', 'context', 'spoiler', 'credits_of', 'sub_universe',
)


def _accent_hex(value: int | None) -> str:
    """Renders a stored 24-bit accent integer as a CSS ``#RRGGBB`` string."""
    return f"#{(value or 0) & 0xFFFFFF:06X}"


def _iso(value: datetime.date | datetime.datetime | None) -> str | None:
    """ISO-formats a date/datetime, passing ``None`` through."""
    return value.isoformat() if value is not None else None


def universe_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    """Shapes one ``list_universes`` row (universe columns + the two aggregates)."""
    return {
        'slug': row['slug'],
        'name': row['name'],
        'short_name': row['short_name'],
        'tagline': row['tagline'],
        'accent': _accent_hex(row['accent']),
        'logo_url': row['logo_url'],
        'backdrop_url': row['backdrop_url'],
        'sort_order': row['sort_order'],
        'published': row['published'],
        'synced_at': _iso(row['synced_at']),
        'title_count': row['title_count'],
        'total_runtime': row['total_runtime'],
    }


def path_payloads(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Shapes ``list_paths`` rows, preserving their ``sort_order`` sequence."""
    return [
        {
            'slug': row['slug'],
            'name': row['name'],
            'description': row['description'],
            'is_default': row['is_default'],
            'sort_order': row['sort_order'],
        }
        for row in rows
    ]


def era_payloads(titles: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Derives the era list from the titles themselves, ordered by ``era_order``.

    Eras are not a table -- a title carries its era key and that era's sort weight -- so the
    page's era headings come from folding the title list. Titles with no era are ignored.
    """
    eras: dict[str, dict[str, Any]] = {}
    for row in titles:
        key = row['era']
        if not key:
            continue
        era = eras.get(key)
        if era is None:
            eras[key] = {'key': key, 'order': row['era_order'], 'title_count': 1}
        else:
            era['title_count'] += 1
            era['order'] = min(era['order'], row['era_order'])
    return sorted(eras.values(), key=lambda era: (era['order'], era['key']))


def trailer_payload(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Shapes a title row's ``V44`` trailer columns, or ``None`` when it has no trailer.

    Both URLs are handed over ready to use: ``url`` for the plain link-out and ``embed_url``
    for the click-to-load iframe. Neither carries playback parameters -- autoplay is forbidden
    (the plan's §6.2), and *when* the iframe appears stays entirely the renderer's decision.
    Absent rather than empty, so a page can branch on the section existing at all.
    """
    site, key = row.get('trailer_site'), row.get('trailer_key')
    if not site or not key:
        return None
    urls = trailer_urls(site, key)
    if urls is None:  # a site stored before it was renderable; nothing honest to show
        return None
    watch, embed = urls
    return {'site': site, 'key': key, 'name': row.get('trailer_name'), 'url': watch, 'embed_url': embed}


def title_payload(
    row: Mapping[str, Any], *, providers: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Shapes one title row, with its (already region-filtered) provider rows attached.

    ``importance`` is present only when the row came from a path-joined query; it is emitted
    as ``None`` otherwise so the key is always there for the typed Rust model. ``trailer`` is
    read the same forgiving way, so a row selected before ``V44`` landed still shapes.
    """
    payload: dict[str, Any] = {key: row[key] for key in _TITLE_PASSTHROUGH}
    payload['release_date'] = _iso(row['release_date'])
    rating = row['tmdb_rating']
    payload['tmdb_rating'] = float(rating) if rating is not None else None
    payload['importance'] = row.get('importance')
    payload['trailer'] = trailer_payload(row)
    payload['providers'] = [
        {
            'provider_id': provider['provider_id'],
            'provider_name': provider['provider_name'],
            'logo_path': provider['logo_path'],
            'offer': provider['offer'],
        }
        for provider in providers
    ]
    return payload


def universe_payload(
    universe: Mapping[str, Any],
    paths: Iterable[Mapping[str, Any]],
    titles: Sequence[Mapping[str, Any]],
    providers: Iterable[Mapping[str, Any]],
    *,
    path_slug: str | None,
    region: str,
    sort: str,
) -> dict[str, Any]:
    """Builds the whole-page payload: universe, paths, eras and every title.

    ``providers`` is the flat multi-title result of ``list_providers``; it is grouped by
    ``title_id`` here so the caller issues exactly one provider query per page.
    """
    by_title: dict[int, list[Mapping[str, Any]]] = {}
    for provider in providers:
        by_title.setdefault(provider['title_id'], []).append(provider)

    return {
        'universe': {
            'slug': universe['slug'],
            'name': universe['name'],
            'short_name': universe['short_name'],
            'tagline': universe['tagline'],
            'accent': _accent_hex(universe['accent']),
            'logo_url': universe['logo_url'],
            'backdrop_url': universe['backdrop_url'],
            'published': universe['published'],
            'synced_at': _iso(universe['synced_at']),
        },
        'path': path_slug,
        'region': region,
        'sort': sort,
        'paths': path_payloads(paths),
        'eras': era_payloads(titles),
        'titles': [title_payload(row, providers=by_title.get(row['id'], ())) for row in titles],
    }


def credit_payloads(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Shapes ``list_credits`` rows (a credit joined to its person) for a title's cast & crew.

    Emitted flat and in query order (role, then billing) rather than pre-grouped: the page
    decides whether to render one list or a section per role, and a flat list survives a new
    role being added without changing the payload's shape.
    """
    return [
        {
            'tmdb_person_id': row['tmdb_person_id'],
            'name': row['name'],
            'slug': row['slug'],
            'profile_path': row['profile_path'],
            'role': row['role'],
            'character_name': row['character_name'],
            'billing_order': row['billing_order'],
        }
        for row in rows
    ]


def person_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    """Shapes one ``search_people`` row -- what an autocomplete entry needs, nothing more."""
    return {
        'tmdb_person_id': row['tmdb_person_id'],
        'name': row['name'],
        'slug': row['slug'],
        'profile_path': row['profile_path'],
        'credit_count': row['credit_count'],
    }


def person_payload(
    person: Mapping[str, Any], credits: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Builds a person page: the person plus every title they are credited on.

    ``credits`` are ``list_person_credits`` rows -- a credit joined to the full title *and* its
    universe -- so each entry nests a complete :func:`title_payload` and the caller needs no
    second lookup to render a poster row. The repository's ordering (undated first, then newest)
    is preserved, which is what makes the top of the list read as "what's next".
    """
    return {
        'person': {
            'tmdb_person_id': person['tmdb_person_id'],
            'name': person['name'],
            'slug': person['slug'],
            'profile_path': person['profile_path'],
            'updated_at': _iso(person['updated_at']),
        },
        'credits': [
            {
                'role': row['role'],
                'character_name': row['character_name'],
                'billing_order': row['billing_order'],
                'universe_name': row['universe_name'],
                'universe_accent': _accent_hex(row['universe_accent']),
                'title': title_payload(row),
            }
            for row in credits
        ],
    }


def universe_stats(titles: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Totals for one path's title list: counts, runtime minutes and hours.

    ``essential`` / ``recommended`` / ``optional`` count the path-joined ``importance``
    values; titles with no importance row for the path are counted only in ``total``.
    """
    runtime = sum(row['runtime'] or 0 for row in titles)
    counts = {'essential': 0, 'recommended': 0, 'optional': 0}
    for row in titles:
        importance = row.get('importance')
        if importance in counts:
            counts[importance] += 1
    return {
        'total': len(titles),
        'movies': sum(1 for row in titles if row['kind'] == 'movie'),
        'shows': sum(1 for row in titles if row['kind'] == 'tv'),
        'runtime': runtime,
        'hours': round(runtime / 60, 1),
        **counts,
    }
