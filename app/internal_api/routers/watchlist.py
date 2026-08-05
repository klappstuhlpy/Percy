"""Universe watchlist endpoints: the TMDB-synced catalogue and per-user progress/prefs.

Not guild-scoped -- a universe watch order is global content, and progress is per Discord
user, not per server. The dashboard renders `/watchlist` and `/watchlist/{slug}/order` from
these; `get_universe_payload` is deliberately one call that carries everything a page needs
(universe, paths, eras, every title, providers for the chosen region).

Caching: the catalogue payloads sit behind a short TTL cache. Ingest runs as a separate CLI
process (``main.py watchlist sync``), so the running bot cannot be signalled to invalidate --
the TTL, not an invalidation call, is what bounds staleness after a sync. Progress and prefs
are per-user and never cached.
"""
from __future__ import annotations

import datetime  # noqa: TC003 -- used in pydantic body models, whose annotations are evaluated at runtime
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.services.watchlist.payload import (
    credit_payloads,
    person_payload,
    person_summary,
    universe_payload,
    universe_stats,
    universe_summary,
)
from app.utils import cache

from ..dependencies import BotDep, verify_token
from ..helpers import naive_utc

if TYPE_CHECKING:
    from app.core.bot import Bot

router = APIRouter(prefix="/watchlist", tags=["Watchlist"], dependencies=[Depends(verify_token)])

#: How long a catalogue payload is served from memory. The catalogue only changes when the
#: sync CLI runs, so a few minutes of staleness is cheap next to rebuilding it per request.
CATALOGUE_TTL = 300

_SORTS = ('story', 'release')
_STATUSES = ('watched', 'skipped')


class ProgressEntry(BaseModel):
    """One title's watch state, as the client last saw it."""

    title_id: int
    status: str
    updated_at: datetime.datetime | None = None


class ProgressBody(BaseModel):
    """A bulk progress merge: the client's full local set, or a single debounced toggle."""

    entries: list[ProgressEntry] = Field(default_factory=list)


class PrefsBody(BaseModel):
    """Partial update of a user's watchlist preferences; unset fields are left alone."""

    region: str | None = None
    path_slug: str | None = None
    sort_mode: str | None = None
    show_spoiler: bool | None = None
    with_credits: bool | None = None
    hide_sub: bool | None = None
    weekly_hours: dict[str, Any] | None = None
    target_date: datetime.date | None = None


def _check_sort(sort: str) -> str:
    """Validates the ``sort`` query value before it reaches the repository."""
    if sort not in _SORTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown sort {sort!r}; expected one of {list(_SORTS)}",
        )
    return sort


def _check_region(region: str) -> str:
    """Normalises and validates a region against the ``CHAR(2)`` provider column."""
    region = region.upper()
    if len(region) != 2 or not region.isalpha():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid region {region!r}; expected a two-letter ISO 3166-1 code",
        )
    return region


async def _resolve_path(bot: Bot, slug: str, path: str | None) -> tuple[list[Any], str | None]:
    """Fetches a universe's paths and resolves which one to join importance from.

    An unknown ``path`` is a 404 rather than a silent fallback -- a typo'd path would
    otherwise render a full page with every importance tag quietly missing. With no ``path``
    given, the universe's default path is used (or none, if it declares none).
    """
    paths = await bot.db.watchlist.list_paths(slug)
    if path is None:
        return paths, next((row['slug'] for row in paths if row['is_default']), None)
    if not any(row['slug'] == path for row in paths):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown path {path!r}")
    return paths, path


@cache.cache(maxsize=CATALOGUE_TTL, strategy=cache.Strategy.TIMED)
async def get_universe_payload(bot: Bot, slug: str, path: str | None, region: str, sort: str) -> dict:
    """The whole-page payload for one universe, cached for :data:`CATALOGUE_TTL` seconds.

    Raises 404 when the universe does not exist. Three queries regardless of catalogue size:
    paths, titles (importance LEFT JOINed for ``path``), providers (batched by title id).
    """
    universe = await bot.db.watchlist.get_universe(slug)
    if universe is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown universe {slug!r}")

    paths, path_slug = await _resolve_path(bot, slug, path)
    titles = await bot.db.watchlist.list_titles(slug, path_slug=path_slug, sort=sort)
    providers = await bot.db.watchlist.list_providers([row['id'] for row in titles], region)
    return universe_payload(
        universe, paths, titles, providers, path_slug=path_slug, region=region, sort=sort)


# -- Catalogue (read) ---------------------------------------------------------


@router.get("/universes")
async def list_universes(
    bot: BotDep,
    include_unpublished: Annotated[bool, Query(description="Include universes not yet published")] = False,
) -> dict:
    """Every universe with its title count and total runtime, in display order."""
    rows = await bot.db.watchlist.list_universes(published_only=not include_unpublished)
    return {'universes': [universe_summary(row) for row in rows]}


@router.get("/universes/{slug}")
async def get_universe(
    bot: BotDep,
    slug: str,
    path: Annotated[str | None, Query(description="Curated path slug (defaults to the universe's)")] = None,
    region: Annotated[str, Query(description="ISO 3166-1 two-letter region for providers")] = "US",
    sort: Annotated[str, Query(description="story or release")] = "story",
) -> dict:
    """Universe, paths, eras and every title -- one call renders the whole watch-order page."""
    return await get_universe_payload(bot, slug, path, _check_region(region), _check_sort(sort))


@router.get("/universes/{slug}/stats")
async def get_universe_statistics(
    bot: BotDep,
    slug: str,
    path: Annotated[str | None, Query(description="Curated path slug (defaults to the universe's)")] = None,
) -> dict:
    """Totals for one path: title/movie/show counts, importance breakdown, runtime and hours."""
    if await bot.db.watchlist.get_universe(slug) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown universe {slug!r}")
    _, path_slug = await _resolve_path(bot, slug, path)
    titles = await bot.db.watchlist.list_titles(slug, path_slug=path_slug)
    return {'universe': slug, 'path': path_slug, **universe_stats(titles)}


@router.get("/titles/{universe}/{slug}")
async def get_title(
    bot: BotDep,
    universe: str,
    slug: str,
    region: Annotated[str, Query(description="ISO 3166-1 two-letter region for providers")] = "US",
    sort: Annotated[str, Query(description="Ordering the previous/next neighbours follow")] = "story",
) -> dict:
    """One title in detail: providers, the title it plays the credits of, and its neighbours.

    Neighbours come from the same ordering the page uses, so "next" on a detail page always
    matches the row below it on the watch order. ``position``/``total`` and ``path`` complete
    the same thought -- the detail page's "position N of M" strip needs the index within that
    ordering and the name of the path whose ``importance`` the title carries, neither of which
    is derivable from a title row alone.
    """
    sort = _check_sort(sort)
    title = await bot.db.watchlist.get_title(universe, slug)
    if title is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown title {universe}/{slug}")

    payload = await get_universe_payload(bot, universe, None, _check_region(region), sort)
    titles = payload['titles']
    index = next((i for i, row in enumerate(titles) if row['id'] == title['id']), None)
    if index is None:  # pragma: no cover - the title was just fetched from the same universe
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown title {universe}/{slug}")

    current = titles[index]
    credits_of = next((row for row in titles if row['id'] == current['credits_of']), None)
    return {
        'title': current,
        'credits_of': credits_of,
        'credits': credit_payloads(await bot.db.watchlist.list_credits([title['id']])),
        'previous': titles[index - 1] if index > 0 else None,
        'next': titles[index + 1] if index + 1 < len(titles) else None,
        'position': index + 1,
        'total': len(titles),
        'path': payload['path'],
        'sort': sort,
        'universe': payload['universe'],
    }


# -- People -------------------------------------------------------------------


@router.get("/people")
async def search_people(
    bot: BotDep,
    q: Annotated[str, Query(description="Name to match, trigram-similarity")],
    limit: Annotated[int, Query(ge=1, le=50, description="Maximum matches to return")] = 25,
) -> dict:
    """Person search, best match first -- backs autocomplete on both surfaces.

    Deliberately not cached: it is a per-keystroke query against one GIN index, and caching it
    would fill the shared TTL cache with one entry per prefix anybody ever typed.
    """
    rows = await bot.db.watchlist.search_people(q, limit=limit)
    return {'people': [person_summary(row) for row in rows]}


@router.get("/people/{slug}")
async def get_person(bot: BotDep, slug: str) -> dict:
    """One person and their whole filmography, undated titles first, then newest.

    An unknown slug is a real 404 -- the dashboard turns that into a not-found page, and it
    must stay distinguishable from a person who exists with no credits (an empty list).
    """
    person = await bot.db.watchlist.get_person(slug)
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown person {slug!r}")
    return person_payload(person, await bot.db.watchlist.list_person_credits(person['tmdb_person_id']))


# -- Progress / prefs ---------------------------------------------------------


@router.get("/users/{discord_id}/progress")
async def get_progress(
    bot: BotDep,
    discord_id: int,
    universe: Annotated[str | None, Query(description="Scope to one universe")] = None,
) -> dict:
    """A user's watch progress as ``{title_id: {status, updated_at}}``."""
    rows = await bot.db.watchlist.get_progress(discord_id, universe=universe)
    return {
        'entries': {
            str(row['title_id']): {'status': row['status'], 'updated_at': row['updated_at'].isoformat()}
            for row in rows
        },
    }


@router.put("/users/{discord_id}/progress")
async def put_progress(bot: BotDep, discord_id: int, body: Annotated[ProgressBody, Body()]) -> dict:
    """Merges a batch of progress entries, last-write-wins, and returns the merged set.

    The client sends its own ``updated_at`` per entry; the server keeps whichever side is
    newer, so a stale tab cannot overwrite a newer change made on another device.
    """
    for entry in body.entries:
        if entry.status not in _STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown status {entry.status!r}; expected one of {list(_STATUSES)}",
            )
    entries = [(entry.title_id, entry.status, naive_utc(entry.updated_at)) for entry in body.entries]
    await bot.db.watchlist.set_progress_bulk(discord_id, entries)
    return await get_progress(bot, discord_id, None)


@router.delete("/users/{discord_id}/progress")
async def delete_progress(
    bot: BotDep,
    discord_id: int,
    universe: Annotated[str | None, Query(description="Scope the reset to one universe")] = None,
) -> dict:
    """Resets a user's progress, optionally for one universe only."""
    return {'deleted': await bot.db.watchlist.clear_progress(discord_id, universe=universe)}


def _prefs_payload(row: Any) -> dict[str, Any]:
    """Shapes a ``watch_prefs`` row, or the defaults for a user who has never set any."""
    if row is None:
        return {
            'region': 'US', 'path_slug': None, 'sort_mode': 'story', 'show_spoiler': False,
            'with_credits': True, 'hide_sub': False, 'weekly_hours': None, 'target_date': None,
        }
    return {
        'region': row['region'],
        'path_slug': row['path_slug'],
        'sort_mode': row['sort_mode'],
        'show_spoiler': row['show_spoiler'],
        'with_credits': row['with_credits'],
        'hide_sub': row['hide_sub'],
        'weekly_hours': row['weekly_hours'],
        'target_date': row['target_date'].isoformat() if row['target_date'] else None,
    }


@router.get("/users/{discord_id}/prefs")
async def get_prefs(bot: BotDep, discord_id: int) -> dict:
    """A user's watchlist preferences, or the defaults if they have never saved any."""
    return _prefs_payload(await bot.db.watchlist.get_prefs(discord_id))


@router.patch("/users/{discord_id}/prefs")
async def patch_prefs(bot: BotDep, discord_id: int, body: Annotated[PrefsBody, Body()]) -> dict:
    """Updates the given preference fields, leaving the rest untouched."""
    values = body.model_dump(exclude_unset=True, exclude_none=True)
    if 'region' in values:
        values['region'] = _check_region(values['region'])
    if 'sort_mode' in values:
        _check_sort(values['sort_mode'])
    return _prefs_payload(await bot.db.watchlist.upsert_prefs(discord_id, values))
