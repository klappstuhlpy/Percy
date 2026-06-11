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
    'BOOST_MAX_DURATION_MINUTES',
    'BOOST_MAX_PERCENT',
    'DAILY_BASE',
    'DAILY_COOLDOWN',
    'DAILY_RESET',
    'DAILY_STREAK_BONUS',
    'DAILY_STREAK_CAP',
    'FISHING_COOLDOWN',
    'FISHING_TABLE',
    'HUNTING_COOLDOWN',
    'HUNTING_TABLE',
    'ITEM_EFFECTS',
    'LOOTBOX_BANDS',
    'SELL_RATE',
    'Catch',
    'DailyResult',
    'LootEntry',
    'boost_multiplier',
    'compute_daily',
    'describe_effect',
    'pick_weighted_winner',
    'roll_loot',
    'roll_lootbox',
    'sell_price',
    'validate_item_effect',
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


# -- item effects ----------------------------------------------------------

#: Every effect a shop item can carry; ``none`` items are plain collectibles.
ITEM_EFFECTS: tuple[str, ...] = ('none', 'cash', 'lootbox', 'role', 'xp_boost', 'loot_boost')

#: Highest bonus percent a boost item may grant (+500% = x6).
BOOST_MAX_PERCENT = 500
#: Longest a boost item may last (one week).
BOOST_MAX_DURATION_MINUTES = 7 * 24 * 60

#: Lootbox payout bands as ``(low, high, weight)`` fractions of the item's base value:
#: mostly around par, sometimes a bust, rarely a jackpot.
LOOTBOX_BANDS: tuple[tuple[float, float, int], ...] = (
    (0.2, 0.6, 30),
    (0.8, 1.4, 50),
    (1.8, 2.5, 15),
    (4.0, 6.0, 5),
)


def roll_lootbox(value: int, *, rng: random.Random | None = None) -> int:
    """Roll a lootbox payout around ``value`` using the weighted :data:`LOOTBOX_BANDS`."""
    chooser = rng or random
    low, high, _ = chooser.choices(LOOTBOX_BANDS, weights=[band[2] for band in LOOTBOX_BANDS], k=1)[0]
    return max(int(value * chooser.uniform(low, high)), 0)


def boost_multiplier(percent: int) -> float:
    """Convert a stored bonus percent (e.g. ``50``) into a multiplier (``1.5``)."""
    return 1.0 + percent / 100


def validate_item_effect(effect: str, value: int | None, duration_minutes: int | None) -> str | None:
    """Validate an item-effect configuration, returning an error message or ``None`` if valid.

    ``value`` carries the cash amount (``cash``/``lootbox``), the bonus percent
    (``xp_boost``/``loot_boost``) or the role id (``role``); ``duration_minutes``
    only applies to boosts.
    """
    if effect not in ITEM_EFFECTS:
        return f'Unknown effect `{effect}`. Valid effects: {", ".join(ITEM_EFFECTS)}.'
    if effect in ('cash', 'lootbox') and (value is None or value < 1):
        return 'This effect needs a positive **value** (the cash amount).'
    if effect in ('xp_boost', 'loot_boost'):
        if value is None or not 1 <= value <= BOOST_MAX_PERCENT:
            return f'Boost items need a **value** between 1 and {BOOST_MAX_PERCENT} (the bonus in percent).'
        if duration_minutes is None or not 1 <= duration_minutes <= BOOST_MAX_DURATION_MINUTES:
            return f'Boost items need a **duration** between 1 and {BOOST_MAX_DURATION_MINUTES} minutes.'
    if effect == 'role' and value is None:
        return 'Role items need a **role** to grant.'
    return None


def describe_effect(effect: str, value: int | None, duration_minutes: int | None) -> str | None:
    """A short human-readable line for what using an item does (``None`` for plain items).

    The ``role`` description is generic; callers that can resolve the role should
    replace it with a proper mention.
    """
    if effect == 'cash':
        return f'Voucher: redeems for {value:,} cash.'
    if effect == 'lootbox':
        return f'Lootbox: pays out around {value:,} cash — luck decides.'
    if effect == 'role':
        return 'Grants a server role when used.'
    if effect == 'xp_boost':
        return f'Boost: +{value}% leveling XP for {duration_minutes} minutes.'
    if effect == 'loot_boost':
        return f'Boost: +{value}% fishing & hunting payouts for {duration_minutes} minutes.'
    return None


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
