"""Shaping news rows into the JSON ``app/internal_api/routers/news.py`` returns.

Pure: rows in (an ``asyncpg.Record`` or a plain dict), JSON-safe dicts out. No database, no
FastAPI -- the same split as ``app/services/comics/payload.py``, so the contract the Rust client
is written against is unit-testable without a pool.

**Attribution is structural, not optional.** Every item carries its ``source`` (slug, display
name, homepage) and its ``url``, because the plan's §9 requires a story to name where it came
from and link the original on every surface that shows it. A caller cannot render an item from
this payload without having both to hand -- which is the point of putting them in the item
rather than in a lookup table beside it.

The ``subjects`` on an item are the tags it was matched by, in the *same* key shape a
subscription uses, so a client can render "you follow this" without a second call. The
``rule``/``matched_term`` provenance stays out: it is an operator's debugging aid for revoking a
bad rule, not something a reader needs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = (
    'browse_payload',
    'item_payload',
    'source_payload',
    'subject_payload',
)


def _isoformat(value: Any) -> str | None:
    """Renders a timestamp column, tolerating a NULL."""
    return value.isoformat() if value is not None else None


def subject_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """One ``news_subjects`` tag, in the key shape a subscription is expressed in."""
    return {
        'media': row['media'],
        'subject_type': row['subject_type'],
        'subject_value': row['subject_value'],
    }


def source_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """One ``news_sources`` row, for the operator view.

    ``last_fetched``/``last_status`` are the visibility pair §4.5 asks for: a feed that has been
    failing for a week is a value someone can read, not an absence of log lines.
    """
    return {
        'slug': row['slug'],
        'name': row['name'],
        'homepage': row['homepage'],
        'feed_url': row['feed_url'],
        'media': row['media'],
        'enabled': row['enabled'],
        'last_fetched': _isoformat(row['last_fetched']),
        'last_status': row['last_status'],
    }


def _source_stub(slug: str, sources: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The three source fields an item carries, degrading to the slug for an unknown source.

    A story whose source row has been deleted is still a story with a link, so it keeps its
    attribution rather than turning into a ``null`` a renderer has to special-case.
    """
    row = sources.get(slug)
    return {
        'slug': slug,
        'name': row['name'] if row is not None else slug,
        'homepage': row['homepage'] if row is not None else None,
    }


def item_payload(
    row: Mapping[str, Any],
    *,
    sources: Mapping[str, Mapping[str, Any]] | None = None,
    subjects: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Shapes one ``news_items`` row into the object every news surface renders."""
    return {
        'id': row['id'],
        'url': row['url'],
        'headline': row['headline'],
        'excerpt': row['excerpt'],
        'image_url': row['image_url'],
        'author': row['author'],
        'published_at': _isoformat(row['published_at']),
        'fetched_at': _isoformat(row['fetched_at']),
        'source': _source_stub(row['source_slug'], sources or {}),
        'subjects': [subject_payload(subject) for subject in subjects],
    }


def browse_payload(
    items: Sequence[Mapping[str, Any]],
    *,
    total: int,
    page: int,
    per_page: int,
    filters: Mapping[str, Any],
    followed: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Builds the ``GET /news`` page from already-shaped items.

    Takes shaped items rather than rows so the handler can cache the expensive half (the query
    and the shaping) and still compose a per-user answer on top of it -- the personalised head
    must never end up in a shared cache entry.

    ``followed`` is that head: the stories matching the caller's subscriptions, already shaped.
    It is **prepended** to the general latest with duplicates removed, rather than returned as a
    second list, because the ticker reads one ordered list and the personalisation should be
    invisible in its markup (§6.3). ``personalised`` says how many of the leading items came
    from that half, so a page that *wants* to label them still can.

    ``total_pages`` is clamped to at least 1 (matching ``comics/payload.py``), so an empty
    result reports one empty page rather than zero.
    """
    head = list(followed)
    seen = {item['id'] for item in head}
    total_pages = max(1, -(-total // per_page)) if per_page else 1
    return {
        'items': [*head, *(item for item in items if item['id'] not in seen)],
        'personalised': len(head),
        'page': page,
        'per_page': per_page,
        'total': total,
        'total_pages': total_pages,
        'filters': dict(filters),
    }
