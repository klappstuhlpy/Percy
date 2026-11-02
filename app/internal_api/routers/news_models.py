"""Response models for ``news.py`` -- the source of truth for percy-client's generated news types.

Class names are the Rust type names (see dashboard/crates/percy-client/GENERATION.md). Timestamps
are ISO text (``str``) because Percy's are naive UTC.
"""
from __future__ import annotations

from pydantic import BaseModel


class NewsSubject(BaseModel):
    """One tag on a story, in the key shape a subscription is expressed in."""

    media: str
    subject_type: str
    subject_value: str


class NewsItemSource(BaseModel):
    """Where a story came from; carried on the item so attribution is structural."""

    slug: str
    name: str
    homepage: str | None


class NewsItem(BaseModel):
    """One story. ``image_url``/``author`` are stored, not necessarily rendered (plan §9)."""

    id: int
    url: str
    headline: str
    excerpt: str | None
    image_url: str | None
    author: str | None
    published_at: str | None
    fetched_at: str | None
    source: NewsItemSource
    subjects: list[NewsSubject]


class NewsFilters(BaseModel):
    """Echoes the filters ``GET /news`` applied."""

    media: str | None
    subject_type: str | None
    subject_value: str | None
    source: str | None
    since: str | None
    search: str | None


class NewsPage(BaseModel):
    """``GET /news``: one page of the stream."""

    items: list[NewsItem]
    personalised: int
    page: int
    per_page: int
    total: int
    total_pages: int
    filters: NewsFilters


class NewsRelated(BaseModel):
    """``GET /news/related``."""

    items: list[NewsItem]


class NewsSource(BaseModel):
    """One configured feed, for the operator view."""

    slug: str
    name: str
    homepage: str | None
    feed_url: str
    media: str
    enabled: bool
    last_fetched: str | None
    last_status: str | None


class NewsSourcesResponse(BaseModel):
    """``GET /news/sources``."""

    sources: list[NewsSource]
