"""Subject derivation and subscription fan-out -- the core of the releases layer.

Pure: no database, no network, no discord.py. Rows come in as mappings (an ``asyncpg.Record``
or a plain dict), subscriptions as mappings, and what comes back is plain dataclasses, so the
whole matching policy is unit-testable without a pool or a bot.

A *subject* is the string key a subscription is expressed in -- ``(media, type, value)``. It is
deliberately not a foreign key: comics and film/TV keep their own tables (their fields genuinely
differ) and meet here, which is why one ``release_subscriptions`` table serves both media.

Every value that is written or matched goes through :func:`normalise` exactly once. That single
rule is what makes ``"Pedro Pascal"``, ``"pedro  pascal"`` and ``" PEDRO PASCAL "`` one
subscription rather than three -- the repository normalises on write and this module normalises
on match, so no path can put an unnormalised key on either side of the comparison.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = (
    'COMIC_SUBJECT_TYPES',
    'STREAMS',
    'TITLE_SUBJECT_TYPES',
    'Releasable',
    'Subject',
    'comic_releasable',
    'match',
    'normalise',
    'title_releasable',
)

#: The closed set of subject types per medium. The migration deliberately carries no CHECK for
#: this (a cross-column constraint ages badly), so these live with the code that derives them.
COMIC_SUBJECT_TYPES = ('brand', 'series', 'creator', 'character')
TITLE_SUBJECT_TYPES = ('universe', 'franchise', 'person', 'character')

#: Stream values a subscription can carry. ``both`` matches whichever stream is dispatched.
STREAMS = ('releases', 'news', 'both')

_WHITESPACE_RE = re.compile(r'\s+')


def normalise(value: str) -> str:
    """Casefolds, trims and collapses inner whitespace.

    The one rule that decides whether two subject values are the same subscription. Casefold
    rather than ``lower`` because the values are user-typed names in arbitrary scripts.
    """
    return _WHITESPACE_RE.sub(' ', (value or '').strip()).casefold()


@dataclass(frozen=True, slots=True)
class Subject:
    """One subscribable key: ``(media, type, value)``, with ``value`` always normalised.

    Build one with :meth:`of` rather than the constructor whenever the value comes from a row
    or a user -- the constructor stores exactly what it is given.
    """

    media: str
    type: str
    value: str

    @classmethod
    def of(cls, media: str, type: str, value: str) -> Subject:
        """Builds a subject, normalising ``value`` on the way in."""
        return cls(media=media, type=type, value=normalise(value))


@dataclass(frozen=True, slots=True)
class Releasable:
    """The shared shape comics and titles both expose to the subscription layer.

    ``key`` is the ``release_key`` used for delivery idempotency and progress, namespaced by
    ``media``: ``{brand}/{slug}`` for comics, ``{universe}/{slug}`` for titles.

    ``first_seen`` is when *we* first learned about the item, ``release_date`` when it is out;
    :func:`match` prefers the former and falls back to the latter, which is what stops a fresh
    subscription dumping a backlog.

    ``source`` is who published the item, and it exists because the plan's §9 makes naming the
    source non-optional wherever an item is shown -- the DM included. It is set for news (the
    outlet) and left ``None`` for the two catalogues, whose attribution is a per-page constant
    rather than a per-item fact.
    """

    media: str
    key: str
    name: str
    release_date: datetime.date | None
    first_seen: datetime.datetime | None
    cover_url: str | None
    url: str | None
    subjects: frozenset[Subject]
    source: str | None = None


def _text(value: Any) -> str:
    """Trims a value that may be ``None`` (a nullable column) into a string."""
    return str(value).strip() if value is not None else ''


def _column(row: Any, key: str) -> Any:
    """Reads an optional column off a mapping-like row, or ``None`` if it carries none.

    Rows reach this module as ``asyncpg.Record`` *or* dict, and a query that selects only what
    it needs is a feature, not a bug -- a missing column is absence, not an error.
    """
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return None


def _named(entry: Any, key: str) -> str:
    """Reads ``key`` off a credit/character row, tolerating a bare string entry."""
    return entry.strip() if isinstance(entry, str) else _text(_column(entry, key))


def _subjects(media: str, pairs: Iterable[tuple[str, str]]) -> frozenset[Subject]:
    """Builds the subject set, dropping any pair whose value is empty once normalised."""
    return frozenset(s for s in (Subject.of(media, kind, value) for kind, value in pairs) if s.value)


def comic_releasable(
    release: Mapping[str, Any],
    creators: Iterable[Any] = (),
    characters: Iterable[Any] = (),
) -> Releasable:
    """Maps a ``comic_releases`` row (plus its credit and character rows) onto a releasable.

    Subjects: the brand, the series (by ``series_slug``, absent for a title that carried no
    issue marker), one per credited creator name and one per character name.
    ``creators``/``characters`` may be rows with a ``name`` column or bare strings.
    """
    brand = _text(release['brand'])
    pairs: list[tuple[str, str]] = [('brand', brand), ('series', _text(_column(release, 'series_slug')))]
    pairs += [('creator', _named(entry, 'name')) for entry in creators]
    pairs += [('character', _named(entry, 'name')) for entry in characters]
    return Releasable(
        media='comic',
        key=f'{brand}/{_text(release["slug"])}',
        name=_text(release['title']),
        release_date=_column(release, 'release_date'),
        first_seen=_column(release, 'first_seen'),
        cover_url=_column(release, 'cover_url'),
        url=_column(release, 'url'),
        subjects=_subjects('comic', pairs),
    )


def title_releasable(title: Mapping[str, Any], credits: Iterable[Any] = ()) -> Releasable:
    """Maps a ``watch_titles`` row (plus its ``watch_credits`` rows) onto a releasable.

    Subjects: the universe, the franchise (the title's ``sub_universe``, skipped when unset),
    one ``person`` per credited person **slug** -- not their display name, because the slug is
    the stable key the ``/people/:slug`` page and person autocomplete already use -- and one
    ``character`` per named cast credit.

    ``watch_titles`` has neither a ``first_seen`` nor an external ``url``: the backlog cutoff
    falls back to ``release_date``, and the link is built from ``key`` by whoever renders it.
    ``cover_url`` is TMDB's relative ``poster_path``, prefixed by the renderer as everywhere else.
    """
    universe = _text(title['universe'])
    pairs: list[tuple[str, str]] = [('universe', universe), ('franchise', _text(_column(title, 'sub_universe')))]
    for credit in credits:
        pairs.append(('person', _named(credit, 'slug')))
        if _named(credit, 'role') == 'cast':
            pairs.append(('character', _named(credit, 'character_name')))
    return Releasable(
        media='title',
        key=f'{universe}/{_text(title["slug"])}',
        name=_text(title['title']),
        release_date=_column(title, 'release_date'),
        first_seen=None,
        cover_url=_column(title, 'poster_path'),
        url=None,
        subjects=_subjects('title', pairs),
    )


def _naive_utc(value: datetime.datetime) -> datetime.datetime:
    """Drops an aware datetime to naive UTC, which is how Percy stores timestamps."""
    return value.astimezone(datetime.UTC).replace(tzinfo=None) if value.tzinfo is not None else value


def _known_since(item: Releasable) -> datetime.datetime | None:
    """When we first knew about ``item``: its ``first_seen``, else its release date."""
    if item.first_seen is not None:
        return _naive_utc(item.first_seen)
    if item.release_date is not None:
        return datetime.datetime.combine(item.release_date, datetime.time.min)
    return None


def _order(item: Releasable) -> tuple[int, datetime.date, str]:
    """Sort key: release date ascending with undated items last, then name."""
    if item.release_date is None:
        return (1, datetime.date.min, item.name)
    return (0, item.release_date, item.name)


def match(
    releasables: Iterable[Releasable],
    subscriptions: Iterable[Mapping[str, Any]],
    *,
    stream: str = 'releases',
) -> dict[int, list[Releasable]]:
    """Fans a batch of releasables out to the users subscribed to them.

    A subscription is any mapping carrying ``user_id``, ``media``, ``subject_type``,
    ``subject_value``, ``streams``, ``delivery`` and ``created_at`` -- i.e. a
    ``release_subscriptions`` row. The rules, all of them deliberate:

    1. a subscription matches when ``media``, ``subject_type`` and ``subject_value`` (compared
       after :func:`normalise`) equal one of the releasable's subjects;
    2. ``delivery = 'off'`` never matches -- that is what a blocked DM sets, and it must mute a
       subscription without deleting it;
    3. ``streams`` must be ``stream`` or ``both``. News is far higher-frequency than releases,
       so one function serves both streams and a subscriber picks which ones reach them;
    4. an item we already knew about *before* the subscription was created never matches, so
       following something today does not dump last month's backlog. "Knew about" is
       ``first_seen``, falling back to ``release_date``; an item with neither is matched rather
       than dropped, since nothing about it proves it is old;
    5. a user matching one item through three subscriptions receives it once -- deduped by
       ``key``;
    6. each user's list is sorted by release date (undated last), then name.

    Returns only users with at least one match, so a caller can iterate the result directly.
    """
    by_subject: dict[Subject, list[Releasable]] = {}
    for item in releasables:
        for subject in item.subjects:
            by_subject.setdefault(subject, []).append(item)

    matched: dict[int, dict[str, Releasable]] = {}
    for subscription in subscriptions:
        if subscription['delivery'] == 'off':
            continue
        if subscription['streams'] not in (stream, 'both'):
            continue
        subject = Subject.of(subscription['media'], subscription['subject_type'], subscription['subject_value'])
        created_at = subscription['created_at']
        if created_at is not None:
            created_at = _naive_utc(created_at)
        for item in by_subject.get(subject, ()):
            known = _known_since(item)
            if created_at is not None and known is not None and known < created_at:
                continue
            matched.setdefault(subscription['user_id'], {})[item.key] = item

    return {user_id: sorted(items.values(), key=_order) for user_id, items in matched.items()}
