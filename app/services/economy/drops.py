"""Random chat drops (no ``discord`` imports): when one appears and what it holds."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.economy.catalogue import _RARITY_WEIGHT, CATALOGUE, CatalogueItem

if TYPE_CHECKING:
    import datetime

__all__ = (
    'DROP_CHANCE_PER_MESSAGE',
    'DROP_COINS',
    'DROP_COIN_SHARE',
    'DROP_GRAB_TIMEOUT',
    'DROP_MIN_INTERVAL',
    'ChatDrop',
    'roll_chat_drop',
    'should_drop',
)

#: Minimum seconds between two drops in one guild.
DROP_MIN_INTERVAL = 900
#: Chance that a message (outside the interval) spawns a drop.
DROP_CHANCE_PER_MESSAGE = 0.03
#: Coin range of a coin drop (before payout scaling).
DROP_COINS = (150, 900)
#: Share of drops that are coins; the rest are catalogue items.
DROP_COIN_SHARE = 0.7
#: Seconds the Grab button stays live.
DROP_GRAB_TIMEOUT = 30

_ITEM_POOL = tuple(i for i in CATALOGUE if i.drop_from is not None)


@dataclass(frozen=True, slots=True)
class ChatDrop:
    """What a drop holds: ``coins`` (when ``item`` is ``None``) or one catalogue ``item``."""

    coins: int = 0
    item: CatalogueItem | None = None


def should_drop(
    last_drop_at: datetime.datetime | None, now: datetime.datetime, rng: random.Random | None = None
) -> bool:
    """``False`` inside :data:`DROP_MIN_INTERVAL` of ``last_drop_at``, otherwise a chance roll."""
    if last_drop_at is not None and (now - last_drop_at).total_seconds() < DROP_MIN_INTERVAL:
        return False
    return (rng or random).random() < DROP_CHANCE_PER_MESSAGE


def roll_chat_drop(rng: random.Random | None = None) -> ChatDrop:
    """Roll a drop: coins most of the time, otherwise a rarity-weighted catalogue drop item."""
    chooser = rng or random
    if chooser.random() < DROP_COIN_SHARE:
        return ChatDrop(coins=chooser.randint(*DROP_COINS))
    item = chooser.choices(_ITEM_POOL, weights=[_RARITY_WEIGHT[i.rarity] for i in _ITEM_POOL], k=1)[0]
    return ChatDrop(item=item)
