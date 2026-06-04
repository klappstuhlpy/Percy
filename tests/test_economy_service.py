"""Tests for :mod:`app.services.economy`."""

from __future__ import annotations

import datetime

from app.services.economy import (
    DAILY_BASE,
    DAILY_STREAK_BONUS,
    DAILY_STREAK_CAP,
    compute_daily,
    sell_price,
)

UTC = datetime.UTC
NOW = datetime.datetime(2026, 6, 3, 12, 0, tzinfo=UTC)


# -- compute_daily ---------------------------------------------------------


def test_first_ever_claim_starts_streak_at_one() -> None:
    result = compute_daily(None, 0, now=NOW)
    assert result.claimed is True
    assert result.streak == 1
    assert result.amount == DAILY_BASE
    assert result.next_available is None


def test_claim_within_cooldown_is_refused() -> None:
    last = NOW - datetime.timedelta(hours=5)
    result = compute_daily(last, 3, now=NOW)
    assert result.claimed is False
    assert result.amount == 0
    assert result.streak == 3
    assert result.next_available == last + datetime.timedelta(hours=24)


def test_claim_after_cooldown_continues_streak() -> None:
    last = NOW - datetime.timedelta(hours=30)  # within [24h, 48h)
    result = compute_daily(last, 3, now=NOW)
    assert result.claimed is True
    assert result.streak == 4
    assert result.amount == DAILY_BASE + 3 * DAILY_STREAK_BONUS


def test_claim_after_reset_window_restarts_streak() -> None:
    last = NOW - datetime.timedelta(hours=50)  # beyond 48h
    result = compute_daily(last, 9, now=NOW)
    assert result.claimed is True
    assert result.streak == 1
    assert result.amount == DAILY_BASE


def test_streak_bonus_is_capped() -> None:
    # A very long streak should not exceed the capped bonus.
    last = NOW - datetime.timedelta(hours=30)
    result = compute_daily(last, 100, now=NOW)
    assert result.streak == 101
    assert result.amount == DAILY_BASE + DAILY_STREAK_CAP * DAILY_STREAK_BONUS


def test_cooldown_boundary_is_claimable() -> None:
    last = NOW - datetime.timedelta(hours=24)
    result = compute_daily(last, 2, now=NOW)
    assert result.claimed is True
    assert result.streak == 3


# -- sell_price ------------------------------------------------------------


def test_sell_price_is_half_floored() -> None:
    assert sell_price(100) == 50
    assert sell_price(101) == 50
    assert sell_price(1) == 0


def test_sell_price_never_negative() -> None:
    assert sell_price(0) == 0


def test_sell_price_respects_custom_rate() -> None:
    assert sell_price(100, rate=0.8) == 80
