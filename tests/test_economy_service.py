"""Tests for :mod:`app.services.economy`."""

from __future__ import annotations

import datetime
import random

from app.services.economy import (
    BOOST_MAX_DURATION_MINUTES,
    BOOST_MAX_PERCENT,
    DAILY_BASE,
    DAILY_STREAK_BONUS,
    DAILY_STREAK_CAP,
    FISHING_TABLE,
    HUNTING_TABLE,
    ITEM_EFFECTS,
    LOOTBOX_BANDS,
    LootEntry,
    boost_multiplier,
    compute_daily,
    describe_effect,
    pick_weighted_winner,
    roll_loot,
    roll_lootbox,
    sell_price,
    validate_item_effect,
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


# -- roll_lootbox ------------------------------------------------------------


def test_roll_lootbox_payout_within_band_bounds() -> None:
    lowest = min(band[0] for band in LOOTBOX_BANDS)
    highest = max(band[1] for band in LOOTBOX_BANDS)
    for seed in range(200):
        payout = roll_lootbox(1000, rng=random.Random(seed))
        assert int(1000 * lowest) <= payout <= int(1000 * highest)


def test_roll_lootbox_is_deterministic_for_a_seed() -> None:
    assert roll_lootbox(500, rng=random.Random(3)) == roll_lootbox(500, rng=random.Random(3))


def test_roll_lootbox_zero_value_pays_nothing() -> None:
    assert roll_lootbox(0, rng=random.Random(0)) == 0


# -- boost_multiplier --------------------------------------------------------


def test_boost_multiplier_converts_percent() -> None:
    assert boost_multiplier(50) == 1.5
    assert boost_multiplier(100) == 2.0
    assert boost_multiplier(0) == 1.0


# -- validate_item_effect ----------------------------------------------------


def test_validate_accepts_plain_items() -> None:
    assert validate_item_effect('none', None, None) is None


def test_validate_rejects_unknown_effect() -> None:
    assert validate_item_effect('teleport', 1, None) is not None


def test_validate_cash_and_lootbox_need_positive_value() -> None:
    assert validate_item_effect('cash', None, None) is not None
    assert validate_item_effect('cash', 0, None) is not None
    assert validate_item_effect('cash', 100, None) is None
    assert validate_item_effect('lootbox', 250, None) is None


def test_validate_boosts_need_percent_and_duration() -> None:
    assert validate_item_effect('xp_boost', None, 60) is not None
    assert validate_item_effect('xp_boost', 50, None) is not None
    assert validate_item_effect('xp_boost', BOOST_MAX_PERCENT + 1, 60) is not None
    assert validate_item_effect('xp_boost', 50, BOOST_MAX_DURATION_MINUTES + 1) is not None
    assert validate_item_effect('xp_boost', 50, 60) is None
    assert validate_item_effect('loot_boost', BOOST_MAX_PERCENT, BOOST_MAX_DURATION_MINUTES) is None


def test_validate_role_needs_a_role_id() -> None:
    assert validate_item_effect('role', None, None) is not None
    assert validate_item_effect('role', 1234567890, None) is None


# -- describe_effect ---------------------------------------------------------


def test_describe_effect_none_is_blank() -> None:
    assert describe_effect('none', None, None) is None


def test_describe_effect_covers_every_other_effect() -> None:
    for effect in ITEM_EFFECTS:
        if effect == 'none':
            continue
        line = describe_effect(effect, 100, 60)
        assert line


def test_describe_effect_includes_payload() -> None:
    assert '1,000' in describe_effect('cash', 1000, None)
    assert '+25%' in describe_effect('xp_boost', 25, 60)
    assert '60 minutes' in describe_effect('loot_boost', 25, 60)


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
