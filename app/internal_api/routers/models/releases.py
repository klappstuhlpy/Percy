"""Response models for ``releases.py`` -- the source of truth for percy-client's generated release types.

Class names are the Rust type names (see dashboard/crates/percy-client/GENERATION.md). Open sets
(``media``, ``status``, ``streams``...) are ``str`` so a new server value cannot break the client.
"""
from __future__ import annotations

from pydantic import BaseModel


class Subscription(BaseModel):
    """One thing a user follows; ``subject_value`` is an opaque key, ``guild_id`` a string snowflake."""

    media: str
    subject_type: str
    subject_value: str
    subject_label: str | None
    streams: str
    delivery: str
    guild_id: str | None
    created_at: str | None


class SubscriptionsResponse(BaseModel):
    """``GET /releases/users/{id}/subscriptions``."""

    subscriptions: list[Subscription]


class SubscriptionDeleted(BaseModel):
    """``DELETE /releases/users/{id}/subscriptions``: false when there was nothing to remove."""

    deleted: bool


class Subject(BaseModel):
    """One row of the subject picker."""

    media: str
    subject_type: str
    subject_value: str
    label: str
    hint: str


class SubjectsResponse(BaseModel):
    """``GET /releases/subjects``."""

    subjects: list[Subject]


class OverviewWindow(BaseModel):
    """The ISO date span the overview covers."""

    start: str
    end: str


class CardSubject(BaseModel):
    """One subject chip on an overview card."""

    subject_type: str
    subject_value: str
    label: str


class ReleaseCard(BaseModel):
    """One release on the overview, in the shape both media share."""

    media: str
    key: str
    name: str
    subtitle: str | None
    release_date: str | None
    cover_url: str | None
    url: str | None
    subjects: list[CardSubject]


class ProgressMark(BaseModel):
    """One item's mark: ``read``/``skipped`` for a comic, ``watched``/``skipped`` for a title."""

    status: str
    updated_at: str | None


class ReleasesOverview(BaseModel):
    """``GET /releases/overview``; ``progress`` is ``{media: {release_key: mark}}`` beside the cards."""

    window: OverviewWindow
    comics: list[ReleaseCard]
    titles: list[ReleaseCard]
    followed: list[ReleaseCard]
    progress: dict[str, dict[str, ProgressMark]]


class ProgressResponse(BaseModel):
    """``GET``/``PUT /releases/users/{id}/progress``; ``merged`` is 0 on a GET."""

    merged: int = 0
    entries: dict[str, dict[str, ProgressMark]]


class ReleaseProgressDeleted(BaseModel):
    """``DELETE /releases/users/{id}/progress``: how many rows were removed."""

    deleted: int
