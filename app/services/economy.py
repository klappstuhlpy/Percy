"""Pure economy logic: daily-reward streaks and item pricing (no ``discord`` imports).

The economy cog owns balances, the shop, and inventories; this module owns the
Discord-free arithmetic — how a daily streak advances and what a reward/sale is worth —
so it can be reasoned about and unit-tested in isolation.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

__all__ = (
    'DAILY_BASE',
    'DAILY_COOLDOWN',
    'DAILY_RESET',
    'DAILY_STREAK_BONUS',
    'DAILY_STREAK_CAP',
    'SELL_RATE',
    'DailyResult',
    'compute_daily',
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
