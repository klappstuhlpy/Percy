"""Pure economy logic: daily-reward streaks and item pricing (no ``discord`` imports).

The economy cog owns balances, the shop, and inventories; this module owns the
Discord-free arithmetic — how a daily streak advances and what a reward/sale is worth —
so it can be reasoned about and unit-tested in isolation.
"""

from __future__ import annotations

import datetime
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = (
    'DAILY_BASE',
    'DAILY_COOLDOWN',
    'DAILY_RESET',
    'DAILY_STREAK_BONUS',
    'DAILY_STREAK_CAP',
    'FISHING_COOLDOWN',
    'FISHING_TABLE',
    'HUNTING_COOLDOWN',
    'HUNTING_TABLE',
    'SELL_RATE',
    'Catch',
    'DailyResult',
    'LootEntry',
    'compute_daily',
    'pick_weighted_winner',
    'roll_loot',
    'sell_price',
)

#: Base daily payout before streak bonuses.
DAILY_BASE = 250
#: Extra payout per consecutive day, until the cap.
DAILY_STREAK_BONUS = 50
#: Maximum number of streak days that contribute a bonus.
DAILY_STREAK_CAP = 7
#: Minimum time between claims.
DAILY_COOLDOWN = datetime.timedelta(hours=24)
#: A claim later than this after the previous one resets the streak.
DAILY_RESET = datetime.timedelta(hours=48)
#: Fraction of an item's price returned when selling it back.
SELL_RATE = 0.5


@dataclass(frozen=True, slots=True)
class DailyResult:
    """The outcome of a daily-reward claim attempt."""

    claimed: bool
    amount: int
    streak: int
    #: When the reward is next claimable. ``None`` once a claim succeeds.
    next_available: datetime.datetime | None


def compute_daily(
    last_claim: datetime.datetime | None,
    streak: int,
    *,
    now: datetime.datetime,
    base: int = DAILY_BASE,
    bonus: int = DAILY_STREAK_BONUS,
    cap: int = DAILY_STREAK_CAP,
    cooldown: datetime.timedelta = DAILY_COOLDOWN,
    reset: datetime.timedelta = DAILY_RESET,
) -> DailyResult:
    """Resolve a daily-reward claim.

    The streak increments when the previous claim was within ``[cooldown, reset)``,
    resets to 1 if the gap exceeded ``reset`` (or there was no prior claim), and the
    claim is refused (``claimed=False``) when less than ``cooldown`` has elapsed.

    Parameters
    ----------
    last_claim:
        When the user last claimed, or ``None`` if never.
    streak:
        The user's current streak (ignored when ``last_claim`` is ``None``).
    now:
        The current time.

    Returns
    -------
    DailyResult
        ``claimed`` indicates success; on refusal, ``next_available`` says when the
        reward unlocks and ``amount`` is 0.
    """
    if last_claim is not None:
        elapsed = now - last_claim
        if elapsed < cooldown:
            return DailyResult(False, 0, streak, last_claim + cooldown)
        new_streak = streak + 1 if elapsed < reset else 1
    else:
        new_streak = 1

    amount = base + min(new_streak - 1, cap) * bonus
    return DailyResult(True, amount, new_streak, None)


def sell_price(price: int, *, rate: float = SELL_RATE) -> int:
    """The amount returned for selling an item bought at ``price`` (floored, ≥ 0)."""
    return max(int(price * rate), 0)


# -- earning activities (fishing / hunting) -------------------------------

#: Minimum seconds between ``fish`` claims.
FISHING_COOLDOWN = 60
#: Minimum seconds between ``hunt`` claims.
HUNTING_COOLDOWN = 90


@dataclass(frozen=True, slots=True)
class LootEntry:
    """A single weighted outcome of an earning activity."""

    name: str
    emoji: str
    min_value: int
    max_value: int
    weight: int


@dataclass(frozen=True, slots=True)
class Catch:
    """The resolved result of one earning roll."""

    name: str
    emoji: str
    amount: int


#: Fishing outcomes - common low payouts down to rare jackpots, plus a "junk" miss.
FISHING_TABLE: tuple[LootEntry, ...] = (
    LootEntry('an old boot', '\N{ATHLETIC SHOE}', 0, 5, 18),
    LootEntry('a school of sardines', '\N{FISH}', 40, 90, 40),
    LootEntry('a plump salmon', '\N{FISH}', 90, 180, 25),
    LootEntry('a pufferfish', '\N{BLOWFISH}', 150, 280, 12),
    LootEntry('a treasure chest', '\N{NAZAR AMULET}', 400, 800, 4),
    LootEntry('a rare pearl', '\N{OYSTER}', 900, 1600, 1),
)

#: Hunting outcomes - higher variance and a longer cooldown than fishing.
HUNTING_TABLE: tuple[LootEntry, ...] = (
    LootEntry('nothing but tracks', '\N{PAW PRINTS}', 0, 10, 20),
    LootEntry('a rabbit', '\N{RABBIT}', 60, 120, 38),
    LootEntry('a wild boar', '\N{BOAR}', 130, 240, 22),
    LootEntry('a deer', '\N{DEER}', 220, 380, 13),
    LootEntry('a bear', '\N{BEAR FACE}', 500, 950, 5),
    LootEntry('a trophy stag', '\N{DEER}', 1100, 2000, 2),
)


def roll_loot(table: Sequence[LootEntry], *, rng: random.Random | None = None) -> Catch:
    """Pick a weighted outcome from ``table`` and roll its payout amount."""
    chooser = rng or random
    entry = chooser.choices(table, weights=[e.weight for e in table], k=1)[0]
    amount = chooser.randint(entry.min_value, entry.max_value)
    return Catch(entry.name, entry.emoji, amount)


def pick_weighted_winner(
    entries: Sequence[tuple[int, int]], *, rng: random.Random | None = None
) -> int | None:
    """Draw a winner id from ``(user_id, tickets)`` pairs, weighted by ticket count.

    Returns ``None`` if there are no entries or every ticket count is non-positive.
    """
    pool = [(uid, tickets) for uid, tickets in entries if tickets > 0]
    if not pool:
        return None
    chooser = rng or random
    return chooser.choices([uid for uid, _ in pool], weights=[t for _, t in pool], k=1)[0]
