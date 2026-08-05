"""Release endpoints: the overview, what a Discord user follows, and the picker that finds it.

Not guild-scoped -- a subscription belongs to a user and the DM it produces is theirs, the same
way watch progress is per user rather than per server. ``guild_id`` is recorded only as the
place a subscription was created from, never as a scope.

A subscription is the tuple ``(user_id, media, subject_type, subject_value)`` (see
``migrations/V42__subscriptions.sql``). ``subject_value`` is a *string key*, not a foreign key,
and it only works because both sides normalise it exactly once: the repository on every write
and lookup, :func:`app.services.releases.match` on every comparison. So subject values travel
through these handlers *raw* -- normalising here as well would only casefold the display label
the repository derives from an unlabelled subscribe. The one place this router normalises is
the picker's own ``subject_value``, which a client compares against stored rows.

Note what ``normalise`` is not: it casefolds and collapses whitespace, it does not slugify. A
client must echo back the ``subject_value`` the picker gave it, not the ``label`` -- ``series``
and ``universe`` keys are slugs and ``person`` keys are person slugs, matching what
:func:`app.services.releases.comic_releasable` / ``title_releasable`` derive at fan-out time.

Caching: ``GET /releases/subjects`` reads the catalogue (comic series, creator/character names,
watchlist universes), which only changes when an ingest run lands -- a separate process on its
own schedule with no seam to invalidate from, exactly as in ``routers/comics.py`` and
``routers/watchlist.py``. So the catalogue half of the picker sits behind the same short TTL and
the TTL, not an invalidation call, is what bounds staleness. Only the catalogue *fetch* is
cached, not the search: ranking happens per request, so a per-keystroke ``q`` never becomes a
cache key (which would fill the shared cache with one entry per prefix anybody ever typed --
the reason ``watchlist.search_people`` is uncached, and why person matches are added here after
the cache, per query).

Subscriptions and progress are per-user mutable state and are **never** cached: a user toggling
a follow or marking an issue read must see it on the very next read, on any surface, and there
is no getter to invalidate. ``GET /releases/overview`` splits along exactly that line: its
catalogue halves are cached, its personalised halves are recomputed per request -- and progress
is returned as a map beside the cards rather than as a field on them precisely so the cached
half never has to hold a value that belongs to one reader.

Progress uses ``release_progress`` (V42), keyed by the ``(media, release_key)`` identity the
delivery claim already uses. V39's ``watch_progress`` is untouched and still backs the universe
order pages via ``routers/watchlist.py`` -- one table replacing the other is a later migration,
not a side effect of this one (the plan's §4.4).
"""
from __future__ import annotations

import datetime
import itertools
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.services.releases import (
    BRAND_LABELS,
    COMIC_SUBJECT_TYPES,
    TITLE_SUBJECT_TYPES,
    Entry,
    build_overview,
    check_status,
    comic_entry,
    group_rows,
    normalise,
    progress_payload,
    title_entry,
)
from app.utils import cache, fuzzy

from ..dependencies import BotDep, verify_token
from ..helpers import naive_utc

if TYPE_CHECKING:
    import asyncpg

    from app.core.bot import Bot

router = APIRouter(prefix="/releases", tags=["Releases"], dependencies=[Depends(verify_token)])

#: How long the subject catalogue is served from memory. Ingest is a separate process, so a few
#: minutes of staleness is cheap next to rebuilding the picker's candidate set per keystroke.
CATALOGUE_TTL = 300

#: How many rows each catalogue source contributes to the candidate pool (the repositories'
#: own default page size). The pool is ranked in-process, so this bounds the work per request.
POOL_SIZE = 200

#: Minimum fuzzy score a candidate needs to appear in a search. Below this the picker is just
#: showing the catalogue in a random-looking order, which is worse than showing nothing.
SCORE_CUTOFF = 60

#: Default and ceiling for the overview's ``days``. A month is as far as the film/TV half is
#: kept honest (the cog's nightly TMDB re-check has the same horizon), so offering a wider
#: window would be offering dates nothing re-verifies.
DEFAULT_DAYS = 7
MAX_DAYS = 30

#: How many rows each catalogue half fetches before ``limit`` slices it. The cached entries are
#: built once per TTL for the widest ``limit`` any request can ask for, so ``limit`` stays a
#: display concern and never becomes part of the cache key.
OVERVIEW_FETCH_LIMIT = 200

Media = Literal['comic', 'title']
SubjectType = Literal['brand', 'series', 'creator', 'character', 'universe', 'franchise', 'person']
Streams = Literal['releases', 'news', 'both']
Delivery = Literal['dm', 'off']
Status = Literal['read', 'watched', 'skipped']

#: Which subject types each medium accepts, taken from the service that *derives* them rather
#: than restated here -- the two must never drift, or this API would accept a subscription the
#: fan-out can never match. ``character`` is deliberately in both.
SUBJECT_TYPES: dict[str, tuple[str, ...]] = {'comic': COMIC_SUBJECT_TYPES, 'title': TITLE_SUBJECT_TYPES}


class SubscribeBody(BaseModel):
    """A follow, as the picker or a Discord command submits it."""

    media: Media
    subject_type: SubjectType
    subject_value: str
    subject_label: str | None = None
    streams: Streams = 'releases'
    delivery: Delivery = 'dm'
    guild_id: int | None = None


class SubscriptionPatchBody(BaseModel):
    """The identifying triple plus whichever of the two mutable fields is changing."""

    media: Media
    subject_type: SubjectType
    subject_value: str
    streams: Streams | None = None
    delivery: Delivery | None = None


class ProgressEntry(BaseModel):
    """One item's read/watched/skipped state, as the client last saw it.

    ``release_key`` is the identity the delivery claim and the overview cards already use --
    ``{brand}/{slug}`` for a comic, ``{universe}/{slug}`` for a title -- so a client marks the
    card it is looking at by echoing that card's ``key``, never by composing its own.
    """

    media: Media
    release_key: str
    status: Status
    updated_at: datetime.datetime | None = None


class ProgressBody(BaseModel):
    """A bulk progress merge: the client's whole local set, or a single debounced toggle."""

    entries: list[ProgressEntry] = Field(default_factory=list)


def _check_subject_type(media: str, subject_type: str) -> str:
    """Validates a ``subject_type`` against its medium.

    The two closed sets overlap but are not the same, and pydantic's ``Literal`` can only check
    the union -- ``media='comic', subject_type='person'`` passes there and would create a
    subscription nothing can ever match, so the pairing is checked here.
    """
    allowed = SUBJECT_TYPES[media]
    if subject_type not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"subject_type {subject_type!r} is not valid for media {media!r}; expected one of {list(allowed)}",
        )
    return subject_type


def _subscription(row: asyncpg.Record | dict[str, Any]) -> dict[str, Any]:
    """Shapes a ``release_subscriptions`` row.

    ``guild_id`` goes out as a string: it is a snowflake, and the browser clients on the other
    end of this API lose precision on integers past 2^53.
    """
    return {
        'media': row['media'],
        'subject_type': row['subject_type'],
        'subject_value': row['subject_value'],
        'subject_label': row['subject_label'],
        'streams': row['streams'],
        'delivery': row['delivery'],
        'guild_id': str(row['guild_id']) if row['guild_id'] is not None else None,
        'created_at': row['created_at'].isoformat() if row['created_at'] else None,
    }


# -- Subscriptions (per-user, never cached) -----------------------------------


@router.get("/users/{discord_id}/subscriptions")
async def list_subscriptions(
    bot: BotDep,
    discord_id: int,
    media: Annotated[Media | None, Query(description="Scope to one medium")] = None,
) -> dict:
    """Everything a user follows. A user with no subscriptions is an empty list, not a 404."""
    rows = await bot.db.releases.list_subscriptions(discord_id, media=media)
    return {'subscriptions': [_subscription(row) for row in rows]}


@router.put("/users/{discord_id}/subscriptions")
async def put_subscription(bot: BotDep, discord_id: int, body: Annotated[SubscribeBody, Body()]) -> dict:
    """Follows a subject, or refreshes the settings of a follow that already exists.

    Idempotent by design: the picker cannot know whether the user already follows a subject
    without a round trip, so re-subscribing is an upsert rather than a conflict.
    """
    _check_subject_type(body.media, body.subject_type)
    # The raw value goes down: the repository normalises every subject value it writes, and it
    # falls back to this string for a missing label -- normalising here first would casefold
    # the display name a picker-less caller (a Discord command) never sent one for.
    row = await bot.db.releases.subscribe(
        discord_id,
        body.media,
        body.subject_type,
        body.subject_value,
        label=body.subject_label,
        streams=body.streams,
        delivery=body.delivery,
        guild_id=body.guild_id,
    )
    return _subscription(row)


@router.patch("/users/{discord_id}/subscriptions")
async def patch_subscription(
    bot: BotDep, discord_id: int, body: Annotated[SubscriptionPatchBody, Body()],
) -> dict:
    """Changes the stream or delivery mode of an existing follow.

    A 404 here means "you do not follow that" -- distinct from a PUT, which would happily
    create it. The dashboard needs the difference to keep a stale toggle from resurrecting a
    subscription the user removed in another tab. The repository's ``UPDATE ... RETURNING``
    answers both questions at once, so the miss costs no extra query: no row back, no
    subscription.
    """
    if body.streams is None and body.delivery is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="expected at least one of 'streams' or 'delivery'",
        )
    _check_subject_type(body.media, body.subject_type)

    row = None
    if body.streams is not None:
        row = await bot.db.releases.set_streams(
            discord_id, body.media, body.subject_type, body.subject_value, body.streams)
    if body.delivery is not None:
        row = await bot.db.releases.set_delivery(
            discord_id, body.media, body.subject_type, body.subject_value, body.delivery)

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no {body.media} subscription to {body.subject_type} {normalise(body.subject_value)!r}",
        )
    return _subscription(row)


@router.delete("/users/{discord_id}/subscriptions")
async def delete_subscription(
    bot: BotDep,
    discord_id: int,
    media: Annotated[Media, Query(description="comic or title")],
    subject_type: Annotated[SubjectType, Query(description="The subject key's type")],
    subject_value: Annotated[str, Query(description="The subject key")],
) -> dict:
    """Unfollows a subject. ``deleted`` is false when there was nothing to remove.

    The triple travels as query parameters rather than a body: a DELETE body is awkward for
    both the Rust client and every HTTP cache between here and it.
    """
    _check_subject_type(media, subject_type)
    return {'deleted': await bot.db.releases.unsubscribe(discord_id, media, subject_type, subject_value)}


# -- Progress (per-user, never cached) ----------------------------------------


def _check_status(media: str, progress_status: str) -> str:
    """Validates a status against its medium, as a 400 rather than a ``ValueError``.

    The pairing is **rejected, not repaired**. ``read`` and ``watched`` are the same gesture in
    two media, so translating one into the other looks harmless -- but ``media`` is also what
    namespaces ``release_key``, and a client that sent the wrong verb has almost certainly sent
    the wrong key too. Normalising the status would file that row against a real item in the
    other catalogue and the mistake would only surface later, as progress on something the user
    never opened. The pure rule lives in :func:`app.services.releases.check_status`, next to the
    sets it enforces; this only turns it into an HTTP answer, exactly as
    :func:`_check_subject_type` does for the subscription pairing.
    """
    try:
        return check_status(media, progress_status)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error


@router.get("/users/{discord_id}/progress")
async def list_progress(
    bot: BotDep,
    discord_id: int,
    media: Annotated[Media | None, Query(description="Scope to one medium")] = None,
) -> dict:
    """A user's progress as ``{media: {release_key: {status, updated_at}}}``.

    Both media are always present (empty when the user has marked nothing in one), so a client
    can index by a card's ``media`` without a guard. A user who has marked nothing at all is two
    empty maps, not a 404.
    """
    rows = await bot.db.releases.get_progress(discord_id, media=media)
    return {'entries': progress_payload(rows)}


@router.put("/users/{discord_id}/progress")
async def put_progress(bot: BotDep, discord_id: int, body: Annotated[ProgressBody, Body()]) -> dict:
    """Merges a batch of progress entries, last-write-wins, and returns the merged set.

    The same contract as ``PUT /watchlist/users/{id}/progress``: the client sends its own
    ``updated_at`` per entry and the server keeps whichever side is newer, so a stale tab
    replaying its localStorage cannot undo a newer change made on another device. ``merged``
    counts the rows that actually stuck -- fewer than were sent means the server was already
    ahead, which is a successful merge and not an error.

    The returned set is the user's *whole* progress, not just the medium touched, because the
    client's next render needs both halves of the page anyway.
    """
    rows = [
        (entry.media, entry.release_key, _check_status(entry.media, entry.status), naive_utc(entry.updated_at))
        for entry in body.entries
    ]
    merged = await bot.db.releases.set_progress_bulk(discord_id, rows)
    return {'merged': merged, **await list_progress(bot, discord_id, None)}


@router.delete("/users/{discord_id}/progress")
async def delete_progress(
    bot: BotDep,
    discord_id: int,
    media: Annotated[Media | None, Query(description="Scope the reset to one medium")] = None,
    release_key: Annotated[str | None, Query(description="Unmark one item; needs ``media``")] = None,
) -> dict:
    """Unmarks one item, or resets a medium (or everything). ``deleted`` counts what was removed.

    The key travels as a query parameter rather than a body for the same reason the
    unsubscribe triple does. ``release_key`` without ``media`` is a 400 and not a wildcard: the
    key is only unique *within* a medium, so honouring it unscoped could delete a comic's row
    because a film happened to share its slug.
    """
    if release_key is not None:
        if media is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="'release_key' needs 'media'; a release key is only unique within one medium",
            )
        return {'deleted': int(await bot.db.releases.clear_progress(discord_id, media, release_key))}
    return {'deleted': await bot.db.releases.clear_all_progress(discord_id, media=media)}


# -- Subject picker (catalogue read, TTL-cached) ------------------------------


def _subject(media: str, subject_type: str, value: str, label: str, hint: str) -> dict[str, Any]:
    """One picker row: the key to subscribe by, plus the display form and a one-line hint."""
    return {
        'media': media,
        'subject_type': subject_type,
        'subject_value': normalise(value),
        'label': label,
        'hint': hint,
    }


@cache.cache(maxsize=CATALOGUE_TTL, strategy=cache.Strategy.TIMED)
async def _catalogue_subjects(bot: Bot, media: str | None) -> list[tuple[int, dict[str, Any]]]:
    """Every subject derivable from a catalogue table, each with a popularity weight.

    Cached for :data:`CATALOGUE_TTL` seconds and keyed only on the medium, so the candidate pool
    is built at most twice per TTL no matter how much anybody types. Each source arrives already
    ordered by the repository (series by recency, names by appearance count, universes by
    display order), which is the order the empty-query case shows them in.
    """
    subjects: list[tuple[int, dict[str, Any]]] = []

    if media != 'title':
        # The whole-publisher follow. It is a closed set of three rather than a query, and it
        # carries no weight: a brand outranking every series in the popular list would bury the
        # subjects a user actually came looking for.
        for brand_key, brand_label in BRAND_LABELS.items():
            subjects.append((0, _subject('comic', 'brand', brand_key, brand_label, 'every release')))

        for row in await bot.db.releases.list_series(limit=POOL_SIZE):
            label = row['series_name'] or row['series_slug']
            brand = row['brand'] or ''
            hint = f"{BRAND_LABELS.get(brand.lower(), brand.title())} · {row['issue_count']} issues"
            subjects.append((row['issue_count'], _subject('comic', 'series', row['series_slug'], label, hint)))

        for kind, table, noun in (
            ('creator', 'comic_creators', 'credits'),
            ('character', 'comic_characters', 'appearances'),
        ):
            for row in await bot.db.releases.list_names(table, limit=POOL_SIZE):
                hint = f"{kind} · {row['appearances']} {noun}"
                subjects.append((row['appearances'], _subject('comic', kind, row['name'], row['name'], hint)))

    if media != 'comic':
        for row in await bot.db.watchlist.list_universes():
            hint = f"universe · {row['title_count']} titles"
            subjects.append((row['title_count'], _subject('title', 'universe', row['slug'], row['name'], hint)))

    return subjects


def _rank(candidates: list[tuple[int, dict[str, Any]]], query: str, limit: int) -> list[dict[str, Any]]:
    """Ranks candidates against ``query`` with the project's own fuzzy scorer.

    :func:`app.utils.fuzzy.partial_ratio` is the substring-tolerant scorer, so "iron" scores 100
    against "Iron Man" -- which makes ties the common case, not the exception. Popularity breaks
    them, then the label, so the order is stable between identical requests.
    """
    needle = query.lower()
    scored = [
        (fuzzy.partial_ratio(needle, subject['label'].lower()), weight, subject)
        for weight, subject in candidates
    ]
    ranked = sorted(
        (row for row in scored if row[0] >= SCORE_CUTOFF),
        key=lambda row: (-row[0], -row[1], row[2]['label']),
    )
    return [subject for _, _, subject in ranked[:limit]]


def _popular(candidates: list[tuple[int, dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    """The empty-query case: the head of each source, interleaved.

    A blank picker is a dead end, so it opens on the catalogue's most popular subjects instead.
    Round-robin rather than concatenation, or the first source alone would fill every slot and
    a user would never learn the picker also knows about actors.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for _, subject in candidates:
        groups.setdefault((subject['media'], subject['subject_type']), []).append(subject)
    interleaved = itertools.chain.from_iterable(itertools.zip_longest(*groups.values()))
    return [subject for subject in interleaved if subject is not None][:limit]


@router.get("/subjects")
async def search_subjects(
    bot: BotDep,
    q: Annotated[str, Query(description="What the user typed; empty returns popular subjects")] = "",
    media: Annotated[Media | None, Query(description="Narrow to one medium")] = None,
    limit: Annotated[int, Query(ge=1, le=50, description="Maximum subjects to return")] = 25,
) -> dict:
    """The subject picker: everything a user could follow, ranked against ``q``.

    Spans both media unless ``media`` narrows it, because a user searching "loki" means the
    character, the show and the universe without knowing which of those the system calls it.
    """
    candidates = list(await _catalogue_subjects(bot, media))
    if not q:
        return {'subjects': _popular(candidates, limit)}

    if media != 'comic':
        # Person search is a trigram query against one GIN index, so it is per-query rather than
        # part of the cached pool -- the catalogue side cannot pre-materialise a name index.
        for row in await bot.db.watchlist.search_people(q, limit=limit):
            hint = f"person · {row['credit_count']} credits"
            candidates.append((row['credit_count'], _subject('title', 'person', row['slug'], row['name'], hint)))

    return {'subjects': _rank(candidates, q, limit)}


# -- Overview (catalogue TTL-cached, personalisation never) -------------------


@cache.cache(maxsize=CATALOGUE_TTL, strategy=cache.Strategy.TIMED)
async def _overview_entries(bot: Bot, days: int) -> tuple[datetime.date, datetime.date, list[Entry], list[Entry]]:
    """Both catalogue halves of the overview window, cached for :data:`CATALOGUE_TTL` seconds.

    Keyed on ``days`` alone -- the window is the only thing that changes what is fetched, so
    every caller of a given width shares one build no matter who is asking or how many cards
    they asked for. Nothing user-specific is read in here, and nothing user-specific may ever
    be: this is the half that is shared between every signed-out visitor and every signed-in
    one, and a subscription leaking into it would be served to the next stranger.

    Six queries per build, independent of catalogue size: comics + their credits + their
    characters, titles + their credits + the universe name lookup.

    ``today`` is resolved inside the cached call, so for up to one TTL after midnight the
    window is still yesterday's. That is the same staleness ``routers/watchlist.py`` and
    ``routers/comics.py`` already accept, and a five-minute-old "this week" is not a wrong
    answer -- it is a slightly late one.
    """
    today = datetime.datetime.now(datetime.UTC).date()
    start = today - datetime.timedelta(days=days)
    end = today + datetime.timedelta(days=days)

    repo = bot.db.releases
    rows = await repo.recent_comic_releases(
        datetime.datetime.combine(start, datetime.time.min), limit=OVERVIEW_FETCH_LIMIT)
    release_ids = [row['id'] for row in rows]
    creators = group_rows(await repo.credits_for_releases(release_ids), 'release_id')
    characters = group_rows(await repo.characters_for_releases(release_ids), 'release_id')
    comics = [
        comic_entry(row, creators.get(row['id'], ()), characters.get(row['id'], ()))
        for row in rows
    ]

    title_rows = await bot.db.watchlist.titles_releasing_between(start, end)
    credits = group_rows(await bot.db.watchlist.list_credits([row['id'] for row in title_rows]), 'title_id')
    universe_names = {row['slug']: row['name'] for row in await bot.db.watchlist.list_universes()}
    titles = [title_entry(row, credits.get(row['id'], ()), universe_names=universe_names) for row in title_rows]

    return start, end, comics, titles


@router.get("/overview")
async def releases_overview(
    bot: BotDep,
    days: Annotated[int, Query(ge=1, le=MAX_DAYS, description="Half-width of the window, in days")] = DEFAULT_DAYS,
    discord_id: Annotated[int | None, Query(description="Personalise for this user; omit for the public page")] = None,
    limit: Annotated[int, Query(ge=1, le=OVERVIEW_FETCH_LIMIT, description="Maximum cards per list")] = 24,
) -> dict:
    """The front door: what just landed and what is imminent, across both media.

    **The window is deliberately asymmetric**, because "new" means different things to the two
    catalogues:

    * **Comics** are backward-only, ``first_seen >= today - days``. ``first_seen`` (not
      ``release_date``) is the honest measure of new here -- it is why ``V40`` carries the
      column -- because it survives a restart and because the ingest learns about a book when
      it is *solicited*, often months early. A forward window on comics would fill the page
      with books nobody can buy yet, and would show them again on the week they actually ship.
    * **Film/TV** is symmetric, ``release_date`` within ``[today - days, today + days]``.
      ``watch_titles`` has no ``first_seen``, its dates are known long in advance, and half the
      value of a film/TV overview is what is *about to* land. The forward half is only
      trustworthy because the ``Releases`` cog re-checks the next 30 days against TMDB nightly.

    The returned ``window`` reports the union, ``[today - days, today + days]``: it bounds every
    card in the response, and comics simply never occupy its forward half.

    ``discord_id`` is optional and additive, and adds two things. ``followed`` is the subset of
    the cards already in ``comics``/``titles`` that this user's subscriptions match, computed
    with the same :func:`~app.services.releases.match` the DM dispatch uses. ``progress`` is
    what that user has already read or watched, keyed by ``(media, release_key)`` -- the same
    ``key`` every card carries. Without a ``discord_id`` the first is ``[]`` and the second is
    two empty maps: empty rather than null or a 401, so the signed-out page is the same page
    with two sections quiet.

    ``progress`` rides beside the cards rather than as a field on them, and that is a caching
    constraint rather than a style choice: the cards themselves come out of a shared TTL cache,
    so writing one reader's status into a card would hand it to the next visitor who hits the
    same entry. Two extra queries per personalised request, neither of them cached.

    Caching: the two catalogue halves come from a :data:`CATALOGUE_TTL`-second TTL cache keyed
    on the window (see :func:`_overview_entries`); both personalised halves are **never**
    cached. Subscriptions and progress are per-user mutable state with no getter to invalidate,
    and a user who has just followed or marked something must see it on the very next load --
    the same rule the handlers above follow.
    """
    start, end, comics, titles = await _overview_entries(bot, days)
    subscriptions = await bot.db.releases.list_subscriptions(discord_id) if discord_id is not None else ()
    progress = await bot.db.releases.get_progress(discord_id) if discord_id is not None else ()
    return build_overview(
        comics[:limit], titles[:limit], start=start, end=end,
        subscriptions=subscriptions, progress=progress)
