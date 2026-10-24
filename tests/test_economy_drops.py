"""Tests for random chat drops (pure logic)."""

from __future__ import annotations

import datetime
import random

from app.services.economy import (
    DROP_COINS,
    DROP_MIN_INTERVAL,
    GuildEconomySettings,
    get_catalogue_item,
    roll_chat_drop,
    should_drop,
)

NOW = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)


class _Always(random.Random):
    def random(self) -> float:
        return 0.0


def test_should_drop_blocked_inside_interval() -> None:
    recent = NOW - datetime.timedelta(seconds=DROP_MIN_INTERVAL - 1)
    assert should_drop(recent, NOW, _Always()) is False


def test_should_drop_rolls_chance_outside_interval() -> None:
    old = NOW - datetime.timedelta(seconds=DROP_MIN_INTERVAL)
    assert should_drop(old, NOW, _Always()) is True
    assert should_drop(None, NOW, _Always()) is True
    assert should_drop(None, NOW, random.Random(1)) in (True, False)

    class _Never(random.Random):
        def random(self) -> float:
            return 0.99

    assert should_drop(None, NOW, _Never()) is False


def test_roll_chat_drop_shape() -> None:
    rng = random.Random(7)
    seen_coins = seen_item = False
    for _ in range(300):
        drop = roll_chat_drop(rng)
        if drop.item is None:
            seen_coins = True
            assert DROP_COINS[0] <= drop.coins <= DROP_COINS[1]
        else:
            seen_item = True
            assert drop.coins == 0
            assert drop.item.drop_from is not None
            assert get_catalogue_item(drop.item.key) is drop.item
    assert seen_coins and seen_item


def test_drops_setting_defaults_off() -> None:
    assert GuildEconomySettings.from_record(None).drops_enabled is False
    row = {'payout_multiplier': 1.0, 'rob_enabled': True, 'daily_base': 100, 'max_bet': None}
    assert GuildEconomySettings.from_record(row).drops_enabled is False
