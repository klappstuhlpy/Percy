"""Pure selection of one trailer from a TMDB ``videos`` payload, and its playable URLs.

Same discipline as :mod:`app.services.watchlist.people`: no network, no database, no
discord.py -- :func:`select_trailer` takes decoded JSON and gives back a plain dict, so the
whole precedence policy is unit-testable without a token or a pool.

TMDB returns a dozen videos for a well-covered film -- teasers, featurettes, clips, dubbed
trailers, fan uploads -- in mixed languages. The page shows exactly one (the plan's §6.2 item
7), so *which* one is the entire decision, and it has to be deterministic: a re-sync that
picked a different trailer for the same payload would churn the column for no reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ('SITES', 'TRAILER_TYPES', 'select_trailer', 'trailer_urls')

#: Video ``site`` values we can turn into a URL, best first, mapped to their canonical
#: spelling and their (watch, embed) URL templates. A site we cannot link *or* embed is not a
#: trailer we can render, so it is dropped rather than stored as a dead key.
SITES: dict[str, tuple[str, str, str]] = {
    'youtube': ('YouTube', 'https://www.youtube.com/watch?v={key}', 'https://www.youtube.com/embed/{key}'),
    'vimeo': ('Vimeo', 'https://vimeo.com/{key}', 'https://player.vimeo.com/video/{key}'),
}

#: Video ``type`` values that count as a trailer, best first. Everything else TMDB publishes
#: (``Clip``, ``Featurette``, ``Behind the Scenes``, ``Bloopers``, ``Opening Credits``) is a
#: different promise to the reader than a button labelled "Trailer".
TRAILER_TYPES: tuple[str, ...] = ('Trailer', 'Teaser')

#: ``iso_639_1`` values treated as "no language barrier": English, or unspecified.
_NEUTRAL_LANGUAGES = ('en', '')


def _text(entry: Mapping[str, Any], field: str) -> str:
    """A stripped string field, or ``''`` for anything that is not a non-empty string."""
    value = entry.get(field)
    return value.strip() if isinstance(value, str) else ''


def _site_key(entry: Mapping[str, Any]) -> str:
    return _text(entry, 'site').lower()


def _type_of(entry: Mapping[str, Any]) -> str:
    """The entry's ``type`` in :data:`TRAILER_TYPES`' canonical casing, or ``''``."""
    value = _text(entry, 'type').lower()
    return next((known for known in TRAILER_TYPES if known.lower() == value), '')


def _usable(entry: Any) -> bool:
    """Whether an entry is a trailer we could actually render (see :data:`SITES`)."""
    return (
        isinstance(entry, dict)
        and bool(_text(entry, 'key'))
        and _site_key(entry) in SITES
        and bool(_type_of(entry))
    )


def _published(entry: Mapping[str, Any]) -> str:
    """``published_at`` as a sortable string; a missing/odd value sorts oldest.

    TMDB emits a uniform ``2019-12-16T14:00:15.000Z``, which orders correctly as plain text --
    so this deliberately does not parse, and a malformed value degrades to "oldest" instead of
    raising in the middle of a sync.
    """
    return _text(entry, 'published_at')


def _rank(entry: Mapping[str, Any]) -> tuple[int, int, int, int]:
    """Categorical precedence for one entry; lower is better (see :func:`select_trailer`)."""
    return (
        TRAILER_TYPES.index(_type_of(entry)),
        0 if entry.get('official') is True else 1,
        list(SITES).index(_site_key(entry)),
        0 if _text(entry, 'iso_639_1').lower() in _NEUTRAL_LANGUAGES else 1,
    )


def select_trailer(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Picks the single best trailer from a TMDB ``{movie,tv}/{id}/videos`` payload.

    Returns ``{'trailer_site', 'trailer_key', 'trailer_name'}`` -- the ``watch_titles`` columns
    from ``V44__trailers.sql`` -- or ``None`` when nothing qualifies. ``None`` is the normal
    answer for plenty of titles, not an error: an unreleased film often has no video at all.

    Entries are first discarded outright when they are unrenderable -- not a dict, no ``key``,
    a ``site`` outside :data:`SITES` (we could neither link nor embed it), or a ``type`` outside
    :data:`TRAILER_TYPES` (a featurette must not end up behind a "Trailer" button).

    The survivors are ordered by this precedence, most significant first:

    1. ``type``: ``Trailer`` beats ``Teaser``.
    2. ``official``: a publisher upload beats a channel repost.
    3. ``site``: ``YouTube`` beats ``Vimeo`` -- both embed, but YouTube is where the official
       trailers actually are, so preferring it keeps the common page uniform.
    4. language: ``iso_639_1`` of ``en`` or unset beats any other, so a dubbed teaser never
       displaces the original trailer.
    5. ``published_at``: newest first -- a re-cut trailer supersedes the announcement one.
    6. the payload's own order, for a full tie: stable, so the same payload always wins the
       same way.

    Language sits above recency deliberately: rule 5 exists to prefer the *later* trailer for
    the same film, not to hand a French teaser the page because it shipped a week after the
    English one.
    """
    candidates = [entry for entry in (payload or {}).get('results') or () if _usable(entry)]
    if not candidates:
        return None

    # Least-significant rule first, then `min` over the categorical ranks: Python's sort is
    # stable (and stays stable under `reverse=True`), so entries tied on every categorical rule
    # keep the recency order, and entries tied on that keep the payload's order.
    by_recency = sorted(candidates, key=_published, reverse=True)
    best = min(by_recency, key=_rank)
    return {
        'trailer_site': SITES[_site_key(best)][0],
        'trailer_key': _text(best, 'key'),
        'trailer_name': _text(best, 'name') or None,
    }


def trailer_urls(site: str, key: str) -> tuple[str, str] | None:
    """``(watch_url, embed_url)`` for a stored trailer, or ``None`` for a site we cannot render.

    The embed URL carries no playback parameters on purpose -- autoplay is forbidden (§6.2) and
    the page, not this function, decides when the iframe is created.
    """
    entry = SITES.get(site.strip().lower())
    if entry is None or not key:
        return None
    _, watch, embed = entry
    return watch.format(key=key), embed.format(key=key)
