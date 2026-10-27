"""Pure warn-policy rules (no ``discord`` imports): active-warn counting and threshold selection."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ('THRESHOLD_ACTIONS', 'WarnThreshold', 'active_warn_count', 'select_threshold', 'warn_expiry')

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
