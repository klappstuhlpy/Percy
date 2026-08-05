"""Catalogue rows -> the tagger's vocabulary.

Pure: rows in (an ``asyncpg.Record`` or a plain dict), :class:`~app.services.news.tagging.Term`
objects out. No database, no network -- the caller fetches the rows through the repositories it
already has and hands them over, exactly as ``app/services/comics/ingest.py`` takes fetched
comics rather than fetching them.

Why a builder rather than a query
---------------------------------
Every subject a subscription can name has **two** strings, and confusing them is the one
mistake that produces a subscription nothing ever matches:

* the **key** -- what is stored in ``release_subscriptions.subject_value`` and
  ``news_subjects.subject_value``. For ``series``, ``universe`` and ``person`` it is a *slug*
  (``x-men``, ``mcu``, ``pedro-pascal``); for ``creator``, ``character`` and ``franchise`` it is
  the name itself.
* the **name** -- the display form a headline actually contains. ``"X-Men"``, not ``"x-men"``.

:func:`~app.services.news.tagging.build_terms` takes both for exactly this reason, and this
module is the one place that knows which catalogue column is which. Matching a slug against
prose would fire on nothing; storing a name where a slug belongs would fire on the wrong key.

What is deliberately **not** in the vocabulary
----------------------------------------------
``brand``. The three brands are a whole-publisher follow, and ``"Marvel"`` appears in a large
fraction of entertainment headlines -- tagging every one of them would turn a brand follower's
news stream into the feed itself. Brand followers get the *release* stream, which is what a
whole-publisher subscription is for; a brand term can be added here the day someone asks for it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.services.news.tagging import build_terms

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from app.services.news.tagging import Term

__all__ = ('catalogue_terms',)


def _value(row: Mapping[str, Any], key: str) -> str:
    """Reads a column as a trimmed string, tolerating a NULL."""
    try:
        raw = row[key]
    except (KeyError, TypeError, IndexError):
        return ''
    return str(raw).strip() if raw is not None else ''


def _terms(media: str, subject_type: str, rows: Iterable[Mapping[str, Any]],
           key_column: str, name_column: str) -> list[Term]:
    """Builds the terms for one catalogue source, skipping rows missing either string.

    A row whose name column is empty falls back to its key -- for ``creator``/``character``
    those are the same column anyway, and for a slugged type an unnamed row is better matched
    on its slug (which will simply never fire) than dropped silently from the fan-out.
    """
    built: list[Term] = []
    for row in rows:
        key = _value(row, key_column)
        if not key:
            continue
        built.extend(build_terms(media, subject_type, key, _value(row, name_column) or key))
    return built


def catalogue_terms(
    *,
    series: Iterable[Mapping[str, Any]] = (),
    creators: Iterable[Mapping[str, Any]] = (),
    characters: Iterable[Mapping[str, Any]] = (),
    universes: Iterable[Mapping[str, Any]] = (),
    franchises: Iterable[Mapping[str, Any]] = (),
    people: Iterable[Mapping[str, Any]] = (),
) -> list[Term]:
    """Builds every term a polling run tags against, from the rows the repositories return.

    The keyword names match the repository methods that feed them
    (:meth:`~app.database.repositories.releases.ReleasesRepository.list_series`,
    ``list_names``, :meth:`~app.database.repositories.watchlist.WatchlistRepository.list_universes`,
    ``list_franchises``, ``list_people``), so the cog's wiring reads as a list of sources rather
    than as a mapping problem.

    Terms that fail the tagger's specificity rules -- a one-token common word, a two-letter
    name -- are dropped by :func:`~app.services.news.tagging.build_terms` on the way in, so the
    result is safe to hand straight to :func:`~app.services.news.tagging.compile_terms`.
    """
    return [
        *_terms('comic', 'series', series, 'series_slug', 'series_name'),
        *_terms('comic', 'creator', creators, 'name', 'name'),
        *_terms('comic', 'character', characters, 'name', 'name'),
        *_terms('title', 'universe', universes, 'slug', 'name'),
        *_terms('title', 'franchise', franchises, 'name', 'name'),
        *_terms('title', 'person', people, 'slug', 'name'),
    ]
