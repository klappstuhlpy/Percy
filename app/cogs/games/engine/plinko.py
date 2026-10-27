"""Pure engine for Plinko.

A ball falls through ``rows`` pegs, bouncing left or right with equal odds, and lands
in one of ``rows + 1`` slots (binomial). Each slot pays a multiplier; edges pay most.
Tables are shaped per risk level and scaled so the exact RTP is :data:`TARGET_RTP`.
No Discord imports — fully unit-testable.
"""

from __future__ import annotations

import random
from math import comb

__all__ = ('RISKS', 'ROWS', 'TARGET_RTP', 'drop', 'multipliers', 'rtp')

TARGET_RTP: float = 0.97
ROWS: tuple[int, ...] = (8, 12, 16)
#: risk -> (curve exponent, floor weight at the centre slots)
RISKS: dict[str, tuple[float, float]] = {'low': (1.5, 0.3), 'medium': (2.5, 0.1), 'high': (4.0, 0.0)}


def _probs(rows: int) -> list[float]:
    return [comb(rows, k) / 2**rows for k in range(rows + 1)]


def multipliers(rows: int, risk: str) -> list[float]:
    """Slot multipliers (left to right, symmetric) for ``rows`` and ``risk``."""
    power, floor = RISKS[risk]
    half = rows / 2
    raw = [floor + (abs(k - half) / half) ** power for k in range(rows + 1)]
    scale = TARGET_RTP / sum(p * w for p, w in zip(_probs(rows), raw, strict=True))
    return [round(w * scale, 2) for w in raw]


def rtp(rows: int, risk: str) -> float:
    """Exact expected return per coin staked."""
    return sum(p * m for p, m in zip(_probs(rows), multipliers(rows, risk), strict=True))


def drop(rows: int, risk: str, rng: random.Random | None = None) -> tuple[list[int], int, float]:
    """Drop one ball. Returns ``(path, slot, multiplier)`` where path is 0 (left) / 1 (right) per row."""
    r = rng or random
    path = [r.getrandbits(1) for _ in range(rows)]
    slot = sum(path)
    return path, slot, multipliers(rows, risk)[slot]
