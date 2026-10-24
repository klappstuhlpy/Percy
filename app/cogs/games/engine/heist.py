"""Pure engine for the group bank heist.

A crew robs one member's **bank**. Every extra member raises the odds (capped), a
success takes a random slice of the bank split evenly across the crew, and a failure
fines each member a slice of their own cash. No Discord imports.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

__all__ = (
    'ENTRY_CASH',
    'MAX_CREW',
    'MIN_CREW',
    'MIN_TARGET_BANK',
    'HeistResult',
    'resolve_heist',
    'success_chance',
)

MIN_CREW: int = 2
MAX_CREW: int = 10
#: Cash each crew member must carry — it is what a failure fines.
ENTRY_CASH: int = 500
#: A bank below this is not worth casing.
MIN_TARGET_BANK: int = 1_000
BASE_CHANCE: float = 0.30
CHANCE_PER_MEMBER: float = 0.08
MAX_CHANCE: float = 0.75
LOOT_FRACTION: tuple[float, float] = (0.10, 0.35)
FINE_FRACTION: tuple[float, float] = (0.10, 0.25)


def success_chance(crew: int) -> float:
    """Odds of a crew of ``crew`` members pulling it off."""
    return min(MAX_CHANCE, BASE_CHANCE + CHANCE_PER_MEMBER * (crew - 1))


@dataclass(frozen=True, slots=True)
class HeistResult:
    success: bool
    #: Total taken from the target's bank (0 on failure).
    loot: int
    #: Each member's share of the loot (0 on failure).
    share: int
    #: Fraction of their cash each member is fined on failure (0.0 on success).
    fine_fraction: float


def resolve_heist(crew: int, target_bank: int, rng: random.Random | None = None) -> HeistResult:
    """Roll one heist. ``loot`` is always ``share * crew`` so no coin is created or lost in the split."""
    source = rng or random
    if source.random() < success_chance(crew):
        share = int(target_bank * source.uniform(*LOOT_FRACTION)) // crew
        return HeistResult(True, share * crew, share, 0.0)
    return HeistResult(False, 0, 0, source.uniform(*FINE_FRACTION))
