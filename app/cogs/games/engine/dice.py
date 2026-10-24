"""Pure engines for the dice games: two-dice sum prediction, Snake Eyes and Gamble.

The player predicts the total of a 2d6 roll. Payout scales inversely with how many
of the 36 combinations produce that total, shaved by a house edge — so betting on
7 (six ways) pays little, betting on 2 or 12 (one way) pays a lot.
"""

from __future__ import annotations

import random

__all__ = (
    'DIE_FACES',
    'GAMBLE_PAYOUT',
    'GAMBLE_SIDES',
    'MAX_TARGET',
    'MIN_TARGET',
    'SNAKE_BOTH',
    'SNAKE_ONE',
    'WAYS',
    'gamble_roll',
    'payout_multiplier',
    'roll',
    'snake_eyes_multiplier',
)

HOUSE_EDGE: float = 0.9
MIN_TARGET: int = 2
MAX_TARGET: int = 12

#: Number of (d1, d2) combinations that produce each total out of 36.
WAYS: dict[int, int] = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}

#: Unicode die faces indexed by ``value - 1``.
DIE_FACES: str = "⚀⚁⚂⚃⚄⚅"


def roll(rng: random.Random | None = None) -> tuple[int, int]:
    """Rolls two six-sided dice."""
    source = rng or random
    return source.randint(1, 6), source.randint(1, 6)


def payout_multiplier(target: int) -> float:
    """Winning multiplier for predicting ``target`` (the 2d6 total)."""
    if target not in WAYS:
        raise ValueError(f"target must be between {MIN_TARGET} and {MAX_TARGET}")
    fair = 36 / WAYS[target]
    return round(fair * HOUSE_EDGE, 2)


# -- Snake Eyes -------------------------------------------------------------

#: Multiplier when exactly one die shows a 1 (10 of 36 rolls).
SNAKE_ONE: float = 2.0
#: Multiplier for double 1s (1 of 36 rolls). EV = (10*2 + 15) / 36 ≈ 0.97.
SNAKE_BOTH: float = 15.0


def snake_eyes_multiplier(d1: int, d2: int) -> float:
    """Payout multiplier of a Snake Eyes roll (``0.0`` when no die shows a 1)."""
    ones = (d1 == 1) + (d2 == 1)
    return SNAKE_BOTH if ones == 2 else SNAKE_ONE if ones == 1 else 0.0


# -- Gamble (player vs house, one d12 each) ---------------------------------

GAMBLE_SIDES: int = 12
#: Paid on a strictly higher roll; ties go to the house. EV = 11/24 * 2 ≈ 0.92.
GAMBLE_PAYOUT: float = 2.0


def gamble_roll(rng: random.Random | None = None) -> tuple[int, int]:
    """Rolls ``(player, house)`` — one d12 each."""
    source = rng or random
    return source.randint(1, GAMBLE_SIDES), source.randint(1, GAMBLE_SIDES)
