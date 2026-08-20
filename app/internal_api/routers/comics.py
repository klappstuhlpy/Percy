"""Comic catalogue endpoints: browse, one release, series, and creator/character name lists.

Not guild-scoped -- the comic archive (``comic_releases`` / ``comic_creators`` /
``comic_characters``, see ``migrations/V40__comic_catalogue.sql``) is global content, same as
the watchlist universes. Reads only; ingest is the comic cog's existing 6 h refresh task
(``app/services/comics/ingest.py``), a separate process from this API.

Caching: the browse/list payloads sit behind a short TTL cache, same approach as
``routers/watchlist.py``. Ingest cannot signal this process to invalidate (it runs inside the
same bot process on its own schedule, not as an external CLI, but there is still no seam to
invalidate from) -- the TTL, not an invalidation call, is what bounds staleness after a refresh.
"""
from __future__ import annotations

import datetime  # noqa: TC003 -- used in handler Query() annotations, which FastAPI evaluates at runtime
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.services.comics.payload import (
    DEFAULT_PER_PAGE,
    MAX_PER_PAGE,
    browse_payload,
    names_payload,
    release_payload,
    series_context,
    series_detail_payload,
    series_summary_payload,
)
from app.utils import cache

from ..dependencies import BotDep, verify_token

if TYPE_CHECKING:
    from app.core.bot import Bot

router = APIRouter(prefix="/comics", tags=["Comics"], dependencies=[Depends(verify_token)])

#: How long a browse/list payload is served from memory. Ingest is a separate 6 h task (the
#: comic cog's refresh), not something a request can trigger an invalidation from, so a TTL is
#: what bounds staleness after a refresh rather than a cache.invalidate() call.
CATALOGUE_TTL = 300

#: Query ``kind`` -> the table ``ReleasesRepository.list_names`` accepts.
_NAME_KINDS = {'creators': 'comic_creators', 'characters': 'comic_characters'}


def _check_kind(kind: str) -> str:
    """Validates the ``kind`` query value before it reaches the repository."""
    table = _NAME_KINDS.get(kind)
    if table is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown kind {kind!r}; expected one of {list(_NAME_KINDS)}",
        )
    return table


@cache.cache(maxsize=CATALOGUE_TTL, strategy=cache.Strategy.TIMED)
async def _browse(
    bot: Bot,
    brand: str | None,
    series: str | None,
    creator: str | None,
    character: str | None,
    since: datetime.date | None,
    until: datetime.date | None,
    search: str | None,
    page: int,
    per_page: int,
) -> dict:
    """The ``GET /comics`` page, cached for :data:`CATALOGUE_TTL` seconds per filter set."""
    offset = (page - 1) * per_page
    rows = await bot.db.releases.list_releases(
        brand=brand, series=series, creator=creator, character=character,
        since=since, until=until, search=search, limit=per_page, offset=offset)
    total = await bot.db.releases.count_releases(
        brand=brand, series=series, creator=creator, character=character,
        since=since, until=until, search=search)
    filters = {
        'brand': brand, 'series': series, 'creator': creator, 'character': character,
        'since': since.isoformat() if since else None, 'until': until.isoformat() if until else None,
        'search': search,
    }
    return browse_payload(rows, total=total, page=page, per_page=per_page, filters=filters)


@cache.cache(maxsize=CATALOGUE_TTL, strategy=cache.Strategy.TIMED)
async def _series_list(bot: Bot, brand: str | None) -> dict:
    """The ``GET /comics/series`` list, cached for :data:`CATALOGUE_TTL` seconds."""
    rows = await bot.db.releases.list_series(brand=brand)
    return {'series': [series_summary_payload(row) for row in rows]}


@cache.cache(maxsize=CATALOGUE_TTL, strategy=cache.Strategy.TIMED)
async def _names(bot: Bot, table: str, brand: str | None, limit: int) -> dict:
    """The ``GET /comics/names`` list, cached for :data:`CATALOGUE_TTL` seconds."""
    kind = next(k for k, v in _NAME_KINDS.items() if v == table)
    rows = await bot.db.releases.list_names(table, brand=brand, limit=limit)
    return names_payload(kind, rows)


# -- Browse / series / names ---------------------------------------------------------


@router.get("")
async def list_comics(
    bot: BotDep,
    brand: Annotated[str | None, Query(description="Filter to one brand")] = None,
    series: Annotated[str | None, Query(description="Filter to one series slug")] = None,
    creator: Annotated[str | None, Query(description="Filter to one creator name")] = None,
    character: Annotated[str | None, Query(description="Filter to one character name")] = None,
    since: Annotated[datetime.date | None, Query(description="Only releases on/after this date")] = None,
    until: Annotated[datetime.date | None, Query(description="Only releases on/before this date")] = None,
    search: Annotated[str | None, Query(description="Substring match on the title")] = None,
    page: Annotated[int, Query(ge=1, description="1-based page number")] = 1,
    per_page: Annotated[int, Query(ge=1, le=MAX_PER_PAGE, description="Page size")] = DEFAULT_PER_PAGE,
) -> dict:
    """Browses the archive newest-first, filtered by any combination of the query params."""
    return await _browse(bot, brand, series, creator, character, since, until, search, page, per_page)


@router.get("/series")
async def list_series(
    bot: BotDep,
    brand: Annotated[str | None, Query(description="Filter to one brand")] = None,
) -> dict:
    """Every series (optionally scoped to a brand), with its issue count and latest release."""
    return await _series_list(bot, brand)


@router.get("/series/{slug}")
async def get_series(
    bot: BotDep,
    slug: str,
    brand: Annotated[str | None, Query(description="Scope to one brand, for a slug two brands share")] = None,
) -> dict:
    """One series: header plus its issues in reading order. 404 when the series has no issues."""
    rows = await bot.db.releases.list_series_releases(slug, brand=brand)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown series {slug!r}")
    return series_detail_payload(rows)


@router.get("/names")
async def list_names(
    bot: BotDep,
    kind: Annotated[str, Query(description="creators or characters")],
    brand: Annotated[str | None, Query(description="Filter to one brand")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict:
    """Distinct creator or character names with appearance counts."""
    table = _check_kind(kind)
    return await _names(bot, table, brand, limit)


# -- One release ---------------------------------------------------------------------


@router.get("/{brand}/{slug}")
async def get_comic(bot: BotDep, brand: str, slug: str) -> dict:
    """One release: fields, credits, characters, and its position within its series.

    404 when unknown. Registered last among the ``/comics/*`` routes -- ``/series`` and
    ``/names`` must be matched as their own literal routes first, or a request for
    ``/comics/series`` would be parsed as ``brand='series'`` here instead.
    """
    row = await bot.db.releases.get_release(brand, slug)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown release {brand}/{slug}")

    credits = await bot.db.releases.get_credits(row['id'])
    characters = await bot.db.releases.get_characters(row['id'])
    payload = release_payload(row, credits=credits, characters=characters)

    if row['series_slug']:
        # Brand-scoped: a slug is derived from a title, so a neighbour must not turn out to be
        # another publisher's book that happened to slugify the same way.
        series_rows = await bot.db.releases.list_series_releases(row['series_slug'], brand=row['brand'])
        payload['series'] = series_context(series_rows, row['id'])
    else:
        payload['series'] = {'previous': None, 'next': None, 'more_from_series': []}

    return payload
