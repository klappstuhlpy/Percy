"""Pure shaping of comic-release DB rows into the JSON payloads the internal API serves.

Nothing here touches the database, FastAPI or discord.py -- callers hand in the rows they
fetched (via :class:`~app.database.repositories.releases.ReleasesRepository`) and get back
plain JSON-safe dicts. Mirrors ``app/services/watchlist/payload.py``'s split for the same
reason: the row-to-payload contract is unit-testable without a pool, and the dashboard's Rust
models are generated against these keys.

JSON-safety rules applied here: ``price`` is ``NUMERIC`` and comes back from asyncpg as
:class:`decimal.Decimal` (converted to ``float``); ``release_date``/``first_seen``/``updated_at``
are :mod:`datetime` objects (converted to ISO strings) -- both rather than left for FastAPI's
encoder, so the shape is identical in tests and in production.
"""

from __future__ import annotations

import datetime
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = (
    'DEFAULT_PER_PAGE',
    'MAX_PER_PAGE',
    'browse_payload',
    'character_payload',
    'credit_payload',
    'issue_sort_key',
    'names_payload',
    'release_payload',
    'series_context',
    'series_detail_payload',
    'series_summary_payload',
)

#: Mirrors ``ReleasesRepository.MAX_PAGE_SIZE`` -- the ceiling a bad ``per_page`` gets clamped to.
MAX_PER_PAGE = 100
DEFAULT_PER_PAGE = 24

#: Release columns copied straight through to the payload (JSON-safe as-is).
_RELEASE_PASSTHROUGH = (
    'id', 'brand', 'slug', 'title', 'series_slug', 'series_name', 'issue_number',
    'cover_url', 'description', 'page_count', 'format', 'publisher', 'url',
)

#: The leading numeric run of an issue number -- ``'3AU'`` -> ``'3'``, ``'1.MU'`` -> ``'1.MU'``
#: is NOT matched whole, only the digits (and one optional decimal point) at the start.
_LEADING_NUMBER_RE = re.compile(r'^(\d+(?:\.\d+)?)')


def _iso(value: datetime.date | datetime.datetime | None) -> str | None:
    """ISO-formats a date/datetime, passing ``None`` through."""
    return value.isoformat() if value is not None else None


def issue_sort_key(issue_number: str | None) -> tuple[int, float, str]:
    """Sort key for TEXT issue numbers, which can be ``'1.MU'``, ``'3AU'`` or plain ``'150'``.

    Numeric issues sort first, by their leading digits ascending (so ``'3AU'`` < ``'12'``);
    the full string is the tiebreak so ``'3AU'`` still sorts before ``'3B'``. An issue with no
    leading digits (or no number at all) sorts after every numeric one, lexically among itself.
    """
    if not issue_number:
        return (1, 0.0, '')
    match = _LEADING_NUMBER_RE.match(issue_number.strip())
    if match is None:
        return (1, 0.0, issue_number)
    return (0, float(match[1]), issue_number)


def _reading_order_key(row: Mapping[str, Any]) -> tuple[tuple[int, float, str], datetime.date]:
    """Reading-order key: issue number first, release date as the tiebreak.

    A release with no ``release_date`` sorts as if it were earliest, which only matters when
    two issues share an issue number (or both lack one) -- ``datetime.date.min`` is otherwise
    invisible in the ordering.
    """
    return issue_sort_key(row['issue_number']), row['release_date'] or datetime.date.min


def credit_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """Shapes one ``get_credits`` row."""
    return {'name': row['name'], 'role': row['role']}


def character_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """Shapes one ``get_characters`` row."""
    return {'name': row['name'], 'kind': row['kind']}


def release_payload(
    row: Mapping[str, Any],
    *,
    credits: Sequence[Mapping[str, Any]] = (),
    characters: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Shapes one ``comic_releases`` row, with its (already fetched) credits/characters attached.

    ``credits``/``characters`` default to empty -- callers that only need the release fields
    (a browse page, a series issue list) skip the extra queries and get ``[]`` back rather than
    a missing key, so the shape stays identical for the typed Rust model either way.
    """
    payload: dict[str, Any] = {key: row[key] for key in _RELEASE_PASSTHROUGH}
    payload['release_date'] = _iso(row['release_date'])
    price = row['price']
    payload['price'] = float(price) if price is not None else None
    payload['first_seen'] = _iso(row.get('first_seen'))
    payload['updated_at'] = _iso(row.get('updated_at'))
    payload['credits'] = [credit_payload(c) for c in credits]
    payload['characters'] = [character_payload(c) for c in characters]
    return payload


def browse_payload(
    rows: Iterable[Mapping[str, Any]],
    *,
    total: int,
    page: int,
    per_page: int,
    filters: Mapping[str, Any],
) -> dict[str, Any]:
    """Builds the ``GET /comics`` page: items plus pagination maths and the echoed filters.

    ``total_pages`` is clamped to at least 1 (matching ``app/cogs/tags.py``'s paginator), so an
    empty result still reports one (empty) page rather than zero.
    """
    total_pages = max(1, -(-total // per_page)) if per_page else 1
    return {
        'items': [release_payload(row) for row in rows],
        'page': page,
        'per_page': per_page,
        'total': total,
        'total_pages': total_pages,
        'filters': dict(filters),
    }


def series_summary_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """Shapes one ``list_series`` row."""
    return {
        'series_slug': row['series_slug'],
        'series_name': row['series_name'],
        'brand': row['brand'],
        'issue_count': row['issue_count'],
        'latest': _iso(row['latest']),
    }


def series_detail_payload(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Builds the ``GET /comics/series/{slug}`` payload: header plus issues in reading order.

    The header is read off the earliest issue in reading order (rather than an arbitrary DB
    row) so it stays stable regardless of fetch order. Callers must pass a non-empty sequence --
    "no issues" is a 404 the router decides, not something this shapes.
    """
    ordered = sorted(rows, key=_reading_order_key)
    header = ordered[0]
    return {
        'series_slug': header['series_slug'],
        'series_name': header['series_name'],
        'brand': header['brand'],
        'publisher': header['publisher'],
        'issue_count': len(ordered),
        'issues': [release_payload(row) for row in ordered],
    }


def series_context(
    rows: Sequence[Mapping[str, Any]], current_id: int, *, more_limit: int = 5,
) -> dict[str, Any]:
    """The ``series`` block of a release detail payload: reading-order neighbours + a short list.

    ``more_from_series`` is every other issue in the series, in reading order, capped at
    ``more_limit`` -- a discovery list, not the full series (that's ``GET /comics/series/{slug}``).
    """
    ordered = sorted(rows, key=_reading_order_key)
    index = next((i for i, row in enumerate(ordered) if row['id'] == current_id), None)
    if index is None:
        return {'previous': None, 'next': None, 'more_from_series': []}
    previous = release_payload(ordered[index - 1]) if index > 0 else None
    following = release_payload(ordered[index + 1]) if index + 1 < len(ordered) else None
    more = [release_payload(row) for row in ordered if row['id'] != current_id][:more_limit]
    return {'previous': previous, 'next': following, 'more_from_series': more}


def names_payload(kind: str, rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Shapes ``list_names`` rows for ``GET /comics/names``."""
    return {
        'kind': kind,
        'names': [{'name': row['name'], 'appearances': row['appearances']} for row in rows],
    }
