"""Shaping the release overview -- one week across both media, as cards.

Pure: rows in (an ``asyncpg.Record`` or a plain dict), JSON-safe dicts out. No database, no
FastAPI, no discord.py, exactly like ``app/services/comics/payload.py`` and
``app/services/watchlist/payload.py`` -- so the card contract the Rust client is written
against is unit-testable without a pool.

A **card** is the flattest thing both media can honestly share: what it is, when it lands, the
art, and the handful of subjects a reader could follow to keep seeing it. ``key`` is the
``release_key`` the delivery table already uses (``{brand}/{slug}`` for comics,
``{universe}/{slug}`` for titles), so the page links and later marks progress with the same
string the DM path claims deliveries on -- one identity for an item across all three surfaces.

Each entry carries its :class:`~app.services.releases.subjects.Releasable` alongside its card
(:class:`Entry`), because the personalised half of the overview is computed with
:func:`~app.services.releases.subjects.match` -- the *same* matcher the hourly DM dispatch
uses. Nothing about who follows what is re-implemented here; this module only decides which
already-matched item becomes which card.

**Card subjects are the primary axes only** -- brand and series for a comic, universe and
franchise for a title. A comic's creators and characters and a title's whole cast are subjects
the fan-out genuinely matches on (and ``followed`` reflects that), but thirty chips on an
overview card is noise, not navigation; the detail pages are where the full credit list lives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.services.releases.progress import progress_payload
from app.services.releases.subjects import (
    Releasable,
    comic_releasable,
    match,
    normalise,
    title_releasable,
)

if TYPE_CHECKING:
    import datetime
    from collections.abc import Iterable, Mapping, Sequence

__all__ = (
    'BRAND_LABELS',
    'Entry',
    'build_overview',
    'comic_entry',
    'group_rows',
    'title_entry',
)

#: Display forms for the closed brand set. ``str.title()`` alone turns ``'dc'`` into ``'Dc'``,
#: and a card that misspells the publisher is worse than one that shows the raw key. Lives here
#: rather than in each consumer so the picker, the Discord autocomplete and the overview cards
#: cannot drift apart on what a brand is called.
BRAND_LABELS = {'marvel': 'Marvel', 'dc': 'DC', 'manga': 'Manga'}


@dataclass(frozen=True, slots=True)
class Entry:
    """One overview item: the releasable the matcher sees, and the card the page renders.

    They are built together and kept together on purpose -- deriving the card from the
    releasable alone would lose the display names (a series' title, a universe's name), and
    deriving the match from the card alone would lose the creators, characters and cast that
    a subscription is allowed to match on.
    """

    item: Releasable
    card: dict[str, Any]


def group_rows(rows: Iterable[Mapping[str, Any]], column: str) -> dict[int, list[Mapping[str, Any]]]:
    """Buckets batched rows by their owning id, so one query serves many entries.

    The credit and character lookups are fetched for a whole window at once; this is what turns
    those flat result sets back into per-item lists without an N+1.
    """
    grouped: dict[int, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row[column], []).append(row)
    return grouped


def _subject(subject_type: str, value: Any, label: str | None = None) -> dict[str, str] | None:
    """One card subject, or ``None`` when the column it came from is empty.

    ``subject_value`` is normalised here for the same reason the picker normalises its own: a
    client compares it against the user's stored subscriptions, and both sides of that
    comparison must have been through :func:`normalise` exactly once.
    """
    text = str(value).strip() if value is not None else ''
    if not text:
        return None
    return {'subject_type': subject_type, 'subject_value': normalise(text), 'label': label or text}


def _card(item: Releasable, *, subtitle: str | None, subjects: Sequence[dict[str, str] | None]) -> dict[str, Any]:
    """Assembles one card, dropping the subjects whose source column was empty."""
    return {
        'media': item.media,
        'key': item.key,
        'name': item.name,
        'subtitle': subtitle,
        'release_date': item.release_date.isoformat() if item.release_date is not None else None,
        'cover_url': item.cover_url,
        'url': item.url,
        'subjects': [subject for subject in subjects if subject is not None],
    }


def comic_entry(
    row: Mapping[str, Any],
    creators: Iterable[Any] = (),
    characters: Iterable[Any] = (),
) -> Entry:
    """Maps one ``comic_releases`` row (plus its credit/character rows) onto an overview entry.

    ``subtitle`` is the series and issue number -- the two facts that tell a reader *which*
    book this is when the title alone is ambiguous. A release with no series (a one-shot, or a
    title that carried no issue marker) gets ``None`` rather than a stray ``#``.

    ``cover_url`` is absolute (locg serves it that way) and ``url`` points back at the source
    listing, which is what the attribution in the plan's §9 requires.
    """
    item = comic_releasable(row, creators, characters)
    series = (row.get('series_name') or row.get('series_slug') or '') or None
    issue = (str(row.get('issue_number')).strip() if row.get('issue_number') is not None else '') or None
    subtitle = ' '.join(part for part in (series, f'#{issue}' if issue else None) if part) or None

    brand = str(row.get('brand') or '')
    return Entry(
        item=item,
        card=_card(
            item,
            subtitle=subtitle,
            subjects=[
                _subject('brand', brand, BRAND_LABELS.get(brand.lower(), brand.title() or None)),
                _subject('series', row.get('series_slug'), series),
            ],
        ),
    )


def title_entry(
    row: Mapping[str, Any],
    credits: Iterable[Any] = (),
    *,
    universe_names: Mapping[str, str] | None = None,
) -> Entry:
    """Maps one ``watch_titles`` row (plus its ``watch_credits`` rows) onto an overview entry.

    ``subtitle`` is the universe, plus the franchise (``sub_universe``) when the seed set one --
    "Marvel Cinematic Universe · Spider-Man" says more about where a film sits than its own
    title does. ``universe_names`` maps a universe slug to its display name; a slug missing
    from it falls back to the slug, so an un-passed mapping degrades to something readable
    rather than to ``None``.

    ``cover_url`` stays TMDB's relative ``poster_path`` and ``url`` stays ``None``, matching
    :func:`~app.services.releases.subjects.title_releasable` and
    ``app/services/watchlist/payload.py``: the renderer owns the CDN prefix and builds the
    on-site link from ``key``.
    """
    item = title_releasable(row, credits)
    universe = str(row.get('universe') or '')
    universe_name = (universe_names or {}).get(universe, universe) or None
    franchise = (str(row.get('sub_universe')).strip() if row.get('sub_universe') is not None else '') or None
    subtitle = ' · '.join(part for part in (universe_name, franchise) if part) or None

    return Entry(
        item=item,
        card=_card(
            item,
            subtitle=subtitle,
            subjects=[
                _subject('universe', universe, universe_name),
                _subject('franchise', franchise),
            ],
        ),
    )


def _followed(entries: Sequence[Entry], subscriptions: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The personalised half: the cards this user's subscriptions match, in catalogue order.

    The matching itself is :func:`~app.services.releases.subjects.match`, unchanged -- stream,
    muted delivery and per-user dedupe all behave exactly as they do for the DM dispatch, so
    the page and the digest can never disagree about what a subscription covers.

    One rule *is* deliberately neutralised: ``created_at`` is blanked on the way in, which
    switches off the matcher's backlog cutoff. That cutoff exists so following something today
    does not *DM* you last month's releases; a page showing this week is not spam, and hiding
    the week from someone who subscribed yesterday makes the front door look broken.
    """
    subscriptions = [{**dict(row), 'created_at': None} for row in subscriptions]
    if not subscriptions:
        return []

    matched = match((entry.item for entry in entries), subscriptions)
    keys = {(item.media, item.key) for items in matched.values() for item in items}
    return [entry.card for entry in entries if (entry.item.media, entry.item.key) in keys]


def build_overview(
    comics: Sequence[Entry],
    titles: Sequence[Entry],
    *,
    start: datetime.date,
    end: datetime.date,
    subscriptions: Iterable[Mapping[str, Any]] = (),
    progress: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Builds the ``GET /releases/overview`` payload from already-fetched entries.

    ``window`` bounds *both* halves: ``start``/``end`` is the widest span any card can fall in.
    The two media do not fill it symmetrically and that is the point -- see the endpoint's
    docstring for why comics only ever occupy ``[start, today]``.

    ``followed`` is a *subset* of the ``comics``/``titles`` already passed in, not a wider
    query: what the page highlights must be something it is also showing, or a user would see
    a card in one list and not the other. With no subscriptions it is ``[]`` -- an empty list,
    never ``None``, so a signed-out page renders the same shape as a signed-in one.

    ``progress`` is the second per-user half and rides *beside* the cards rather than on them:
    a ``status`` field inside a card would have to be written into the very objects the
    catalogue half caches and shares between every visitor, and the first request to touch one
    would leak one reader's history to the next stranger. As a separate map keyed by
    ``(media, release_key)`` -- the same identity every card already carries as ``key`` -- the
    cached half stays user-free and the renderer does the join. Empty when no rows are passed,
    with both media present either way (see :func:`~app.services.releases.progress_payload`).
    """
    entries = [*comics, *titles]
    return {
        'window': {'start': start.isoformat(), 'end': end.isoformat()},
        'comics': [entry.card for entry in comics],
        'titles': [entry.card for entry in titles],
        'followed': _followed(entries, subscriptions),
        'progress': progress_payload(progress),
    }
