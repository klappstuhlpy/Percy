"""Tests for the pure time-series helpers in :mod:`app.services.analytics`."""

from datetime import UTC, datetime

import pytest

from app.services import analytics


class TestResolveRange:
    def test_known_tokens(self) -> None:
        assert analytics.resolve_range("7d") == 7
        assert analytics.resolve_range("1y") == 365

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            analytics.resolve_range("42q")


class TestGranularity:
    def test_default_by_range(self) -> None:
        assert analytics.default_granularity(1) == "hour"
        assert analytics.default_granularity(30) == "day"
        assert analytics.default_granularity(365) == "week"

    def test_resolve_none_falls_back(self) -> None:
        assert analytics.resolve_granularity(None, 30) == "day"

    def test_resolve_valid(self) -> None:
        assert analytics.resolve_granularity("hour", 30) == "hour"

    def test_resolve_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            analytics.resolve_granularity("fortnight", 30)


class TestFloorBucket:
    def test_hour(self) -> None:
        dt = datetime(2026, 7, 4, 13, 37, 5, tzinfo=UTC)
        assert analytics.floor_bucket(dt, "hour") == datetime(2026, 7, 4, 13, 0, 0, tzinfo=UTC)

    def test_day(self) -> None:
        dt = datetime(2026, 7, 4, 13, 37, tzinfo=UTC)
        assert analytics.floor_bucket(dt, "day") == datetime(2026, 7, 4, 0, 0, tzinfo=UTC)

    def test_week_snaps_to_monday(self) -> None:
        # 2026-07-04 is a Saturday; ISO week starts Monday 2026-06-29.
        dt = datetime(2026, 7, 4, 13, 0, tzinfo=UTC)
        assert analytics.floor_bucket(dt, "week") == datetime(2026, 6, 29, 0, 0, tzinfo=UTC)

    def test_naive_input_treated_as_utc(self) -> None:
        naive = datetime(2026, 7, 4, 13, 37)
        assert analytics.floor_bucket(naive, "day") == datetime(2026, 7, 4, 0, 0, tzinfo=UTC)


class TestFillBuckets:
    def test_contiguous_and_zero_filled(self) -> None:
        now = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)
        values = {datetime(2026, 7, 4, 0, 0, tzinfo=UTC): 5}
        series = analytics.fill_buckets(values, days=7, granularity="day", now=now)
        # start (06-27) .. end (07-04) inclusive == 8 buckets
        assert len(series) == 8
        assert series[0]["bucket"] < series[-1]["bucket"]
        assert series[-1]["value"] == 5
        assert series[0]["value"] == 0

    def test_sums_collisions_within_a_bucket(self) -> None:
        now = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)
        values = {
            datetime(2026, 7, 4, 3, 0, tzinfo=UTC): 2,
            datetime(2026, 7, 4, 9, 0, tzinfo=UTC): 3,
        }
        series = analytics.fill_buckets(values, days=1, granularity="day", now=now)
        total = sum(p["value"] for p in series)
        assert total == 5

    def test_all_zero_when_no_data(self) -> None:
        now = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)
        series = analytics.fill_buckets({}, days=3, granularity="day", now=now)
        assert all(p["value"] == 0 for p in series)
        assert len(series) == 4
