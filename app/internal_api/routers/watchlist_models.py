"""Response models for ``watchlist.py`` -- the source of truth for percy-client's generated watch types.

Class names are the Rust type names (see dashboard/crates/percy-client/GENERATION.md). Dates are
ISO text (``str``); open sets (``kind``, ``offer``, ``role``) are ``str``.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class WatchUniverseSummary(BaseModel):
    """One universe with its aggregates, as listed by ``GET /watchlist/universes``."""

    slug: str
    name: str
    short_name: str
    tagline: str | None
    accent: str
    logo_url: str | None
    backdrop_url: str | None
    sort_order: int
    published: bool
    synced_at: str | None
    title_count: int
    total_runtime: int


class WatchUniversesResponse(BaseModel):
    """``GET /watchlist/universes``."""

    universes: list[WatchUniverseSummary]


class WatchUniverse(BaseModel):
    """A universe's header on a page payload."""

    slug: str
    name: str
    short_name: str
    tagline: str | None
    accent: str
    logo_url: str | None
    backdrop_url: str | None
    published: bool
    synced_at: str | None


class WatchPath(BaseModel):
    """A curated path through a universe."""

    slug: str
    name: str
    description: str | None
    is_default: bool
    sort_order: int


class WatchEra(BaseModel):
    """An era heading derived from the titles."""

    key: str
    order: int
    title_count: int


class WatchProvider(BaseModel):
    """A streaming provider offer for a title in a region."""

    provider_id: int
    provider_name: str
    logo_path: str | None
    offer: str
    link: str | None


class WatchTrailer(BaseModel):
    """The one trailer Percy selected for a title."""

    site: str
    key: str
    name: str | None
    url: str
    embed_url: str


class WatchTitle(BaseModel):
    """One title on a watch order."""

    id: int
    universe: str
    tmdb_type: str
    tmdb_id: int
    slug: str
    title: str
    kind: str
    runtime: int
    episodes: int | None
    release_date: str | None
    poster_path: str | None
    backdrop_path: str | None
    overview: str | None
    tmdb_rating: float | None
    tmdb_votes: int | None
    era: str | None
    era_order: int
    story_order: int
    release_order: int
    milestone: bool
    instruction: str | None
    context: str | None
    spoiler: str | None
    credits_of: int | None
    sub_universe: str | None
    importance: str | None
    providers: list[WatchProvider]
    trailer: WatchTrailer | None
    season_number: int | None
    season_name: str | None
    parent_id: int | None


class WatchUniversePage(BaseModel):
    """``GET /watchlist/universes/{slug}``: the whole watch-order page."""

    universe: WatchUniverse
    path: str | None
    region: str
    sort: str
    paths: list[WatchPath]
    eras: list[WatchEra]
    titles: list[WatchTitle]


class WatchStats(BaseModel):
    """``GET /watchlist/universes/{slug}/stats``."""

    universe: str
    path: str | None
    total: int
    movies: int
    shows: int
    runtime: int
    hours: float
    essential: int
    recommended: int
    optional: int


class WatchCredit(BaseModel):
    """A cast/crew credit on a title."""

    tmdb_person_id: int
    name: str
    slug: str
    profile_path: str | None
    role: str
    character_name: str | None
    billing_order: int | None


class WatchTitleDetail(BaseModel):
    """``GET /watchlist/titles/{universe}/{slug}``."""

    title: WatchTitle
    credits_of: WatchTitle | None
    parent: WatchTitle | None
    seasons: list[WatchTitle]
    credits: list[WatchCredit]
    previous: WatchTitle | None
    next: WatchTitle | None
    position: int
    total: int
    path: str | None
    sort: str
    universe: WatchUniverse


class WatchPerson(BaseModel):
    """A person on a person page."""

    tmdb_person_id: int
    name: str
    slug: str
    profile_path: str | None
    updated_at: str | None


class WatchPersonCredit(BaseModel):
    """One credit on a person page, nesting the full title."""

    role: str
    character_name: str | None
    billing_order: int | None
    universe_name: str
    universe_accent: str
    title: WatchTitle


class WatchPersonPage(BaseModel):
    """``GET /watchlist/people/{slug}``."""

    person: WatchPerson
    credits: list[WatchPersonCredit]


class WatchPersonSummary(BaseModel):
    """One autocomplete entry from ``GET /watchlist/people``."""

    tmdb_person_id: int
    name: str
    slug: str
    profile_path: str | None
    credit_count: int


class WatchPeopleResponse(BaseModel):
    """``GET /watchlist/people``."""

    people: list[WatchPersonSummary]


class WatchProgressEntry(BaseModel):
    """One title's V13 watch state."""

    status: str
    updated_at: str


class WatchProgressResponse(BaseModel):
    """``GET``/``PUT /watchlist/users/{id}/progress``: ``{title_id: entry}``."""

    entries: dict[str, WatchProgressEntry]


class WatchProgressDeleted(BaseModel):
    """``DELETE /watchlist/users/{id}/progress``."""

    deleted: int


class WatchPrefs(BaseModel):
    """A user's watchlist preferences (defaults when none saved)."""

    region: str
    path_slug: str | None
    sort_mode: str
    show_spoiler: bool
    with_credits: bool
    hide_sub: bool
    weekly_hours: dict[str, Any] | None
    target_date: str | None
