"""Comic ingest: a fetched ``GenericComic`` -> ``comic_releases`` / ``comic_creators`` /
``comic_characters`` rows.

Same split as ``app/services/watchlist/ingest.py``: the mapping functions
(:func:`split_series`, :func:`release_row`, :func:`creator_rows`, :func:`character_rows`) are
pure -- no network, no database, unit-tested with hand-built objects -- and the only impure
part is :class:`ComicIngest`, which is the sole caller of the releases repository.

The comic cog keeps its in-memory cache (the Discord embeds render from it); this writes the
same data through to the archive so it survives a restart and the website has something to
read. Ingest is idempotent: re-running a refresh over unchanged issues rewrites the same rows.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.utils.text import slugify

if TYPE_CHECKING:
    import datetime
    from collections.abc import Iterable, Sequence

    from app.cogs.comic.models import GenericComic
    from app.database.repositories.releases import ReleasesRepository

__all__ = ('ComicIngest', 'ComicIngestReport', 'character_rows', 'creator_rows', 'release_row', 'split_series')

log = logging.getLogger(__name__)

#: ``Amazing Spider-Man #12``, ``Batman #150 (Variant)`` -> series name + issue number.
#: The issue token allows letters and dots so ``#1.MU`` and ``#3AU`` survive intact.
_ISSUE_RE = re.compile(r'^(?P<series>.+?)\s*#\s*(?P<issue>[0-9][0-9A-Za-z.\-]*)\s*(?P<suffix>\(.*\))?$')

#: Trailing volume/year qualifiers stripped from a series name so ``Batman (2016)`` and
#: ``Batman`` group together. Deliberately conservative -- only a bare 4-digit year or an
#: explicit ``Vol. N``, never an arbitrary parenthetical (``(Variant)`` is meaningful).
_SERIES_QUALIFIER_RE = re.compile(r'\s*(\(\d{4}\)|\bvol\.?\s*\d+\b)\s*$', re.IGNORECASE)


def split_series(title: str) -> tuple[str, str | None]:
    """Splits a comic title into ``(series_name, issue_number)``.

    ``"Amazing Spider-Man #12"`` -> ``("Amazing Spider-Man", "12")``. A title with no issue
    marker (collected editions, manga volumes) returns the whole title and ``None`` -- those
    are still series members, they just have no issue number to sort by.
    """
    match = _ISSUE_RE.match(title.strip())
    if match is None:
        return _SERIES_QUALIFIER_RE.sub('', title.strip()), None
    return _SERIES_QUALIFIER_RE.sub('', match['series'].strip()), match['issue']


def _brand_name(comic: GenericComic) -> str:
    return comic.brand.name if comic.brand is not None else 'UNKNOWN'


def _release_date(comic: GenericComic) -> datetime.date | None:
    return comic.date.date() if comic.date is not None else None


def release_row(comic: GenericComic) -> dict[str, Any]:
    """Maps a :class:`GenericComic` onto a ``comic_releases`` row.

    ``external_id`` is the source's own id, stringified -- locg-api hands out integers and the
    manga scraper an index, so the column is TEXT to hold both without a per-brand branch.
    ``slug`` is derived from the title and falls back to the external id for a title with no
    ASCII alphanumerics; the repository resolves the (rare) case of two issues in one brand
    slugifying the same way.
    """
    title = (comic.title or '').strip()
    series_name, issue_number = split_series(title)
    external_id = str(comic.id)
    return {
        'brand': _brand_name(comic),
        'external_id': external_id,
        'slug': slugify(title) or slugify(external_id) or external_id,
        'title': title,
        'series_slug': slugify(series_name) or None,
        'series_name': series_name or None,
        'issue_number': issue_number,
        'release_date': _release_date(comic),
        'cover_url': comic.image_url,
        'description': comic.description,
        'price': comic.price,
        'page_count': comic.page_count,
        'format': comic.comic_format,
        'publisher': comic.brand.value if comic.brand is not None else None,
        'url': comic.url or None,
    }


def creator_rows(comic: GenericComic) -> list[tuple[str, str]]:
    """Flattens ``{role: [names]}`` credits into ``(name, role)`` pairs, deduped."""
    seen: set[tuple[str, str]] = set()
    rows: list[tuple[str, str]] = []
    for role, names in (comic.creators or {}).items():
        for name in names or ():
            pair = ((name or '').strip(), (role or '').strip())
            if all(pair) and pair not in seen:
                seen.add(pair)
                rows.append(pair)
    return rows


def character_rows(comic: GenericComic) -> list[tuple[str, str | None]]:
    """Maps character entries onto ``(name, kind)`` pairs, deduped by name.

    The table's primary key is ``(release_id, name)``, so a character listed twice under two
    types keeps the first ``kind`` seen rather than raising on the second insert.
    """
    seen: set[str] = set()
    rows: list[tuple[str, str | None]] = []
    for character in comic.characters or ():
        name = ((character or {}).get('name') or '').strip()
        if name and name not in seen:
            seen.add(name)
            rows.append((name, (character or {}).get('type') or None))
    return rows


@dataclass(slots=True)
class ComicIngestReport:
    """What one :meth:`ComicIngest.ingest` call did, for the refresh log line."""

    brand: str
    seen: int = 0
    created: int = 0
    updated: int = 0
    failed: int = 0

    def __str__(self) -> str:
        return (f'{self.brand}: {self.seen} seen, {self.created} new, '
                f'{self.updated} updated, {self.failed} failed')


class ComicIngest:
    """Writes fetched comics through to the archive.

    Deliberately forgiving: one malformed issue must not cost the whole refresh, so each
    release is upserted independently and failures are counted, logged and skipped. The caller
    (the comic cog's 6 h refresh) treats ingest as best-effort -- the cache is already set by
    the time this runs, so the Discord feed works whether or not the database does.
    """

    __slots__ = ('repository',)

    def __init__(self, repository: ReleasesRepository) -> None:
        self.repository = repository

    async def ingest(self, brand: str, comics: Sequence[GenericComic] | Iterable[GenericComic]) -> ComicIngestReport:
        """Upserts ``comics`` and their creator/character rows. Never raises."""
        report = ComicIngestReport(brand=brand)
        for comic in comics:
            report.seen += 1
            try:
                release_id, created = await self.repository.upsert_release(release_row(comic))
                await self.repository.replace_creators(release_id, creator_rows(comic))
                await self.repository.replace_characters(release_id, character_rows(comic))
            except Exception as exc:
                report.failed += 1
                log.warning('Comic ingest failed for %s %r: %s', brand, comic.title, exc)
            else:
                if created:
                    report.created += 1
                else:
                    report.updated += 1
        return report
