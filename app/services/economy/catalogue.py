"""Built-in economy items (no ``discord`` imports): activity drops and the tools that unlock them.

Defined in code like :data:`~app.services.economy.pets.PET_SPECIES`, so every server shares one balanced
catalogue. A guild only gets a row (``economy_items.builtin_key``) the first time an item is needed;
the repository keeps name/price/emoji/rarity in sync with this file on every upsert.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

__all__ = (
    'ACTIVITIES',
    'CATALOGUE',
    'DROP_CHANCE',
    'NO_TOOL_PAYOUT',
    'CatalogueItem',
    'builtin_error',
    'drops_for',
    'get_catalogue_item',
    'name_reserved_error',
    'roll_drop',
    'tool_for_activity',
)

#: Activities that can have a tool and a drop table.
ACTIVITIES: tuple[str, ...] = ('fish', 'hunt', 'dig')
#: Chance that an activity done with the right tool also drops an item.
DROP_CHANCE = 0.35
#: Share of the cash payout kept when the matching tool is missing.
NO_TOOL_PAYOUT = 0.6

#: Relative drop weight per rarity (rarer = scarcer).
_RARITY_WEIGHT = {'common': 50, 'uncommon': 30, 'rare': 14, 'epic': 5, 'legendary': 1}


@dataclass(frozen=True, slots=True)
class CatalogueItem:
    """One built-in item. ``price`` is the shop price; the sell value derives via ``sell_price``."""

    key: str
    name: str
    emoji: str
    rarity: str
    price: int
    effect: str = 'none'
    for_sale: bool = False
    tool_for: str | None = None
    durability: int | None = None
    drop_from: str | None = None
    description: str = ''


CATALOGUE: tuple[CatalogueItem, ...] = (
    # Tools - buyable, wear out after `durability` uses.
    CatalogueItem('fishing_rod', 'Fishing Rod', '\N{FISHING POLE AND FISH}', 'uncommon', 600, for_sale=True,
                  tool_for='fish', durability=25, description='Needed for full `fish` payouts and item drops.'),
    CatalogueItem('hunting_rifle', 'Hunting Rifle', '\N{PISTOL}', 'rare', 1500, for_sale=True,
                  tool_for='hunt', durability=20, description='Needed for full `hunt` payouts and item drops.'),
    CatalogueItem('shovel', 'Shovel', '\N{PICK}', 'uncommon', 500, for_sale=True,
                  tool_for='dig', durability=25, description='Needed for full `dig` payouts and item drops.'),
    # Fish
    CatalogueItem('sardine', 'Sardine', '\N{FISH}', 'common', 30, drop_from='fish', description='A shiny little fish.'),
    CatalogueItem('salmon', 'Salmon', '\N{TROPICAL FISH}', 'uncommon', 90, drop_from='fish'),
    CatalogueItem('pufferfish', 'Pufferfish', '\N{BLOWFISH}', 'rare', 220, drop_from='fish'),
    CatalogueItem('golden_fish', 'Golden Fish', '\N{SPARKLES}', 'epic', 700, drop_from='fish',
                  description='Said to bring good fortune to whoever sells it.'),
    # Hunt
    CatalogueItem('rabbit_hide', 'Rabbit Hide', '\N{RABBIT FACE}', 'common', 50, drop_from='hunt'),
    CatalogueItem('boar_tusk', 'Boar Tusk', '\N{BOAR}', 'uncommon', 120, drop_from='hunt'),
    CatalogueItem('bear_pelt', 'Bear Pelt', '\N{BEAR FACE}', 'rare', 400, drop_from='hunt'),
    CatalogueItem('stag_antlers', 'Stag Antlers', '\N{DEER}', 'epic', 900, drop_from='hunt',
                  description='A trophy worth bragging about.'),
    # Dig
    CatalogueItem('bent_spoon', 'Bent Spoon', '\N{SPOON}', 'common', 12, drop_from='dig', description='Junk.'),
    CatalogueItem('ancient_coin', 'Ancient Coin', '\N{COIN}', 'uncommon', 150, drop_from='dig'),
    CatalogueItem('jade_idol', 'Jade Idol', '\N{MOYAI}', 'rare', 500, drop_from='dig'),
    CatalogueItem('ancient_relic', 'Ancient Relic', '\N{AMPHORA}', 'legendary', 1500, drop_from='dig'),
)

_BY_KEY = {item.key: item for item in CATALOGUE}


def get_catalogue_item(key: str | None) -> CatalogueItem | None:
    """The built-in item for ``key`` (or ``None``)."""
    return _BY_KEY.get(key or '')


def tool_for_activity(activity: str) -> CatalogueItem | None:
    """The tool that unlocks ``activity`` (or ``None``)."""
    return next((i for i in CATALOGUE if i.tool_for == activity), None)


def drops_for(activity: str) -> tuple[CatalogueItem, ...]:
    """Every item ``activity`` can drop."""
    return tuple(i for i in CATALOGUE if i.drop_from == activity)


def roll_drop(activity: str, rng: random.Random | None = None) -> CatalogueItem | None:
    """Roll an item drop for ``activity``: ``None`` most of the time, otherwise a rarity-weighted item."""
    chooser = rng or random
    pool = drops_for(activity)
    if not pool or chooser.random() >= DROP_CHANCE:
        return None
    return chooser.choices(pool, weights=[_RARITY_WEIGHT[i.rarity] for i in pool], k=1)[0]


def builtin_error(item: Mapping[str, Any], verb: str) -> str | None:
    """The refusal for ``verb`` (``edit``/``remove``) on a built-in item, or ``None`` for an admin item."""
    if not item.get('builtin_key'):
        return None
    return (
        f'**{item["name"]}** is a built-in Percy item and cannot be {"edited" if verb == "edit" else "removed"}. '
        f'Its name, price and drops are managed by Percy.'
    )


def name_reserved_error(name: str) -> str | None:
    """The refusal for naming an admin item after a built-in one (case-insensitive), or ``None``."""
    taken = {i.name.lower() for i in CATALOGUE}
    if name.strip().lower().removesuffix(' ✦') in taken:
        return f'**{name.strip()}** is the name of a built-in Percy item - pick another name.'
    return None
