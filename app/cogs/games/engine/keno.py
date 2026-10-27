"""Pure engine for Keno.

Pick 1-10 numbers from 1-40; the house draws 10. The paytable is derived from the
hypergeometric odds: tiers at or above half the picks share :data:`TARGET_RTP`
with each tier's share proportional to ``sqrt(P(hits))``, so a tier pays
``c / sqrt(P)`` (capped at :data:`MAX_MULTIPLIER`). No Discord imports.
"""

from __future__ import annotations

import random
from functools import cache
from math import comb, sqrt

__all__ = ('DRAWN', 'MAX_MULTIPLIER', 'MAX_PICKS', 'POOL', 'TARGET_RTP', 'draw', 'paytable', 'rtp', 'score')

POOL: int = 40
DRAWN: int = 10
MAX_PICKS: int = 10
TARGET_RTP: float = 0.93
MAX_MULTIPLIER: float = 10000.0


def _p(picks: int, hits: int) -> float:
    return comb(picks, hits) * comb(POOL - picks, DRAWN - hits) / comb(POOL, DRAWN)


@cache
def paytable(picks: int) -> dict[int, float]:
    """``{hits: multiplier}`` for paying tiers only."""
    tiers = range(max(1, (picks + 1) // 2), picks + 1)
    c = TARGET_RTP / sum(sqrt(_p(picks, h)) for h in tiers)
    return {h: min(MAX_MULTIPLIER, round(c / sqrt(_p(picks, h)), 2)) for h in tiers}


def rtp(picks: int) -> float:
    """Exact expected return per coin staked."""
    return sum(_p(picks, h) * m for h, m in paytable(picks).items())


def draw(rng: random.Random | None = None) -> set[int]:
    return set((rng or random).sample(range(1, POOL + 1), DRAWN))


def score(picks: set[int], drawn: set[int]) -> tuple[set[int], float]:
    """Matching numbers and the payout multiplier (0.0 when the tier does not pay)."""
    hits = picks & drawn
    return hits, paytable(len(picks)).get(len(hits), 0.0)
