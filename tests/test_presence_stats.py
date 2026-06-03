"""Tests for :mod:`app.services.presence_stats`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services import PresenceBreakdown, summarize_presence

NOW = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)


def _at(minutes_ago: float) -> datetime:
    return NOW - timedelta(minutes=minutes_ago)


def test_single_record_yields_no_durations() -> None:
    # With one record there is no interval to attribute time to.
    breakdown = summarize_presence([(_at(0), "Online")])

    assert breakdown.durations == {"Online": 0.0, "Idle": 0.0, "Do Not Disturb": 0.0, "Offline": 0.0}
    assert breakdown.has_data is False
    assert breakdown.earliest == _at(0)


def test_interval_is_credited_to_the_earlier_records_prior_status() -> None:
    # Records are newest-first (changed_at DESC). The gap between consecutive
    # timestamps is credited to the older record's `status_before`.
    changes = [
        (_at(0), "Idle"),  # newest
        (_at(10), "Online"),  # 10 min gap -> credited "Online"
        (_at(30), "Offline"),  # 20 min gap -> credited "Offline"
    ]

    breakdown = summarize_presence(changes)

    assert breakdown.durations["Online"] == 10 * 60
    assert breakdown.durations["Offline"] == 20 * 60
    assert breakdown.durations["Idle"] == 0.0
    assert breakdown.has_data is True
    assert breakdown.earliest == _at(30)


def test_repeated_status_accumulates() -> None:
    changes = [
        (_at(0), "Online"),
        (_at(5), "Online"),  # +5 min Online
        (_at(15), "Online"),  # +10 min Online
    ]

    breakdown = summarize_presence(changes)

    assert breakdown.durations["Online"] == 15 * 60


def test_duplicate_timestamps_collapse_last_wins() -> None:
    # Two records share a timestamp; the later one in iteration order wins,
    # matching the original dict-keyed-by-timestamp behaviour.
    changes = [
        (_at(0), "Idle"),
        (_at(10), "Online"),
        (_at(10), "Offline"),  # same timestamp as previous -> overwrites to "Offline"
    ]

    breakdown = summarize_presence(changes)

    # Only one interval survives: _at(0) - _at(10) == 10 min, credited to "Offline".
    assert breakdown.durations["Offline"] == 10 * 60
    assert breakdown.durations["Online"] == 0.0
    assert breakdown.earliest == _at(10)


def test_returns_breakdown_dataclass() -> None:
    breakdown = summarize_presence([(_at(0), "Online"), (_at(1), "Idle")])

    assert isinstance(breakdown, PresenceBreakdown)
