"""Pure engine for the Horse Race game.

Simulates a race between :data:`NUM_HORSES` horses (each step every horse advances a
random amount) and computes parimutuel payouts: the whole betting pool, shaved by a
house edge, is split among the winning bets in proportion to their stake. No Discord
imports — fully unit-testable.
"""

from __future__ import annotations

import random

__all__ = ('NUM_HORSES', 'TRACK_LENGTH', 'parimutuel_multiplier', 'simulate_race')

NUM_HORSES: int = 6
TRACK_LENGTH: int = 12
HOUSE_EDGE: float = 0.9


def simulate_race(rng: random.Random | None = None) -> tuple[int, list[list[int]]]:
    """Runs a race.

    Returns ``(winner_index, frames)`` where ``winner_index`` is 0-based and ``frames``
    is the list of position snapshots (one per tick) for animation. The winner is the
    horse furthest along when the first horse crosses :data:`TRACK_LENGTH`.
    """
    source = rng or random
    positions = [0] * NUM_HORSES
    frames: list[list[int]] = []

    while max(positions) < TRACK_LENGTH:
        for i in range(NUM_HORSES):
            positions[i] = min(TRACK_LENGTH, positions[i] + source.randint(0, 2))
        frames.append(positions.copy())

    winner = max(range(NUM_HORSES), key=lambda i: positions[i])
    return winner, frames


def parimutuel_multiplier(total_pool: int, total_on_winner: int, *, edge: float = HOUSE_EDGE) -> float:
    """Payout multiplier applied to each winning stake.

    Returns ``0.0`` when nobody backed the winning horse (the house keeps the pool).
    """
    if total_on_winner <= 0:
        return 0.0
    return (total_pool * edge) / total_on_winner
