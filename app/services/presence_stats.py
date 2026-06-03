"""Presence-history summarization.

Extracted from the ``stats`` cog's ``presence`` command: turning a user's status
transitions into the total time spent in each status is pure logic over the recorded
timestamps, so it lives here free of Discord and is unit-testable. The cog keeps the
DB fetch, the chart rendering and the embed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

__all__ = (
    "PRESENCE_STATUSES",
    "PresenceBreakdown",
    "summarize_presence",
)

# The status buckets the chart reports, in the order the records store them.
PRESENCE_STATUSES = ("Online", "Idle", "Do Not Disturb", "Offline")


@dataclass(slots=True)
class PresenceBreakdown:
    """Seconds spent in each status, plus the earliest timestamp observed."""

    durations: dict[str, float]
    earliest: datetime

    @property
    def has_data(self) -> bool:
        """Whether any status accrued time (else there is nothing to chart)."""
        return any(self.durations.values())


def summarize_presence(changes: Iterable[tuple[datetime, str]]) -> PresenceBreakdown:
    """Total the time spent in each status from a user's presence history.

    ``changes`` is ``(changed_at, status_before)`` pairs ordered newest-first (as the
    ``changed_at DESC`` query returns them). Each consecutive pair contributes the gap
    between the two timestamps to the *earlier* record's ``status_before`` bucket --
    i.e. the status the user held during that interval. Duplicate timestamps collapse,
    with the last occurrence winning, mirroring the original dict-keyed-by-timestamp.

    The caller must pass at least one record (the cog returns early on empty history).
    """
    by_time: dict[datetime, str] = dict(changes)

    durations = dict.fromkeys(PRESENCE_STATUSES, 0.0)
    timestamps = list(by_time.items())
    for i in range(1, len(timestamps)):
        newer_at = timestamps[i - 1][0]
        older_at, status_before = timestamps[i]
        durations[status_before] += (newer_at - older_at).total_seconds()

    return PresenceBreakdown(durations=durations, earliest=min(by_time))
