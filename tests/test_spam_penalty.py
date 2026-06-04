"""Tests for :mod:`app.services.spam_penalty`."""

from __future__ import annotations

from app.services.spam_penalty import (
    BURST_WINDOW,
    LOOKBACK_WINDOW,
    ONE_DAY,
    ONE_WEEK,
    compute_spam_penalty,
)

NOW = 1_000_000.0


def test_no_offenses_is_minimum_penalty() -> None:
    # apply_penalty only fires for actual spammers, but the curve should still be defined.
    assert compute_spam_penalty([], now=NOW) == ONE_DAY


def test_single_offense_is_one_day() -> None:
    assert compute_spam_penalty([NOW], now=NOW) == ONE_DAY


def test_offenses_outside_lookback_are_ignored() -> None:
    old = [NOW - LOOKBACK_WINDOW - 1 for _ in range(20)]
    assert compute_spam_penalty([*old, NOW], now=NOW) == ONE_DAY


def test_spread_out_offenses_escalate_slower_than_a_burst() -> None:
    # Five offenses spread across the lookback window (only one inside the burst window).
    spread = [NOW - i * (BURST_WINDOW + 60) for i in range(5)]
    # Five offenses all inside the burst window.
    burst = [NOW - i * 10 for i in range(5)]

    spread_penalty = compute_spam_penalty(spread, now=NOW)
    burst_penalty = compute_spam_penalty(burst, now=NOW)

    assert spread_penalty is not None and burst_penalty is not None
    assert burst_penalty > spread_penalty


def test_sustained_burst_becomes_a_week() -> None:
    # 4 in-burst offenses -> pressure = 4 + 4 = 8 -> a week.
    offenses = [NOW - i * 10 for i in range(4)]
    assert compute_spam_penalty(offenses, now=NOW) == ONE_WEEK


def test_large_burst_is_permanent() -> None:
    offenses = [NOW - i * 10 for i in range(10)]
    assert compute_spam_penalty(offenses, now=NOW) is None


def test_high_frequency_is_permanent() -> None:
    # 16 offenses inside the lookback window but spaced outside the burst window.
    offenses = [NOW - (i + 1) * (BURST_WINDOW + 30) for i in range(16)]
    # Keep them all within the lookback window.
    offenses = [t for t in offenses if NOW - t <= LOOKBACK_WINDOW]
    # Pad to 16 distinct in-lookback timestamps.
    while len(offenses) < 16:
        offenses.append(NOW - LOOKBACK_WINDOW + len(offenses))
    assert compute_spam_penalty(offenses, now=NOW) is None


def test_penalty_is_monotonic_in_burst_size() -> None:
    penalties: list[int | None] = []
    for n in range(1, 11):
        offenses = [NOW - i * 10 for i in range(n)]
        penalties.append(compute_spam_penalty(offenses, now=NOW))

    # None (permanent) sorts as "largest"; map it to infinity for the monotonicity check.
    numeric = [p if p is not None else float("inf") for p in penalties]
    assert numeric == sorted(numeric)
