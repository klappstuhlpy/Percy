"""Pure helpers for the time-series analytics API.

Turns a loose ``range``/``granularity`` query into concrete parameters and zero-fills
sparse aggregation results into a contiguous series (so a chart never shows a gap where a
bucket simply had no activity). Discord-free and unit-testable; the SQL aggregation and the
live member-growth computation live in the ``analytics`` router.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

__all__ = (
    'GRANULARITIES',
    'METRICS',
    'RANGES',
    'default_granularity',
    'fill_buckets',
    'floor_bucket',
    'resolve_granularity',
    'resolve_range',
)

#: Accepted ``range`` tokens mapped to a number of days back from now.
RANGES: dict[str, int] = {'24h': 1, '7d': 7, '30d': 30, '90d': 90, '1y': 365}

#: Accepted bucket sizes. ``month`` is intentionally excluded (irregular stepping).
GRANULARITIES: frozenset[str] = frozenset({'hour', 'day', 'week'})

#: Metrics the router knows how to source. ``xp`` is daily-snapshot-based (day/week only).
METRICS: frozenset[str] = frozenset({'commands', 'command_failures', 'xp', 'members'})


def resolve_range(range_str: str) -> int:
    """Map a ``range`` token to a day count, raising ``ValueError`` on an unknown token."""
    try:
        return RANGES[range_str]
    except KeyError:
        raise ValueError(f'unknown range {range_str!r}; expected one of {sorted(RANGES)}') from None


def default_granularity(days: int) -> str:
    """Pick a sensible bucket size for a range so a series stays readable (~24-90 points)."""
    if days <= 2:
        return 'hour'
    if days <= 90:
        return 'day'
    return 'week'


def resolve_granularity(granularity: str | None, days: int) -> str:
    """Validate an explicit granularity or fall back to :func:`default_granularity`."""
    if granularity is None:
        return default_granularity(days)
    if granularity not in GRANULARITIES:
        raise ValueError(f'unknown granularity {granularity!r}; expected one of {sorted(GRANULARITIES)}')
    return granularity


def floor_bucket(dt: datetime, granularity: str) -> datetime:
    """Snap a timestamp down to the start of its bucket (UTC, tz-aware in → tz-aware out)."""
    dt = dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    if granularity == 'hour':
        return dt.replace(minute=0, second=0, microsecond=0)
    day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if granularity == 'week':
        return day - timedelta(days=day.weekday())  # ISO week starts Monday
    return day


def _step(granularity: str) -> timedelta:
    return {'hour': timedelta(hours=1), 'day': timedelta(days=1), 'week': timedelta(weeks=1)}[granularity]


def fill_buckets(
    values: dict[datetime, float],
    *,
    days: int,
    granularity: str,
    now: datetime | None = None,
) -> list[dict]:
    """Expand sparse ``{bucket_start: value}`` data into a contiguous, ordered series.

    Every bucket from ``now - days`` up to and including the current bucket is emitted; a
    bucket with no data reports ``0``. Input keys are floored to their bucket start before
    matching, so callers can pass raw ``date_trunc`` timestamps. Returns a list of
    ``{'bucket': iso8601, 'value': number}`` dicts oldest-first.
    """
    now = (now or datetime.now(UTC))
    now = now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)

    # Normalise the caller's data onto floored bucket keys (summing collisions).
    normalized: dict[datetime, float] = {}
    for raw_key, value in values.items():
        key = floor_bucket(raw_key, granularity)
        normalized[key] = normalized.get(key, 0) + value

    end = floor_bucket(now, granularity)
    start = floor_bucket(now - timedelta(days=days), granularity)
    step = _step(granularity)

    series: list[dict] = []
    cursor = start
    while cursor <= end:
        series.append({'bucket': cursor.isoformat(), 'value': normalized.get(cursor, 0)})
        cursor += step
    return series
