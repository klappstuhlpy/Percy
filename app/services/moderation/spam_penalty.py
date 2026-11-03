"""Pure escalation logic for the global anti-spam blacklist.

Extracted from :class:`app.core.spam.SpamControl` so the penalty curve can be
reasoned about and unit-tested without a bot instance. The cog records offense
timestamps and delegates the duration decision here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = (
    'BURST_WINDOW',
    'LOOKBACK_WINDOW',
    'ONE_DAY',
    'ONE_WEEK',
    'compute_spam_penalty',
)

ONE_DAY = 24 * 60 * 60
ONE_WEEK = 7 * ONE_DAY

#: Offenses older than this (seconds) no longer count toward escalation.
LOOKBACK_WINDOW = ONE_DAY
#: Offenses within this tighter window (seconds) count as a recidivist "burst".
BURST_WINDOW = 60 * 60

#: Total offenses within :data:`LOOKBACK_WINDOW` that trigger a permanent block.
PERMANENT_FREQUENCY = 16
#: Offenses within :data:`BURST_WINDOW` that trigger a permanent block.
PERMANENT_BURST = 10


def compute_spam_penalty(timestamps: Sequence[float], *, now: float) -> int | None:
    """Compute the blacklist duration (in seconds) for a repeat spammer.

    The penalty escalates on both *frequency* — how many times the user has tripped
    the spam filter within :data:`LOOKBACK_WINDOW` — and *recency* — how many of those
    offenses fall inside the tighter :data:`BURST_WINDOW`. Recent offenses are weighted
    more heavily, so a tight burst of repeat offenses escalates faster than the same
    number of offenses spread out over a day.

    Parameters
    ----------
    timestamps:
        POSIX timestamps of the user's recent spam offenses (order does not matter).
    now:
        The current POSIX timestamp.

    Returns
    -------
    int | None
        The penalty duration in seconds, or ``None`` for a permanent block.
    """
    recent = [t for t in timestamps if 0 <= now - t <= LOOKBACK_WINDOW]
    frequency = len(recent)
    burst = sum(1 for t in recent if now - t <= BURST_WINDOW)

    if frequency >= PERMANENT_FREQUENCY or burst >= PERMANENT_BURST:
        return None

    # Recency counts double: a clustered burst escalates faster than spread-out offenses.
    pressure = frequency + burst
    if pressure <= 3:
        return ONE_DAY
    if pressure <= 6:
        return 3 * ONE_DAY
    return ONE_WEEK
