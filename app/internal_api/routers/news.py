"""News endpoints: the stream, the per-subject strip, and the source list.

Not guild-scoped -- a story belongs to the catalogue, not to a server (``news_sources`` /
``news_items`` / ``news_subjects``, see ``migrations/V43__news.sql``). Reads only: ingest is the
``Releases`` cog's polling loop and ``main.py news poll``, both separate from this API.

Every item goes out with its **source name and a link to the original**. That is not a payload
convenience -- the plan's §9 makes naming the source and linking out a condition of storing the
story at all, so a client that renders this payload cannot accidentally strip the attribution.
The article body is never here because it was never stored: ``app/services/news/feeds.py`` caps
the excerpt on the way in.

Caching, and where the line falls
---------------------------------
The catalogue half -- the query, the source lookup and the tag lookup for a page -- sits behind
the same 300 s ``Strategy.TIMED`` cache as ``routers/comics.py`` and ``routers/watchlist.py``,
for the same reason: polling runs on its own schedule in a task loop with no seam a request can
invalidate from, so the TTL (not an invalidation call) is what bounds staleness after a poll.

Anything keyed on ``discord_id`` is **never** cached. A user's subscriptions are per-user mutable
state with no getter to invalidate, and a follow toggled a second ago must be reflected on the
very next read -- the same rule ``routers/releases.py`` follows. So the personalised head of
``GET /news`` is computed per request and merged onto the shared, cached tail; a subscription can
never leak into an entry the next stranger is served.
"""
from __future__ import annotations

import datetime  # noqa: TC003 -- used in handler Query() annotations, which FastAPI evaluates at runtime
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.services.news.payload import browse_payload, item_payload, source_payload
from app.services.releases import COMIC_SUBJECT_TYPES, TITLE_SUBJECT_TYPES
from app.utils import cache

from ..dependencies import BotDep, verify_token

if TYPE_CHECKING:
    import asyncpg

    from app.core.bot import Bot

router = APIRouter(prefix="/news", tags=["News"], dependencies=[Depends(verify_token)])

#: How long a page of the stream is served from memory. Same value and same reasoning as the
#: sibling catalogue routers -- polling is a task loop, not something a request can invalidate.
CATALOGUE_TTL = 300

#: Paging bounds. ``news_items`` is the highest-volume table in the catalogue, so the ceiling is
#: the repository's own ``MAX_PAGE_SIZE`` rather than something more generous.
DEFAULT_PER_PAGE = 25
MAX_PER_PAGE = 100

#: How many personalised stories lead the stream. Enough to fill a ticker without pushing the
#: general latest off the first page.
MAX_FOLLOWED = 12

#: Default and ceiling for the detail pages' related-news strip (§6.2's section 6b: "the three
#: most recent tagged stories").
DEFAULT_RELATED = 3
MAX_RELATED = 10

Media = Literal['comic', 'title']
SubjectType = Literal['brand', 'series', 'creator', 'character', 'universe', 'franchise', 'person']

#: Which subject types each medium accepts, taken from the service that derives them so this
#: router and the fan-out cannot drift. Mirrors ``routers/releases.py``.
SUBJECT_TYPES: dict[str, tuple[str, ...]] = {'comic': COMIC_SUBJECT_TYPES, 'title': TITLE_SUBJECT_TYPES}


def _check_subject_type(media: str, subject_type: str) -> None:
    """Validates a ``subject_type`` against its medium.

    The two closed sets overlap but are not equal, and pydantic's ``Literal`` can only check the
    union -- ``media='comic', subject_type='person'`` would pass there and then match nothing,
    which reads to a caller as "no news" rather than as the mistake it is.
    """
    allowed = SUBJECT_TYPES[media]
    if subject_type not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"subject_type {subject_type!r} is not valid for media {media!r}; expected one of {list(allowed)}",
        )


def _group_subjects(rows: list[asyncpg.Record]) -> dict[int, list[asyncpg.Record]]:
    """Buckets batched tag rows by their story id, so one query serves a whole page."""
    grouped: dict[int, list[asyncpg.Record]] = {}
    for row in rows:
        grouped.setdefault(row['news_id'], []).append(row)
    return grouped


async def _shape(bot: Bot, rows: list[asyncpg.Record]) -> list[dict[str, Any]]:
    """Turns story rows into payload items, resolving sources and tags in two batched queries."""
    if not rows:
        return []
    sources = {row['slug']: row for row in await bot.db.news.list_sources(enabled_only=False)}
    subjects = _group_subjects(await bot.db.news.subjects_for_items([row['id'] for row in rows]))
    return [item_payload(row, sources=sources, subjects=subjects.get(row['id'], ())) for row in rows]


@cache.cache(maxsize=CATALOGUE_TTL, strategy=cache.Strategy.TIMED)
async def _page(
    bot: Bot,
    media: str | None,
    subject: tuple[str, str] | None,
    source: str | None,
    since: datetime.datetime | None,
    search: str | None,
    page: int,
    per_page: int,
) -> tuple[list[dict[str, Any]], int]:
    """One page of the stream plus its total, cached for :data:`CATALOGUE_TTL` seconds.

    Nothing user-specific is read in here and nothing user-specific may ever be: this entry is
    shared between every caller asking for the same filters.
    """
    rows = await bot.db.news.list_news(
        media=media, subject=subject, source=source, since=since, search=search,
        limit=per_page, offset=(page - 1) * per_page)
    total = await bot.db.news.count_news(
        media=media, subject=subject, source=source, since=since, search=search)
    return await _shape(bot, rows), total


@cache.cache(maxsize=CATALOGUE_TTL, strategy=cache.Strategy.TIMED)
async def _related(bot: Bot, media: str, subject_type: str, subject_value: str, limit: int) -> list[dict[str, Any]]:
    """The related-news strip for one subject, cached for :data:`CATALOGUE_TTL` seconds."""
    rows = await bot.db.news.list_news(media=media, subject=(subject_type, subject_value), limit=limit)
    return await _shape(bot, rows)


async def _followed(bot: Bot, discord_id: int, limit: int) -> list[dict[str, Any]]:
    """The personalised head: recent stories tagged with anything this user follows.

    Never cached, and deliberately built from **every** subscription the user has -- including
    the ones whose ``streams`` is ``releases`` and the ones muted with ``delivery = 'off'``.
    Those two are *delivery* preferences: they decide what Percy pushes into a DM, not what a
    page shows about the things you told it you care about. Hiding a followed subject's news
    from the news page because the user asked not to be DMed about it would be a different
    feature wearing the same switch.
    """
    subscriptions = await bot.db.releases.list_subscriptions(discord_id)
    subjects = [(row['media'], row['subject_type'], row['subject_value']) for row in subscriptions]
    if not subjects:
        return []

    # One row per (story, matching subject), so a story matching three of this user's follows
    # comes back three times -- deduped here by id, keeping the newest-first order the query
    # already imposed.
    seen: set[int] = set()
    rows: list[asyncpg.Record] = []
    for row in await bot.db.news.news_for_subjects(subjects, limit=limit * 4):
        if row['id'] in seen:
            continue
        seen.add(row['id'])
        rows.append(row)
        if len(rows) >= limit:
            break
    return await _shape(bot, rows)


# -- The stream ---------------------------------------------------------------


@router.get("")
async def list_news(
    bot: BotDep,
    media: Annotated[Media | None, Query(description="Only stories tagged in this catalogue")] = None,
    subject_type: Annotated[SubjectType | None, Query(description="With subject_value, one tag")] = None,
    subject_value: Annotated[str | None, Query(description="The subject key, as the picker gave it")] = None,
    source: Annotated[str | None, Query(description="Only this source slug")] = None,
    since: Annotated[datetime.datetime | None, Query(description="Only stories published on/after this")] = None,
    search: Annotated[str | None, Query(description="Substring match on the headline")] = None,
    page: Annotated[int, Query(ge=1, description="1-based page number")] = 1,
    per_page: Annotated[int, Query(ge=1, le=MAX_PER_PAGE, description="Page size")] = DEFAULT_PER_PAGE,
    discord_id: Annotated[int | None, Query(description="Lead with what this user follows")] = None,
) -> dict:
    """The news stream, newest-first, filtered by any combination of the query params.

    With ``discord_id``, the stories matching that user's subscriptions come **first** and the
    general latest follows, deduplicated -- one list, so a ticker renders it without knowing
    whether anyone is signed in. ``personalised`` counts the leading items. Without it the list
    is plain latest and ``personalised`` is ``0``; never a 401, so the signed-out page is the
    same page.

    ``subject_type`` and ``subject_value`` are a pair: either both or neither. A lone one is
    ignored rather than rejected, because a page that clears its filter by blanking one input
    should show everything, not a 400.
    """
    subject = (subject_type, subject_value) if subject_type and subject_value else None
    if subject is not None and media is not None:
        _check_subject_type(media, subject_type)  # type: ignore[arg-type]

    items, total = await _page(bot, media, subject, source, since, search, page, per_page)
    followed = await _followed(bot, discord_id, MAX_FOLLOWED) if discord_id is not None else ()
    return browse_payload(
        items,
        total=total,
        page=page,
        per_page=per_page,
        filters={
            'media': media,
            'subject_type': subject_type,
            'subject_value': subject_value,
            'source': source,
            'since': since.isoformat() if since is not None else None,
            'search': search,
        },
        followed=followed,
    )


@router.get("/related")
async def related_news(
    bot: BotDep,
    media: Annotated[Media, Query(description="comic or title")],
    subject_type: Annotated[SubjectType, Query(description="The subject key's type")],
    subject_value: Annotated[str, Query(description="The subject key, as the picker gave it")],
    limit: Annotated[int, Query(ge=1, le=MAX_RELATED, description="How many stories")] = DEFAULT_RELATED,
) -> dict:
    """The detail pages' related-news strip: the most recent stories tagged with one subject.

    An empty ``items`` list is the honest answer for a subject nothing has been written about --
    §6.2 asks the section to be *absent* rather than empty on the page, which is the renderer's
    decision to make from this, not a 404 to make here.
    """
    _check_subject_type(media, subject_type)
    return {'items': await _related(bot, media, subject_type, subject_value, limit)}


@router.get("/sources")
async def list_sources(
    bot: BotDep,
    enabled_only: Annotated[bool, Query(description="Hide sources an operator switched off")] = False,
) -> dict:
    """Every configured feed, with when it was last polled and what it answered.

    Deliberately **not** cached: ``last_status`` is the one column whose whole job is telling an
    operator that a feed has been failing, and a five-minute-old answer to "is it broken now?"
    is the wrong answer.
    """
    rows = await bot.db.news.list_sources(enabled_only=enabled_only)
    return {'sources': [source_payload(row) for row in rows]}
