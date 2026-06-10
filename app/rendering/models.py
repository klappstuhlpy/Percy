"""Plain view-models that carry the *prepared data* for a render.

These dataclasses are the boundary between domain/Discord objects and the pure
drawing functions in :mod:`app.rendering.templates`. The :class:`RenderingService`
extracts primitive values out of records/members into one of these, then hands it
to a template function. Templates never see a domain model, a database record or a
``discord`` object — only the data below — which keeps them pure and unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.rendering.primitives import Font

__all__ = (
    "BarChartData",
    "ColorSwatchData",
    "LevelCardData",
    "PresenceData",
    "QuoteData",
)


@dataclass(slots=True)
class LevelCardData:
    """Everything needed to draw a rank/level card, fully resolved."""

    avatar: bytes
    name: str
    total_xp: int
    rank: int
    member_count: int
    level: int
    xp: int
    max_xp: int
    messages: int
    font: Font = Font.RUBIK


@dataclass(slots=True)
class QuoteData:
    """Everything needed to draw a quote image."""

    avatar: bytes
    text: str
    author_name: str
    font: Font = Font.GINTO_BOLD


@dataclass(slots=True)
class ColorSwatchData:
    """A solid colour swatch with optional centred text."""

    rgb: tuple[int, int, int]
    text: str | None = None


@dataclass(slots=True)
class BarChartData:
    """A single horizontal bar chart."""

    data: dict[str, int | float]
    title: str


@dataclass(slots=True)
class PresenceData:
    """A presence/activity donut chart."""

    labels: list[str] = field(default_factory=list)
    values: list[int] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)
    title: str = 'Presence'
