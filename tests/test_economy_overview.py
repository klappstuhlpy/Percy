"""Tests for the cooldowns / multipliers text helpers."""

from __future__ import annotations

from app.services.economy import describe_multipliers, split_cooldowns, tool_uses_left


def test_split_cooldowns_orders_waiting_soonest_first() -> None:
    ready, waiting = split_cooldowns([('b', 0.0), ('slow', 90.0), ('a', 0.0), ('fast', 5.0)])
    assert ready == ['a', 'b']
    assert waiting == [('fast', 5.0), ('slow', 90.0)]


def test_tool_uses_left() -> None:
    assert tool_uses_left(0, None, 25) == 0
    assert tool_uses_left(1, None, 25) == 25
    assert tool_uses_left(1, 7, 25) == 7
    assert tool_uses_left(2, 7, 25) == 32


def test_describe_multipliers_combines_guild_and_prestige() -> None:
    lines = describe_multipliers(
        guild=1.5, prestige=1.2, loot=1.0, xp=2.0, vote=1.0, tools=[('Fishing Rod', 10), ('Shovel', None)]
    )
    assert lines[-1] == 'Combined earning multiplier: **x1.80**'
    assert 'Loot boost: **x1** (none active)' in lines
    assert 'XP boost: **x2**' in lines
    assert 'Fishing Rod: **10** uses left' in lines
    assert 'Shovel: not owned' in lines
