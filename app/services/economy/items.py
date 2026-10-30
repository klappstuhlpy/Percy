"""Pure item-catalogue rules: rarity, categories, listing metadata and purchase outcomes.

The shop row carries an ``effect`` (what ``use`` does, see :mod:`.core`) plus the
presentation/gating columns added in V51 (``emoji``, ``rarity``, ``stock``,
``required_role_id``, ``max_per_user``). This module owns everything about those
that does not need Discord: which category an effect belongs to, how rarities
sort, what limits are valid, and the user-facing text for each purchase outcome.
"""

from __future__ import annotations

import enum
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import datetime
    from collections.abc import Mapping, Sequence

__all__ = (
    'CATEGORIES',
    'IMAGE_URL_LIMIT',
    'ITEM_NAME_LIMIT',
    'MAX_ITEM_STOCK',
    'RARITIES',
    'REPLY_LIMIT',
    'Category',
    'PurchaseStatus',
    'Rarity',
    'daily_sale',
    'filter_items',
    'get_rarity',
    'item_category',
    'item_emoji',
    'item_expired',
    'purchase_error',
    'sale_price',
    'validate_item_meta',
)

#: Longest item name accepted (autocomplete choices cap at 100 characters).
ITEM_NAME_LIMIT = 64
#: Highest stock an admin may set on a limited item.
MAX_ITEM_STOCK = 1_000_000
#: Longest ``reply_message`` an item may carry.
REPLY_LIMIT = 500
#: Longest ``image_url`` an item may carry.
IMAGE_URL_LIMIT = 500
#: Signed Discord attachment links expire, so an item picture hosted there breaks within a day.
_EXPIRING_HOSTS = ('cdn.discordapp.com/attachments/', 'media.discordapp.net/attachments/')
#: Items put on sale per guild per UTC day, and the discount bounds (percent).
SALE_COUNT = 2
SALE_MIN, SALE_MAX = 10, 40


@dataclass(frozen=True, slots=True)
class Rarity:
    """A rarity tier; ``order`` sorts rarer items further down a listing."""

    id: str
    name: str
    emoji: str
    colour: int
    order: int


#: Rarity tiers, common first. Purely cosmetic — Dank Memer-style signposting.
RARITIES: tuple[Rarity, ...] = (
    Rarity('common', 'Common', '\N{MEDIUM WHITE CIRCLE}', 0x9AA0A6, 0),
    Rarity('uncommon', 'Uncommon', '\N{LARGE GREEN CIRCLE}', 0x57F287, 1),
    Rarity('rare', 'Rare', '\N{LARGE BLUE CIRCLE}', 0x3B82F6, 2),
    Rarity('epic', 'Epic', '\N{LARGE PURPLE CIRCLE}', 0xA855F7, 3),
    Rarity('legendary', 'Legendary', '\N{LARGE ORANGE CIRCLE}', 0xF59E0B, 4),
)
_RARITY_BY_ID = {r.id: r for r in RARITIES}


def get_rarity(rarity_id: str | None) -> Rarity:
    """The tier for ``rarity_id``, falling back to common for unknown/missing values."""
    return _RARITY_BY_ID.get(rarity_id or '', RARITIES[0])


@dataclass(frozen=True, slots=True)
class Category:
    """A browsing category; every effect maps to exactly one."""

    id: str
    name: str
    emoji: str
    effects: tuple[str, ...]


#: Browsing categories in display order, derived from the item's effect (no extra column).
CATEGORIES: tuple[Category, ...] = (
    Category('consumable', 'Consumables', '\N{WRAPPED PRESENT}', ('cash', 'lootbox', 'bank_space')),
    Category('boost', 'Boosts', '\N{HIGH VOLTAGE SIGN}', ('xp_boost', 'loot_boost', 'rob_shield')),
    Category('role', 'Role Items', '\N{MILITARY MEDAL}', ('role',)),
    Category('collectible', 'Collectibles', '\N{GEM STONE}', ('none',)),
)
_CATEGORY_BY_EFFECT = {effect: c for c in CATEGORIES for effect in c.effects}


def item_category(effect: str | None) -> Category:
    """The category an item with ``effect`` belongs to (unknown effects are collectibles)."""
    return _CATEGORY_BY_EFFECT.get(effect or 'none', CATEGORIES[-1])


def item_emoji(item: Mapping[str, Any]) -> str:
    """The item's own emoji, or its category's when none was configured."""
    return item.get('emoji') or item_category(item.get('effect')).emoji


def filter_items(
    items: Sequence[Mapping[str, Any]], category: str | None = None
) -> list[Mapping[str, Any]]:
    """Items of ``category`` (all when ``None``), sorted by rarity then name."""
    chosen = [i for i in items if category is None or item_category(i.get('effect')).id == category]
    return sorted(chosen, key=lambda i: (get_rarity(i.get('rarity')).order, str(i['name']).lower()))


def validate_item_meta(
    name: str | None = None,
    *,
    rarity: str | None = None,
    stock: int | None = None,
    max_per_user: int | None = None,
    price: int | None = None,
    reply_message: str | None = None,
    image_url: str | None = None,
) -> str | None:
    """Validate the listing columns, returning an error message or ``None`` when valid.

    Only the arguments passed are checked, so an edit can validate just what changed.
    """
    if name is not None and not 1 <= len(name.strip()) <= ITEM_NAME_LIMIT:
        return f'Item names must be 1-{ITEM_NAME_LIMIT} characters.'
    if rarity is not None and rarity not in _RARITY_BY_ID:
        return f'Unknown rarity `{rarity}`. Valid: {", ".join(_RARITY_BY_ID)}.'
    if stock is not None and not 0 <= stock <= MAX_ITEM_STOCK:
        return f'Stock must be between 0 and {MAX_ITEM_STOCK:,}.'
    if max_per_user is not None and max_per_user < 1:
        return 'The per-member limit must be at least 1.'
    if price is not None and price < 1:
        return 'The price must be at least 1.'
    if reply_message is not None and len(reply_message) > REPLY_LIMIT:
        return f'The reply message can be at most {REPLY_LIMIT} characters.'
    if image_url is not None:
        if not image_url.startswith('https://') or len(image_url) > IMAGE_URL_LIMIT or any(c.isspace() for c in image_url):
            return f'The image must be an https:// link of at most {IMAGE_URL_LIMIT} characters.'
        if any(host in image_url for host in _EXPIRING_HOSTS):
            return 'Discord attachment links expire after a day — host the image somewhere permanent.'
    return None


def item_expired(item: Mapping[str, Any], now: datetime.datetime) -> bool:
    """Whether the item's ``available_until`` (naive UTC) has passed at ``now`` (naive UTC)."""
    until = item.get('available_until')
    return until is not None and until <= now


def daily_sale(guild_id: int, day: datetime.date, item_ids: Sequence[int]) -> dict[int, int]:
    """The day's deals: up to :data:`SALE_COUNT` items, each ``item_id -> percent off``.

    Seeded by ``(guild_id, day)`` like the daily quest board, so every process and
    every restart agrees without persisting anything.
    """
    rng = random.Random(f'{guild_id}:{day.toordinal()}')
    chosen = rng.sample(sorted(item_ids), k=min(SALE_COUNT, len(item_ids)))
    return {item_id: rng.randint(SALE_MIN, SALE_MAX) for item_id in chosen}


def sale_price(price: int, percent: int) -> int:
    """``price`` less ``percent`` percent, rounded, never below 1."""
    return max(round(price * (100 - percent) / 100), 1)


class PurchaseStatus(enum.Enum):
    """The result of an atomic purchase attempt (see ``EconomyRepository.buy_item``)."""

    ok = 'ok'
    missing = 'missing'
    sold_out = 'sold_out'
    limit = 'limit'
    funds = 'funds'
    expired = 'expired'


def purchase_error(
    status: PurchaseStatus, *, name: str, stock: int | None = None, limit: int | None = None,
    owned: int = 0, cost: int = 0, cash: int = 0,
) -> str | None:
    """The user-facing reason a purchase failed (``None`` for :attr:`PurchaseStatus.ok`)."""
    if status is PurchaseStatus.missing:
        return f'**{name}** is no longer in the shop.'
    if status is PurchaseStatus.expired:
        return f'**{name}** is no longer on sale.'
    if status is PurchaseStatus.sold_out:
        return f'**{name}** is sold out.' if not stock else f'Only **{stock}** of **{name}** left in stock.'
    if status is PurchaseStatus.limit:
        room = max((limit or 0) - owned, 0)
        return f'You can hold at most **{limit}× {name}** — you have {owned}, room for {room} more.'
    if status is PurchaseStatus.funds:
        return f'You need **{cost:,}** cash but only have **{cash:,}**.'
    return None
