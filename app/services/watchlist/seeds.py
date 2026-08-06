"""Watchlist seed format: hand-curated TOML files describing one franchise ("universe").

Pure parsing + validation only -- no network, no database. A seed pairs curation-only data
(paths, eras, per-title story/release ordering, importance, spoilers, ...) with a TMDB
identifier per title; ``app/services/watchlist/ingest.py`` is the only place that fetches
TMDB metadata and writes rows, using :class:`UniverseSeed` as its input.

See ``.superpowers/sdd/CLAUDE_WATCHLIST_PLAN/task-4-brief.md`` for the authoritative field
list and validation rules this module implements.
"""

from __future__ import annotations

import datetime
import tomllib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = (
    'DiscoverSeed',
    'EraSeed',
    'PathSeed',
    'SeedError',
    'TitleSeed',
    'UniverseSeed',
    'accent_to_int',
    'load_seed',
)

#: Default accent colour when a seed omits ``accent``. Not the same value as
#: ``watch_universes.accent``'s SQL column default (15082537, i.e. ``#E62429`` -- see
#: ``migrations/V38__watchlist_core.sql``); that default only applies to rows written outside
#: this seed pipeline, since :func:`accent_to_int` always resolves this value explicitly.
_DEFAULT_ACCENT = '#D97757'

_TMDB_TYPES = ('movie', 'tv')
_KINDS = ('movie', 'tv', 'special')
_IMPORTANCE_VALUES = ('essential', 'recommended', 'optional')

#: The three mutually exclusive ways a ``[[discover]]`` block can name a TMDB query.
_DISCOVER_SOURCES = ('keyword', 'company', 'collection')


class SeedError(Exception):
    """Raised by :func:`load_seed` carrying every validation problem found, not just the first."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__('; '.join(problems))


@dataclass(frozen=True, slots=True)
class PathSeed:
    """A curated viewing path (``[[paths]]``) through a universe."""

    slug: str
    name: str
    description: str | None = None
    default: bool = False


@dataclass(frozen=True, slots=True)
class EraSeed:
    """A named viewing era (``[[eras]]``), e.g. an MCU phase."""

    key: str
    name: str
    order: int = 0


@dataclass(frozen=True, slots=True)
class TitleSeed:
    """One curated title (``[[titles]]``): a TMDB identifier plus curation-only fields.

    ``era``/``tmdb_type``/``tmdb_id`` are only ``None``/invalid transiently while
    :func:`load_seed` is still collecting validation problems -- a :class:`UniverseSeed`
    returned successfully always has these populated and valid.
    """

    tmdb_type: str
    tmdb_id: int
    era: str | None
    story: int
    release: int
    kind: str | None = None
    importance: dict[str, str] = field(default_factory=dict)
    seasons: tuple[int, ...] | None = None
    milestone: bool = False
    instruction: str | None = None
    context: str | None = None
    spoiler: str | None = None
    sub_universe: str | None = None
    credits_of: int | None = None

    @property
    def resolved_kind(self) -> str:
        """``kind`` if the seed set one, else derived from ``tmdb_type``."""
        return self.kind or self.tmdb_type


@dataclass(frozen=True, slots=True)
class DiscoverSeed:
    """One ``[[discover]]`` block: a TMDB query whose results join the universe unattended.

    The complement to :class:`TitleSeed` -- a hand-written block for the titles whose story
    order, context and importance are worth curating, one query for the long tail nobody
    wants to retype every time a franchise ships something. Exactly one of
    ``keyword``/``company``/``collection`` names the query; the rest narrows or labels what
    it returns.

    Expansion (paging TMDB, filtering, numbering) lives in
    :class:`~app.services.watchlist.ingest.WatchlistIngest` -- this module stays network-free.
    ``kind`` is only invalid transiently while :func:`load_seed` is still collecting problems.
    """

    kind: str
    keyword: int | None = None
    company: int | None = None
    collection: int | None = None
    era: str | None = None
    importance: str = 'optional'
    min_date: datetime.date | None = None
    max_date: datetime.date | None = None
    exclude: frozenset[int] = frozenset()

    @property
    def source(self) -> tuple[str, int]:
        """``('keyword', 180547)`` -- which of the three queries this block names, and its id."""
        for name in _DISCOVER_SOURCES:
            value: int | None = getattr(self, name)
            if value is not None:
                return name, value
        return '?', 0  # unreachable for a returned seed; load_seed rejects a block with no source


@dataclass(frozen=True, slots=True)
class UniverseSeed:
    """A fully parsed and validated ``data/universes/<slug>.toml`` seed."""

    slug: str
    name: str
    short_name: str
    tagline: str | None = None
    accent: str = _DEFAULT_ACCENT
    logo_url: str | None = None
    backdrop_url: str | None = None
    sort_order: int = 0
    published: bool = False
    paths: tuple[PathSeed, ...] = ()
    eras: tuple[EraSeed, ...] = ()
    titles: tuple[TitleSeed, ...] = ()
    discover: tuple[DiscoverSeed, ...] = ()


def accent_to_int(value: str) -> int:
    """``"#E62429"`` -> ``15082537`` -- the ``watch_universes.accent`` column is an integer."""
    return int(value.removeprefix('#'), 16)


def _is_hex_color(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith('#') or len(value) != 7:
        return False
    try:
        int(value[1:], 16)
    except ValueError:
        return False
    return True


def _title_ident(raw: Mapping[str, Any]) -> str:
    return f'{raw.get("tmdb_type", "?")}:{raw.get("tmdb_id", "?")}'


def _require_int(raw: Mapping[str, Any], key: str, default: int, *, ident: str, filename: str, problems: list[str]) -> int:
    """Reads ``raw[key]`` (falling back to ``default``), rejecting non-``int`` values (``bool`` included).

    A network-free ``lint`` is only useful if it catches what would otherwise die at insert
    against an ``INTEGER`` column -- ``story = 100.5`` or ``story = "100"`` must fail here, not
    silently coerce or wait for Postgres.
    """
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        problems.append(f'{filename}: {ident} has non-integer {key} {value!r}')
        return default
    return value


def _parse_paths(raw_paths: list[Any], filename: str, problems: list[str]) -> tuple[tuple[PathSeed, ...], set[str]]:
    seen: set[str] = set()
    defaults = 0
    result: list[PathSeed] = []

    for raw in raw_paths:
        slug = raw.get('slug', '')
        if not slug:
            problems.append(f'{filename}: a path is missing "slug"')
        elif slug in seen:
            problems.append(f'{filename}: duplicate path slug {slug!r}')
        seen.add(slug)

        is_default = bool(raw.get('default', False))
        defaults += is_default

        result.append(PathSeed(slug=slug, name=raw.get('name', ''), description=raw.get('description'), default=is_default))

    if defaults > 1:
        problems.append(f'{filename}: more than one path has default = true')

    return tuple(result), seen


def _parse_eras(raw_eras: list[Any], filename: str, problems: list[str]) -> tuple[tuple[EraSeed, ...], set[str]]:
    seen: set[str] = set()
    result: list[EraSeed] = []

    for raw in raw_eras:
        key = raw.get('key', '')
        if not key:
            problems.append(f'{filename}: an era is missing "key"')
        elif key in seen:
            problems.append(f'{filename}: duplicate era key {key!r}')
        seen.add(key)

        order = _require_int(raw, 'order', 0, ident=f'era {key!r}', filename=filename, problems=problems)
        result.append(EraSeed(key=key, name=raw.get('name', ''), order=order))

    return tuple(result), seen


def _parse_titles(
    raw_titles: list[Any], filename: str, problems: list[str], *, path_slugs: set[str], era_keys: set[str],
) -> tuple[TitleSeed, ...]:
    present_ids = {raw['tmdb_id'] for raw in raw_titles if isinstance(raw.get('tmdb_id'), int)}
    seen_keys: set[tuple[Any, Any]] = set()
    result: list[TitleSeed] = []

    for raw in raw_titles:
        ident = _title_ident(raw)
        tmdb_type = raw.get('tmdb_type')
        tmdb_id = raw.get('tmdb_id')

        if tmdb_type not in _TMDB_TYPES:
            problems.append(f'{filename}: title {ident} has invalid tmdb_type {tmdb_type!r} (must be movie|tv)')
        if not isinstance(tmdb_id, int):
            problems.append(f'{filename}: title {ident} is missing a valid "tmdb_id"')

        key = (tmdb_type, tmdb_id)
        if key in seen_keys:
            problems.append(f'{filename}: duplicate title (tmdb_type, tmdb_id) {ident}')
        seen_keys.add(key)

        era = raw.get('era')
        if era is None:
            problems.append(f'{filename}: title {ident} is missing "era"')
        elif era not in era_keys:
            problems.append(f'{filename}: title {ident} references undeclared era {era!r}')

        kind = raw.get('kind')
        if kind is not None and kind not in _KINDS:
            problems.append(f'{filename}: title {ident} has invalid kind {kind!r} (must be movie|tv|special)')

        seasons = raw.get('seasons')
        if seasons is not None and tmdb_type == 'movie':
            problems.append(f'{filename}: title {ident} sets "seasons" but is a movie')

        importance: dict[str, str] = dict(raw.get('importance') or {})
        for path_slug, level in importance.items():
            if path_slug not in path_slugs:
                problems.append(f'{filename}: title {ident} importance references undeclared path {path_slug!r}')
            if level not in _IMPORTANCE_VALUES:
                problems.append(f'{filename}: title {ident} importance {level!r} for {path_slug!r} is invalid')

        credits_of = raw.get('credits_of')
        if credits_of is not None and credits_of not in present_ids:
            problems.append(f'{filename}: title {ident} credits_of {credits_of!r} is not a tmdb_id in this seed')

        story = _require_int(raw, 'story', 0, ident=f'title {ident}', filename=filename, problems=problems)
        release = _require_int(raw, 'release', 0, ident=f'title {ident}', filename=filename, problems=problems)

        result.append(TitleSeed(
            tmdb_type=tmdb_type,
            tmdb_id=tmdb_id,
            era=era,
            story=story,
            release=release,
            kind=kind,
            importance=importance,
            seasons=tuple(seasons) if seasons is not None else None,
            milestone=bool(raw.get('milestone', False)),
            instruction=raw.get('instruction'),
            context=raw.get('context'),
            spoiler=raw.get('spoiler'),
            sub_universe=raw.get('sub_universe'),
            credits_of=credits_of,
        ))

    return tuple(result)


def _parse_iso_date(
    raw: Mapping[str, Any], key: str, *, ident: str, filename: str, problems: list[str],
) -> datetime.date | None:
    """Reads an optional ``"YYYY-MM-DD"`` string. A bare TOML date is rejected on purpose --
    ``tomllib`` hands those back as ``datetime.date`` already, but accepting both shapes would
    make two spellings of the same field valid and neither obviously canonical.
    """
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            pass
    problems.append(f'{filename}: {ident} has invalid {key} {value!r} (must be an ISO date string, e.g. "2008-01-01")')
    return None


def _parse_discover(
    raw_discover: list[Any], filename: str, problems: list[str], *, era_keys: set[str],
) -> tuple[DiscoverSeed, ...]:
    result: list[DiscoverSeed] = []

    for index, raw in enumerate(raw_discover):
        ident = f'discover[{index}]'

        kind = raw.get('kind')
        if kind not in _TMDB_TYPES:
            problems.append(f'{filename}: {ident} has invalid kind {kind!r} (must be movie|tv)')

        sources: dict[str, int] = {}
        for name in _DISCOVER_SOURCES:
            value = raw.get(name)
            if value is None:
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                problems.append(f'{filename}: {ident} has invalid {name} {value!r} (must be a positive integer)')
                continue
            sources[name] = value
        if len(sources) != 1:
            problems.append(
                f'{filename}: {ident} must set exactly one of keyword/company/collection, got {len(sources)}'
            )

        era = raw.get('era')
        if era is not None and era not in era_keys:
            problems.append(f'{filename}: {ident} references undeclared era {era!r}')

        importance = raw.get('importance', 'optional')
        if importance not in _IMPORTANCE_VALUES:
            problems.append(f'{filename}: {ident} has invalid importance {importance!r}')

        # An exclusion that isn't an integer never matches a tmdb_id, so it would silently
        # exclude nothing -- exactly the class of mistake a network-free lint exists to catch.
        raw_exclude = raw.get('exclude') or []
        if not all(isinstance(item, int) and not isinstance(item, bool) for item in raw_exclude):
            problems.append(f'{filename}: {ident} exclude {raw_exclude!r} must be a list of integer tmdb_ids')
            raw_exclude = []

        result.append(DiscoverSeed(
            kind=kind,
            keyword=sources.get('keyword'),
            company=sources.get('company'),
            collection=sources.get('collection'),
            era=era,
            importance=importance,
            min_date=_parse_iso_date(raw, 'min_date', ident=ident, filename=filename, problems=problems),
            max_date=_parse_iso_date(raw, 'max_date', ident=ident, filename=filename, problems=problems),
            exclude=frozenset(raw_exclude),
        ))

    return tuple(result)


def _parse_universe(raw: Mapping[str, Any], filename: str, problems: list[str]) -> UniverseSeed:
    problems.extend(
        f'{filename}: missing required field {required!r}'
        for required in ('slug', 'name', 'short_name')
        if not raw.get(required)
    )

    accent = raw.get('accent', _DEFAULT_ACCENT)
    if not _is_hex_color(accent):
        problems.append(f'{filename}: accent {accent!r} is not a #RRGGBB hex string')

    paths, path_slugs = _parse_paths(raw.get('paths') or [], filename, problems)
    eras, era_keys = _parse_eras(raw.get('eras') or [], filename, problems)
    titles = _parse_titles(raw.get('titles') or [], filename, problems, path_slugs=path_slugs, era_keys=era_keys)
    discover = _parse_discover(raw.get('discover') or [], filename, problems, era_keys=era_keys)
    sort_order = _require_int(raw, 'sort_order', 0, ident='universe', filename=filename, problems=problems)

    return UniverseSeed(
        slug=raw.get('slug', ''),
        name=raw.get('name', ''),
        short_name=raw.get('short_name', ''),
        tagline=raw.get('tagline'),
        accent=accent,
        logo_url=raw.get('logo_url'),
        backdrop_url=raw.get('backdrop_url'),
        sort_order=sort_order,
        published=raw.get('published', False),
        paths=paths,
        eras=eras,
        titles=titles,
        discover=discover,
    )


def load_seed(path: Path) -> UniverseSeed:
    """Parse and validate one seed file. Raises :class:`SeedError` with every problem found."""
    with path.open('rb') as fp:
        try:
            raw = tomllib.load(fp)
        except tomllib.TOMLDecodeError as exc:
            raise SeedError([f'{path.name}: invalid TOML: {exc}']) from exc

    problems: list[str] = []
    universe = _parse_universe(raw, path.name, problems)
    if problems:
        raise SeedError(problems)
    return universe
