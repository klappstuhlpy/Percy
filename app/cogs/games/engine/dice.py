"""Pure engine for the two-dice (sum-prediction) game.

The player predicts the total of a 2d6 roll. Payout scales inversely with how many
of the 36 combinations produce that total, shaved by a house edge — so betting on
7 (six ways) pays little, betting on 2 or 12 (one way) pays a lot.
"""

from __future__ import annotations

import random

__all__ = ('DIE_FACES', 'MAX_TARGET', 'MIN_TARGET', 'WAYS', 'payout_multiplier', 'roll')

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
