"""Pure engine for the Mines (a.k.a. Diamonds) game.

A grid hides a fixed number of mines among gems. Each gem revealed raises the
cash-out multiplier by the fair odds of having dodged a mine
(``remaining_tiles / remaining_safe``), shaved by a house edge. Hitting a mine
busts the run. No Discord imports — fully unit-testable.
"""

from __future__ import annotations

import random

__all__ = ('COLS', 'ROWS', 'TILES', 'Mines')

COLS: int = 5
ROWS: int = 4
TILES: int = COLS * ROWS
HOUSE_EDGE: float = 0.97


class Mines:
    """Stateful single-run Mines game over a :data:`TILES`-tile grid."""

    def __init__(self, mine_count: int, rng: random.Random | None = None) -> None:
        if not 1 <= mine_count <= TILES - 1:
            raise ValueError(f"mine_count must be between 1 and {TILES - 1}")
        self._rng = rng or random.Random()
        self.mine_count: int = mine_count
        self.mine_positions: set[int] = set(self._rng.sample(range(TILES), mine_count))
        self.revealed: set[int] = set()
        self.busted: bool = False

    @property
    def safe_total(self) -> int:
        """Number of gem (non-mine) tiles."""
        return TILES - self.mine_count

    @property
    def safe_revealed(self) -> int:
        return len(self.revealed)

    @property
    def cleared(self) -> bool:
        """Whether every gem has been revealed (max win)."""
        return self.safe_revealed == self.safe_total

    @property
    def multiplier(self) -> float:
        """Current cash-out multiplier for the gems revealed so far."""
        if self.safe_revealed == 0:
            return 1.0
        fair = 1.0
        for i in range(self.safe_revealed):
            fair *= (TILES - i) / (self.safe_total - i)
        return round(fair * HOUSE_EDGE, 2)

    def next_multiplier(self) -> float:
        """The multiplier the player would reach by revealing one more gem."""
        if self.safe_revealed >= self.safe_total:
            return self.multiplier
        fair = 1.0
        for i in range(self.safe_revealed + 1):
            fair *= (TILES - i) / (self.safe_total - i)
        return round(fair * HOUSE_EDGE, 2)

    def reveal(self, index: int) -> bool:
        """Reveals a tile. Returns ``True`` for a gem, ``False`` (and busts) for a mine."""
        if self.busted:
            raise RuntimeError("cannot reveal after busting")
        if index in self.mine_positions:
            self.busted = True
            return False
        self.revealed.add(index)
        return True
