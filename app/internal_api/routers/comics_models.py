"""Response models for ``comics.py`` -- the source of truth for percy-client's generated comic types.

Class names are the Rust type names (see dashboard/crates/percy-client/GENERATION.md). Dates are
ISO text (``str``).
"""
from __future__ import annotations

from pydantic import BaseModel


class ComicCredit(BaseModel):
    """One credited contributor on a release."""

    name: str
    role: str


class ComicCharacter(BaseModel):
    """One character appearing in a release."""

    name: str
    kind: str | None


class ComicRelease(BaseModel):
    """One comic release; browse/series listings carry empty ``credits``/``characters``."""

    id: int
    brand: str
    slug: str
    title: str
    series_slug: str | None
    series_name: str | None
    issue_number: str | None
    release_date: str | None
    cover_url: str | None
    description: str | None
    price: float | None
    page_count: int | None
    format: str | None
    publisher: str | None
    url: str | None
    first_seen: str | None
    updated_at: str | None
    credits: list[ComicCredit]
    characters: list[ComicCharacter]


class ComicBrowseFilters(BaseModel):
    """Echoes the filters ``GET /comics`` applied."""

    brand: str | None
    series: str | None
    creator: str | None
    character: str | None
    since: str | None
    until: str | None
    search: str | None


class ComicBrowsePage(BaseModel):
    """``GET /comics``: one page of filtered releases."""

    items: list[ComicRelease]
    page: int
    per_page: int
    total: int
    total_pages: int
    filters: ComicBrowseFilters


class ComicDetailSeries(BaseModel):
    """Reading-order neighbours nested under ``series`` in a detail response."""

    previous: ComicRelease | None
    next: ComicRelease | None
    more_from_series: list[ComicRelease]


class ComicDetail(ComicRelease):
    """``GET /comics/{brand}/{slug}``: the release's own fields plus its series neighbours."""

    series: ComicDetailSeries


class ComicSeriesSummary(BaseModel):
    """One series card in the ``GET /comics/series`` index."""

    series_slug: str
    series_name: str
    brand: str
    issue_count: int
    latest: str | None


class ComicSeriesList(BaseModel):
    """``GET /comics/series``."""

    series: list[ComicSeriesSummary]


class ComicSeriesPage(BaseModel):
    """``GET /comics/series/{slug}``: one series and every issue in it."""

    series_slug: str
    series_name: str
    brand: str
    publisher: str | None
    issue_count: int
    issues: list[ComicRelease]


class ComicNameEntry(BaseModel):
    """One creator/character name with its appearance count."""

    name: str
    appearances: int


class ComicNamesResponse(BaseModel):
    """``GET /comics/names``."""

    kind: str
    names: list[ComicNameEntry]
