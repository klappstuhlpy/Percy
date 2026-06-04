"""Tests for :mod:`app.cogs.starboard.engine`."""

from __future__ import annotations

import pytest

from app.cogs.starboard.engine import StarboardAction, decide_action, star_emoji_for

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
