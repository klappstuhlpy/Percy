"""Pure engine for scratch cards.

A 3x3 card hides one multiplier per tile, each drawn independently from
:data:`PRIZES`. The player scratches :data:`PICKS` tiles and is paid the stake times
the *average* of what they uncovered — so the expected return is simply the mean
prize, :func:`expected_multiplier` (~0.97). No Discord imports.
"""

from __future__ import annotations

import random

__all__ = ('PICKS', 'PRIZES', 'TILES', 'ScratchCard', 'expected_multiplier')

TILES: int = 9
PICKS: int = 3

#: (multiplier, weight) — mostly duds, the odd x10.
PRIZES: tuple[tuple[float, int], ...] = (
    (0.0, 40),
    (0.5, 25),
    (1.0, 15),
    (2.0, 12),
    (3.0, 5),
    (10.0, 3),
)


def expected_multiplier() -> float:
    """The card's expected return per unit staked."""
    total = sum(weight for _, weight in PRIZES)
    return sum(value * weight for value, weight in PRIZES) / total


class ScratchCard:
    """A card with :data:`TILES` hidden multipliers of which :data:`PICKS` may be scratched."""

    def __init__(self, rng: random.Random | None = None) -> None:
        source = rng or random
        values, weights = zip(*PRIZES, strict=True)
        self.tiles: list[float] = source.choices(values, weights=weights, k=TILES)
        self.scratched: list[int] = []

    @property
    def finished(self) -> bool:
        return len(self.scratched) >= PICKS

    def scratch(self, index: int) -> float:
        """Reveal one tile and return its multiplier."""
        if self.finished:
            raise RuntimeError('no scratches left')
        if index in self.scratched:
            raise ValueError('tile already scratched')
        self.scratched.append(index)
        return self.tiles[index]

    @property
    def multiplier(self) -> float:
        """The payout multiplier — the mean of the scratched tiles (over :data:`PICKS`)."""
        return round(sum(self.tiles[i] for i in self.scratched) / PICKS, 2)
