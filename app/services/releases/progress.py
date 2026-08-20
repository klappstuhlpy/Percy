"""Read/watched/skipped state -- the closed status sets and the shape they are served in.

Pure: rows in (an ``asyncpg.Record`` or a plain dict), JSON-safe dicts out. No database, no
FastAPI, no discord.py, exactly like :mod:`app.services.releases.overview`.

Progress is keyed by the **same** ``(media, release_key)`` identity the delivery claim and the
overview cards use -- ``{brand}/{slug}`` for comics, ``{universe}/{slug}`` for titles (see
:mod:`app.services.releases.subjects`). One string names an item on the page, in the DM and in
the tracker, so a card can be joined against progress without a lookup table.

``release_progress`` covers both media; V39's ``watch_progress`` is deliberately *not* dropped
and keeps serving the universe order pages (the plan's §4.4). Nothing here reads or writes it.

The status sets are per medium and only overlap on ``skipped``: a comic is ``read``, a film is
``watched``. The migration's CHECK is the union of the three, because a cross-column constraint
ages badly for exactly the reason ``subject_type`` carries none -- so the *pairing* lives here,
with the code that knows what a medium is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = (
    'PROGRESS_STATUSES',
    'check_status',
    'progress_payload',
)

#: Which statuses each medium accepts. ``skipped`` is in both -- it is the "not for me" mark and
#: means the same thing either way; ``read`` and ``watched`` are the verb for their own medium.
PROGRESS_STATUSES: dict[str, tuple[str, ...]] = {
    'comic': ('read', 'skipped'),
    'title': ('watched', 'skipped'),
}


def check_status(media: str, status: str) -> str:
    """Validates a status against its medium, returning it unchanged.

    Rejects rather than translates. A ``watched`` on a comic is not a spelling of ``read``, it
    is a client that mixed up its two media -- and since ``media`` also namespaces
    ``release_key``, the row it was about to write names the *wrong item*. Silently normalising
    the status would store that row under a plausible-looking key and the mistake would surface
    much later as progress against a book the user never opened. A loud failure at the boundary
    is the cheap version of that bug.
    """
    allowed = PROGRESS_STATUSES.get(media)
    if allowed is None:
        raise ValueError(f'unknown media {media!r}; expected one of {list(PROGRESS_STATUSES)}')
    if status not in allowed:
        raise ValueError(f'status {status!r} is not valid for media {media!r}; expected one of {list(allowed)}')
    return status


def progress_payload(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Shapes ``release_progress`` rows into ``{media: {release_key: {status, updated_at}}}``.

    Nested by medium rather than flattened onto one key, because ``release_key`` is only unique
    *within* a medium -- flattening would need a separator that a slug is allowed to contain.

    Every medium is always present, empty if the user has no rows for it, so a renderer can
    index ``progress[card['media']]`` without a guard and a signed-out page gets the same shape
    as a signed-in one. This is the same map the read surfaces join against and the same one
    ``GET /releases/users/{id}/progress`` returns -- one shape, not two.
    """
    payload: dict[str, dict[str, Any]] = {media: {} for media in PROGRESS_STATUSES}
    for row in rows:
        updated = row['updated_at']
        payload.setdefault(row['media'], {})[row['release_key']] = {
            'status': row['status'],
            'updated_at': updated.isoformat() if updated is not None else None,
        }
    return payload
