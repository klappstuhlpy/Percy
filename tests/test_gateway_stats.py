"""Tests for :mod:`app.services.gateway_stats`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services import GatewayTraffic, summarize_gateway_traffic

NOW = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=1)


def test_empty_inputs_yield_empty_traffic() -> None:
    traffic = summarize_gateway_traffic({}, {}, since=SINCE)

    assert traffic == GatewayTraffic(identifies={}, resumes={})
    assert traffic.total_identifies == 0
    assert traffic.total_resumes == 0


def test_only_events_after_cutoff_are_counted() -> None:
    recent = NOW - timedelta(hours=1)
    old = NOW - timedelta(days=2)

    traffic = summarize_gateway_traffic(
        {0: [recent, old, recent], 1: [old]},
        {0: [recent]},
        since=SINCE,
    )

    assert traffic.identifies == {0: 2, 1: 0}
    assert traffic.resumes == {0: 1}
    assert traffic.total_identifies == 2
    assert traffic.total_resumes == 1


def test_cutoff_is_strict() -> None:
    # A timestamp exactly equal to `since` is not counted (strictly newer only).
    traffic = summarize_gateway_traffic({0: [SINCE]}, {}, since=SINCE)

    assert traffic.identifies == {0: 0}


def test_shards_without_recent_events_still_appear() -> None:
    old = NOW - timedelta(days=5)
    traffic = summarize_gateway_traffic({3: [old], 7: []}, {}, since=SINCE)

    assert traffic.identifies == {3: 0, 7: 0}
    assert traffic.total_identifies == 0
