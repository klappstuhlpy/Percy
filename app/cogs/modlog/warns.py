"""Pure warn-policy rules (no ``discord`` imports): active-warn counting and threshold selection."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = (
    'MAX_EXPIRY_DAYS',
    'THRESHOLD_ACTIONS',
    'WarnThreshold',
    'active_warn_count',
    'select_threshold',
    'threshold_error',
    'warn_expiry',
)

#: Upper bound for ``warnpolicy expiry`` (10 years).
MAX_EXPIRY_DAYS = 3650

#: Actions a threshold may escalate to; each is a key ``apply_member_action`` understands.
THRESHOLD_ACTIONS = ('timeout', 'mute', 'kick', 'ban')


@dataclass(frozen=True, slots=True)
class WarnThreshold:
    """At ``count`` active warns, apply ``action`` (for ``duration`` seconds where it takes one)."""

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


def select_threshold(thresholds: Iterable[WarnThreshold], active: int) -> WarnThreshold | None:
    """The rule that fires for exactly ``active`` warns.

    Exact match, not "highest at or below": the count rises by one per warn, so each rule fires
    once as it is reached and a later warn never re-applies an earlier rule.
    """
    return next((t for t in thresholds if t.count == active), None)


def threshold_error(count: int, action: str, seconds: int | None) -> str | None:
    """Why ``(count, action, seconds)`` is not a valid escalation rule, or ``None`` if it is.

    Shared by the ``warnpolicy set`` command and the internal API so both enforce one rule set.
    """
    from app.cogs.moderation.member_actions import (
        MemberActionError,
        validate_mute_duration,
        validate_timeout_duration,
    )

    if count < 1 or action not in THRESHOLD_ACTIONS:
        return f'Count must be 1 or more and action one of: {", ".join(THRESHOLD_ACTIONS)}.'
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
