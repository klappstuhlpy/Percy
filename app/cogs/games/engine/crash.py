"""Pure engine for Crash.

A multiplier starts at x1.00 and climbs every tick until it hits a hidden crash
point. Cashing out before then pays ``bet * multiplier``; riding past it loses the
stake. The crash point is drawn so that ``P(crash > m) = HOUSE_EDGE / m``, which
makes every cash-out target worth exactly :data:`HOUSE_EDGE` of the stake on average.
No Discord imports — fully unit-testable.
"""

from __future__ import annotations

import math
import random

__all__ = ('GROWTH', 'HOUSE_EDGE', 'MAX_MULTIPLIER', 'Crash', 'draw_crash_point', 'multiplier_at')

HOUSE_EDGE: float = 0.97
#: Per-tick growth factor: x2 after 5 ticks, x10 after ~17.
GROWTH: float = 1.15
#: Ceiling the multiplier never climbs past; a run that reaches it is cashed out automatically.
MAX_MULTIPLIER: float = 100.0


def draw_crash_point(rng: random.Random | None = None) -> float:
    """A crash point with ``P(point > m) = HOUSE_EDGE / m`` for ``m >= 1`` (floored to 2 decimals)."""
    u = (rng or random).random()
    return max(1.0, math.floor(HOUSE_EDGE / (1.0 - u) * 100) / 100)


def multiplier_at(tick: int) -> float:
    """The displayed multiplier after ``tick`` ticks, capped at :data:`MAX_MULTIPLIER`."""
    return min(MAX_MULTIPLIER, round(GROWTH**tick, 2))


class Crash:
    """One Crash run. Call :meth:`tick` on a timer; :meth:`cash_out` when the player bails."""

    def __init__(self, auto_cashout: float | None = None, rng: random.Random | None = None) -> None:
        self.crash_point: float = draw_crash_point(rng)
        self.auto_cashout = auto_cashout
        self.tick_count: int = 0
        self.crashed: bool = False
        #: The multiplier the run was cashed out at, once it was.
        self.cashed_at: float | None = None

    @property
    def multiplier(self) -> float:
        return multiplier_at(self.tick_count)

    @property
    def finished(self) -> bool:
        return self.crashed or self.cashed_at is not None

    def tick(self) -> None:
        """Advance one step: crash, auto-cash-out, cap-cash-out, or keep climbing."""
        if self.finished:
            return
        self.tick_count += 1
        current = self.multiplier
        if self.auto_cashout is not None and self.auto_cashout < self.crash_point and current >= self.auto_cashout:
            self.cashed_at = self.auto_cashout
        elif current >= self.crash_point:
            self.crashed = True
        elif current >= MAX_MULTIPLIER:
            self.cashed_at = MAX_MULTIPLIER

    def cash_out(self) -> float | None:
        """Lock in the current multiplier; ``None`` when the run already ended or has not started."""
        if self.finished or self.tick_count == 0:
            return None
        self.cashed_at = self.multiplier
        return self.cashed_at
