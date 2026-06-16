"""Tests for :mod:`app.cogs.starboard.engine`."""

from __future__ import annotations

import datetime

import pytest

from app.cogs.starboard.engine import (
    StarboardAction,
    color_for_stars,
    decide_action,
    is_too_old,
    star_emoji_for,
)

STAR = '\N{WHITE MEDIUM STAR}'
GLOW = '\N{GLOWING STAR}'
DIZZY = '\N{DIZZY SYMBOL}'
SPARKLES = '\N{SPARKLES}'


# -- decide_action ---------------------------------------------------------


def test_create_when_threshold_reached_and_not_posted() -> None:
    assert decide_action(star_count=3, threshold=3, has_entry=False) is StarboardAction.CREATE


def test_update_when_threshold_reached_and_already_posted() -> None:
    assert decide_action(star_count=5, threshold=3, has_entry=True) is StarboardAction.UPDATE


def test_delete_when_below_threshold_but_posted() -> None:
    assert decide_action(star_count=2, threshold=3, has_entry=True) is StarboardAction.DELETE


def test_ignore_when_below_threshold_and_not_posted() -> None:
    assert decide_action(star_count=1, threshold=3, has_entry=False) is StarboardAction.IGNORE


def test_threshold_boundary_is_inclusive() -> None:
    assert decide_action(star_count=3, threshold=3, has_entry=False) is StarboardAction.CREATE
    assert decide_action(star_count=2, threshold=3, has_entry=False) is StarboardAction.IGNORE


# -- star_emoji_for --------------------------------------------------------


@pytest.mark.parametrize(
    ('count', 'expected'),
    [
        (0, STAR),
        (3, STAR),
        (4, STAR),
        (5, GLOW),
        (9, GLOW),
        (10, DIZZY),
        (14, DIZZY),
        (15, SPARKLES),
        (100, SPARKLES),
    ],
)
def test_star_emoji_tiers(count: int, expected: str) -> None:
    assert star_emoji_for(count) == expected


def test_star_emoji_is_monotonic_by_tier_index() -> None:
    tiers = [STAR, GLOW, DIZZY, SPARKLES]
    last = -1
    for count in range(0, 30):
        idx = tiers.index(star_emoji_for(count))
        assert idx >= last
        last = idx


# -- color_for_stars -------------------------------------------------------

#: Mirrors the engine's ramp endpoints so the tests pin the exact clamped colours.
FLOOR_COLOR = (0xF8 << 16) | (0xDB << 8) | 0x5E
CEIL_COLOR = (0xF1 << 16) | (0x9B << 8) | 0x2C


def test_color_at_threshold_is_floor() -> None:
    assert color_for_stars(3, 3) == FLOOR_COLOR


def test_color_below_threshold_clamps_to_floor() -> None:
    assert color_for_stars(0, 3) == FLOOR_COLOR


def test_color_far_above_threshold_clamps_to_ceiling() -> None:
    assert color_for_stars(1000, 3) == CEIL_COLOR
    # The ramp spans 15 stars past the threshold; exactly there is already the ceiling.
    assert color_for_stars(3 + 15, 3) == CEIL_COLOR


def test_color_is_a_valid_rgb_int() -> None:
    for count in range(0, 40):
        value = color_for_stars(count, 3)
        assert 0 <= value <= 0xFFFFFF


def test_color_warms_monotonically_with_count() -> None:
    # Blue falls monotonically (0x5E -> 0x2C) as the colour warms from yellow toward amber.
    previous_blue = 0x100
    for count in range(3, 19):
        blue = color_for_stars(count, 3) & 0xFF
        assert blue <= previous_blue
        previous_blue = blue


# -- is_too_old ------------------------------------------------------------

NOW = datetime.datetime(2026, 6, 16, 12, 0, tzinfo=datetime.UTC)


def test_age_limit_disabled_never_too_old() -> None:
    ancient = NOW - datetime.timedelta(days=3650)
    assert is_too_old(ancient, NOW, 0) is False
    assert is_too_old(ancient, NOW, -5) is False


def test_recent_message_is_not_too_old() -> None:
    recent = NOW - datetime.timedelta(hours=1)
    assert is_too_old(recent, NOW, 24) is False


def test_old_message_is_too_old() -> None:
    old = NOW - datetime.timedelta(hours=25)
    assert is_too_old(old, NOW, 24) is True


def test_age_boundary_is_inclusive() -> None:
    exactly = NOW - datetime.timedelta(hours=24)
    assert is_too_old(exactly, NOW, 24) is False
    just_over = NOW - datetime.timedelta(hours=24, seconds=1)
    assert is_too_old(just_over, NOW, 24) is True
