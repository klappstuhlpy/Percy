"""Tests for :mod:`app.services.economy`."""

from __future__ import annotations

import datetime

import random

from app.services.economy import (
    DAILY_BASE,
    DAILY_STREAK_BONUS,
    DAILY_STREAK_CAP,
    FISHING_TABLE,
    HUNTING_TABLE,
    LootEntry,
    compute_daily,
    pick_weighted_winner,
    roll_loot,
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


# -- roll_loot -------------------------------------------------------------


def test_roll_loot_amount_within_chosen_entry_bounds() -> None:
    # Across many seeds every roll must land inside some table entry's [min, max].
    bounds = {(e.name, e.min_value, e.max_value) for e in FISHING_TABLE}
    for seed in range(200):
        catch = roll_loot(FISHING_TABLE, rng=random.Random(seed))
        match = next(b for b in bounds if b[0] == catch.name)
        assert match[1] <= catch.amount <= match[2]


def test_roll_loot_is_deterministic_for_a_seed() -> None:
    a = roll_loot(HUNTING_TABLE, rng=random.Random(7))
    b = roll_loot(HUNTING_TABLE, rng=random.Random(7))
    assert a == b


def test_roll_loot_single_entry_is_forced() -> None:
    only = (LootEntry('certain', 'x', 10, 10, 1),)
    catch = roll_loot(only, rng=random.Random(0))
    assert catch.name == 'certain'
    assert catch.amount == 10


# -- pick_weighted_winner --------------------------------------------------


def test_pick_weighted_winner_returns_an_entrant() -> None:
    winner = pick_weighted_winner([(111, 5), (222, 3)], rng=random.Random(1))
    assert winner in {111, 222}


def test_pick_weighted_winner_ignores_zero_ticket_entries() -> None:
    # Only user 222 holds tickets, so they must always win.
    for seed in range(50):
        assert pick_weighted_winner([(111, 0), (222, 4)], rng=random.Random(seed)) == 222


def test_pick_weighted_winner_without_entrants_is_none() -> None:
    assert pick_weighted_winner([]) is None
    assert pick_weighted_winner([(1, 0), (2, 0)]) is None
