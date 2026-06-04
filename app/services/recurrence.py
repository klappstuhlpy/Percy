"""Pure scheduling math for recurring reminders.

The reminder cog parses the user's interval (via the existing ``RelativeDelta``
converter) and persists it in the timer metadata; this module owns the Discord-free
decisions: normalizing/validating the interval, computing the next fire time, and
tracking a bounded occurrence count. Kept unit-testable without a bot instance.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dateutil.relativedelta import relativedelta

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = (
    'MIN_INTERVAL',
    'RecurrenceResult',
    'advance_recurrence',
    'describe_interval',
    'interval_too_short',
    'next_occurrence',
    'normalize_interval',
)

#: The supported relativedelta fields, in descending magnitude (for display ordering).
INTERVAL_FIELDS = ('years', 'months', 'weeks', 'days', 'hours', 'minutes', 'seconds')

#: The smallest permitted recurrence interval — guards against reminder spam/abuse.
MIN_INTERVAL = datetime.timedelta(minutes=1)


def normalize_interval(delta: relativedelta) -> dict[str, int]:
    """Reduce a :class:`relativedelta` to a JSON-serializable mapping of supported fields.

    Raises
    ------
    ValueError
        If the interval is empty (non-advancing) or contains a negative component.
    """
    data = {field: int(getattr(delta, field) or 0) for field in INTERVAL_FIELDS}
    data = {key: value for key, value in data.items() if value}

    if not data:
        raise ValueError('recurrence interval must be non-zero')
    if any(value < 0 for value in data.values()):
        raise ValueError('recurrence interval must be positive')
    return data


def _to_relativedelta(data: Mapping[str, int]) -> relativedelta:
    return relativedelta(**{field: data[field] for field in INTERVAL_FIELDS if field in data})


def interval_too_short(data: Mapping[str, int], *, reference: datetime.datetime) -> bool:
    """Returns whether the interval is shorter than :data:`MIN_INTERVAL`.

    ``reference`` anchors the check, since month/year intervals are not a fixed length.
    """
    delta = _to_relativedelta(data)
    return (reference + delta) - reference < MIN_INTERVAL


def next_occurrence(
    last: datetime.datetime, data: Mapping[str, int], *, now: datetime.datetime
) -> datetime.datetime:
    """Advance ``last`` by the interval until it is strictly after ``now``.

    Skipping missed occurrences (rather than firing each one) avoids a burst of
    catch-up reminders after downtime. The interval is assumed validated as positive,
    so the loop always terminates.
    """
    delta = _to_relativedelta(data)
    upcoming = last + delta
    while upcoming <= now:
        upcoming += delta
    return upcoming


@dataclass(frozen=True, slots=True)
class RecurrenceResult:
    """The next scheduled run of a recurring reminder and its remaining count."""

    next_run: datetime.datetime
    remaining: int | None


def advance_recurrence(
    last: datetime.datetime,
    data: Mapping[str, int],
    *,
    now: datetime.datetime,
    remaining: int | None = None,
) -> RecurrenceResult | None:
    """Compute the next run after a reminder fires, or ``None`` if the series is over.

    ``remaining`` is the number of occurrences still owed *after* the one that just
    fired (``None`` means unbounded). When it reaches zero the series ends.
    """
    if remaining is not None and remaining <= 0:
        return None

    next_remaining = None if remaining is None else remaining - 1
    return RecurrenceResult(next_occurrence(last, data, now=now), next_remaining)


def describe_interval(data: Mapping[str, int]) -> str:
    """Render an interval mapping as ``"2 weeks, 3 hours"`` for display."""
    parts = []
    for field in INTERVAL_FIELDS:
        value = data.get(field)
        if value:
            unit = field[:-1] if value == 1 else field
            parts.append(f'{value} {unit}')
    return ', '.join(parts)
