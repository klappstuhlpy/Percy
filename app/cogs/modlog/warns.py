"""Pure warn-policy rules (no ``discord`` imports): active-warn counting and threshold selection."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = (
    'MAX_EXPIRY_DAYS',
    'THRESHOLD_ACTIONS',
    'WarnThreshold',
    'active_warn_count',
    'active_warn_points',
    'resolve_warn_reason',
    'select_threshold',
    'threshold_error',
    'warn_expiry',
    'warn_reason_name_error',
)

#: Upper bound for ``warnpolicy expiry`` (10 years).
MAX_EXPIRY_DAYS = 3650

#: Actions a threshold may escalate to; each is a key ``apply_member_action`` understands.
THRESHOLD_ACTIONS = ('timeout', 'mute', 'kick', 'ban')


@dataclass(frozen=True, slots=True)
class WarnThreshold:
    """At ``count`` active warn points, apply ``action`` (for ``duration`` seconds where it takes one)."""

    count: int
    action: str
    duration: int | None = None


def warn_expiry(created: datetime.datetime, days: int | None) -> datetime.datetime | None:
    """When a warn issued at ``created`` stops counting, or ``None`` if the guild never expires warns."""
    return created + datetime.timedelta(days=days) if days else None


def active_warn_count(
    expiries: Iterable[datetime.datetime | None], now: datetime.datetime | None = None
) -> int:
    """Counts warns still in force. ``expiries`` are naive-UTC ``expires_at`` values of un-pardoned warns."""
    now = (now or datetime.datetime.now(datetime.UTC)).replace(tzinfo=None)
    return sum(1 for expires in expiries if expires is None or expires.replace(tzinfo=None) > now)


def active_warn_points(
    rows: Iterable[tuple[datetime.datetime | None, int]], now: datetime.datetime | None = None
) -> int:
    """Total points of warns still in force; ``rows`` are ``(expires_at, points)`` of un-pardoned warns."""
    now = (now or datetime.datetime.now(datetime.UTC)).replace(tzinfo=None)
    return sum(points for expires, points in rows if expires is None or expires.replace(tzinfo=None) > now)


def select_threshold(
    thresholds: Iterable[WarnThreshold], active: int, previous: int | None = None
) -> WarnThreshold | None:
    """The rule that fires when a member's active points become ``active``.

    Without ``previous``: exact match on ``active``. With it (the total before this warn): the
    highest rule *crossed* by the warn, i.e. ``previous < count <= active``. A heavy warn can jump
    past a rule, so exact match would silently skip it, while a later warn still never re-applies
    a rule that was already reached.
    """
    if previous is None:
        return next((t for t in thresholds if t.count == active), None)
    return max((t for t in thresholds if previous < t.count <= active), key=lambda t: t.count, default=None)


def resolve_warn_reason(
    reason: str | None, presets: Mapping[str, tuple[int, str | None]]
) -> tuple[str | None, int]:
    """Applies a guild's reason preset: ``(final_reason, points)``.

    If the first word matches a preset name (case-insensitive; ``presets`` keys are lower-cased)
    the warn carries its points and the preset description becomes the reason, with any extra
    text appended. Otherwise the reason is untouched and worth 1 point.
    """
    head, _, rest = (reason or '').strip().partition(' ')
    preset = presets.get(head.lower()) if head else None
    if preset is None:
        return reason, 1
    points, description = preset
    return ' - '.join(part for part in (description or head, rest.strip()) if part), points


def warn_reason_name_error(name: str) -> str | None:
    """Why ``name`` is not a valid reason preset name (one word, at most 32 characters), or ``None``."""
    if not name or len(name.split()) != 1 or len(name) > 32:
        return 'name must be one word of at most 32 characters'
    return None


def threshold_error(count: int, action: str, seconds: int | None) -> str | None:
    """Why ``(count, action, seconds)`` is not a valid escalation rule, or ``None`` if it is.

    Shared by the ``warnpolicy set`` command and the internal API so both enforce one rule set.
    """
    from app.cogs.moderation.member_actions import (
        MemberActionError,
        validate_mute_duration,
        validate_timeout_duration,
    )

    # 1000 keeps the count inside the int column; no real policy escalates that far.
    if not 1 <= count <= 1000 or action not in THRESHOLD_ACTIONS:
        return f'Count must be 1 to 1000 and action one of: {", ".join(THRESHOLD_ACTIONS)}.'
    try:
        if action == 'timeout':
            validate_timeout_duration(seconds)
        elif action == 'mute':
            validate_mute_duration(seconds)
        elif seconds is not None:
            return f'`{action}` takes no duration.'
    except MemberActionError as e:
        return e.detail
    return None
