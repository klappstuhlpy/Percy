"""Tests for :mod:`app.services.recurrence`."""

from __future__ import annotations

import datetime

import pytest
from dateutil.relativedelta import relativedelta

from app.services.recurrence import (
    MIN_INTERVAL,
    advance_recurrence,
    describe_interval,
    interval_too_short,
    next_occurrence,
    normalize_interval,
)

UTC = datetime.UTC


def dt(year: int = 2026, month: int = 6, day: int = 1, hour: int = 12) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, tzinfo=UTC)


# -- normalize_interval ----------------------------------------------------


def test_normalize_keeps_only_nonzero_supported_fields() -> None:
    data = normalize_interval(relativedelta(days=1, hours=0, minutes=30))
    assert data == {'days': 1, 'minutes': 30}


def test_normalize_rejects_empty_interval() -> None:
    with pytest.raises(ValueError, match='non-zero'):
        normalize_interval(relativedelta())


def test_normalize_rejects_negative_interval() -> None:
    with pytest.raises(ValueError, match='positive'):
        normalize_interval(relativedelta(days=-1))


# -- interval_too_short ----------------------------------------------------


def test_sub_minute_interval_is_too_short() -> None:
    assert interval_too_short({'seconds': 30}, reference=dt()) is True


def test_minimum_interval_is_allowed() -> None:
    assert interval_too_short({'minutes': 1}, reference=dt()) is False
    assert datetime.timedelta(minutes=1) == MIN_INTERVAL


def test_month_interval_is_not_too_short() -> None:
    assert interval_too_short({'months': 1}, reference=dt()) is False


# -- next_occurrence -------------------------------------------------------


def test_next_occurrence_advances_one_interval_when_now_is_at_last() -> None:
    last = dt()
    assert next_occurrence(last, {'days': 1}, now=last) == dt(day=2)


def test_next_occurrence_skips_missed_intervals() -> None:
    last = dt(day=1)
    # "now" is 3.5 days later -> next daily run should be day 5, not day 2.
    now = dt(day=4, hour=18)
    assert next_occurrence(last, {'days': 1}, now=now) == dt(day=5)


def test_next_occurrence_handles_month_lengths() -> None:
    # Monthly from Jan 31 should land on the dateutil-clamped month ends.
    last = datetime.datetime(2026, 1, 31, 12, tzinfo=UTC)
    nxt = next_occurrence(last, {'months': 1}, now=last)
    assert nxt == datetime.datetime(2026, 2, 28, 12, tzinfo=UTC)


# -- advance_recurrence ----------------------------------------------------


def test_unbounded_series_keeps_going() -> None:
    last = dt()
    result = advance_recurrence(last, {'days': 1}, now=last, remaining=None)
    assert result is not None
    assert result.next_run == dt(day=2)
    assert result.remaining is None


def test_bounded_series_counts_down_then_stops() -> None:
    last = dt()
    data = {'days': 1}

    # Created with count=3 -> remaining starts at 2 (occurrences after the first).
    r1 = advance_recurrence(last, data, now=last, remaining=2)
    assert r1 is not None and r1.remaining == 1
    r2 = advance_recurrence(r1.next_run, data, now=r1.next_run, remaining=r1.remaining)
    assert r2 is not None and r2.remaining == 0
    # Final fire has remaining 0 -> series ends.
    assert advance_recurrence(r2.next_run, data, now=r2.next_run, remaining=r2.remaining) is None


# -- describe_interval -----------------------------------------------------


def test_describe_interval_singular_and_plural() -> None:
    assert describe_interval({'days': 1}) == '1 day'
    assert describe_interval({'weeks': 2, 'hours': 3}) == '2 weeks, 3 hours'
