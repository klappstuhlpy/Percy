"""Pure mapping of a TMDB ``credits`` payload onto ``watch_people`` / ``watch_credits`` rows.

Same discipline as the rest of ``app/services/watchlist/``: no network, no database, no
discord.py -- :func:`credit_rows` takes decoded JSON and gives back plain dicts, so the whole
role-normalisation and cast-cap policy is unit-testable without a pool or a token.

Each returned dict carries both halves of the write (the person's identity columns *and* the
credit's columns); :class:`~app.services.watchlist.ingest.WatchlistIngest` splits them across
the two tables. Keeping them in one flat row means the caller never has to correlate two
parallel lists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.utils.text import slugify

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = ('CAST_LIMIT', 'CREW_ROLES', 'credit_rows')

#: TMDB crew ``job`` values that map onto a stored ``watch_credits.role``. Everything else
#: (gaffers, VFX supervisors, casting) is dropped -- the table exists to answer "who made this
#: and what else are they in", and a subscription to a boom operator is not a product.
CREW_ROLES: dict[str, str] = {
    'director': 'director',
    'writer': 'writer',
    'screenplay': 'writer',
    'story': 'writer',
    'teleplay': 'writer',
}

#: How many cast members are kept per title, by TMDB billing order. A blockbuster credits 100+
#: people and the 90th-billed extra is noise in both a filmography and a subscription fan-out.
#: This is the knob to raise if a page ever needs a fuller list.
CAST_LIMIT = 20


def _person(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    """Identity columns for one credit entry, or ``None`` if it is unusable."""
    person_id = entry.get('id')
    name = (entry.get('name') or '').strip()
    if not isinstance(person_id, int) or not name:
        return None
    return {
        'tmdb_person_id': person_id,
        'name': name,
        'slug': slugify(name) or str(person_id),
        'profile_path': entry.get('profile_path'),
    }


def credit_rows(
    payload: Mapping[str, Any] | None,
    *,
    created_by: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Flattens a TMDB ``credits`` payload into one row per ``(person, role)``.

    ``created_by`` is the ``tv/{id}`` payload's show-creator list, which the credits endpoint
    does not include; it is passed separately and mapped to the ``creator`` role. Movies pass
    nothing.

    Policy applied here, all of it deliberate:

    * cast is capped at :data:`CAST_LIMIT` by TMDB's ``order`` (ties broken by list position,
      so the result is deterministic for a payload with no ``order`` at all);
    * crew is filtered to :data:`CREW_ROLES` -- an unrecognised ``job`` is dropped, not stored
      under a made-up role;
    * ``(person, role)`` is deduped, because ``watch_credits``' primary key is
      ``(title_id, person_id, role)`` and TMDB happily lists one writer under both ``Writer``
      and ``Screenplay``. The first occurrence wins, so a cast member billed twice keeps their
      better billing.
    """
    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()

    def add(entry: Mapping[str, Any], role: str, *, character: str | None, billing: int | None) -> None:
        person = _person(entry)
        if person is None:
            return
        key = (person['tmdb_person_id'], role)
        if key in seen:
            return
        seen.add(key)
        rows.append({**person, 'role': role, 'character_name': character, 'billing_order': billing})

    data = payload or {}

    cast = list(enumerate(data.get('cast') or ()))
    # Sort by TMDB's billing, then by original position — an entry with no ``order`` sorts
    # last rather than crashing the comparison against an int.
    cast.sort(key=lambda pair: (pair[1].get('order') if isinstance(pair[1].get('order'), int) else 1_000_000, pair[0]))
    for _, entry in cast[:CAST_LIMIT]:
        order = entry.get('order')
        add(
            entry, 'cast',
            character=(entry.get('character') or '').strip() or None,
            billing=order if isinstance(order, int) else None,
        )

    for entry in data.get('crew') or ():
        role = CREW_ROLES.get((entry.get('job') or '').strip().lower())
        if role is not None:
            add(entry, role, character=None, billing=None)

    for entry in created_by or ():
        add(entry, 'creator', character=None, billing=None)

    return rows
