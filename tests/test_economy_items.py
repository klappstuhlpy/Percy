"""Tests for the item catalogue rules and the CV2 item browser (rendered against a fake database)."""

from __future__ import annotations

import datetime
import random
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from app.cogs.economy import item_ui
from app.cogs.economy.item_ui import PAGE_SIZE, ItemBrowser
from app.services.economy import (
    CATALOGUE,
    CATEGORIES,
    ITEM_EFFECTS,
    RARITIES,
    PurchaseStatus,
    TradeOffer,
    builtin_error,
    daily_sale,
    filter_items,
    get_rarity,
    item_category,
    item_emoji,
    item_expired,
    name_reserved_error,
    purchase_error,
    roll_drop,
    sale_price,
    sell_price,
    summarize_offer,
    tool_for_activity,
    trade_error,
    validate_item_meta,
)

# -- pure rules ---------------------------------------------------------------


def test_every_effect_has_exactly_one_category() -> None:
    for effect in ITEM_EFFECTS:
        owners = [c for c in CATEGORIES if effect in c.effects]
        assert len(owners) == 1, effect


def test_unknown_effect_and_rarity_fall_back() -> None:
    assert item_category('teleport').id == 'collectible'
    assert item_category(None).id == 'collectible'
    assert get_rarity('mythic') is RARITIES[0]
    assert get_rarity(None) is RARITIES[0]


def test_item_emoji_prefers_own_then_category() -> None:
    assert item_emoji({'emoji': '\N{RED APPLE}', 'effect': 'cash'}) == '\N{RED APPLE}'
    assert item_emoji({'emoji': None, 'effect': 'xp_boost'}) == item_category('xp_boost').emoji


def test_filter_items_sorts_by_rarity_then_name() -> None:
    items = [
        {'name': 'b', 'effect': 'none', 'rarity': 'legendary'},
        {'name': 'C', 'effect': 'none', 'rarity': 'common'},
        {'name': 'a', 'effect': 'none', 'rarity': 'common'},
        {'name': 'boost', 'effect': 'xp_boost', 'rarity': 'rare'},
    ]
    assert [i['name'] for i in filter_items(items)] == ['a', 'C', 'boost', 'b']
    assert [i['name'] for i in filter_items(items, 'boost')] == ['boost']


@pytest.mark.parametrize(
    ('kwargs', 'ok'),
    [
        ({'name': 'Cookie'}, True),
        ({'name': '   '}, False),
        ({'name': 'x' * 65}, False),
        ({'rarity': 'epic'}, True),
        ({'rarity': 'mythic'}, False),
        ({'stock': 0}, True),
        ({'stock': -1}, False),
        ({'max_per_user': 0}, False),
        ({'price': 0}, False),
        ({}, True),
    ],
)
def test_validate_item_meta(kwargs: dict[str, Any], ok: bool) -> None:
    assert (validate_item_meta(**kwargs) is None) is ok


def test_purchase_error_messages() -> None:
    assert purchase_error(PurchaseStatus.ok, name='x') is None
    assert 'sold out' in (purchase_error(PurchaseStatus.sold_out, name='x', stock=0) or '')
    assert 'Only **2**' in (purchase_error(PurchaseStatus.sold_out, name='x', stock=2) or '')
    assert 'room for 1 more' in (purchase_error(PurchaseStatus.limit, name='x', limit=3, owned=2) or '')
    assert '1,000' in (purchase_error(PurchaseStatus.funds, name='x', cost=1000, cash=5) or '')


# -- browser rendering ----------------------------------------------------------


def _item(item_id: int, **extra: Any) -> dict[str, Any]:
    row = {
        'id': item_id, 'name': f'Item {item_id}', 'description': 'A thing.', 'price': 100 * item_id,
        'effect': 'none', 'effect_value': None, 'duration_minutes': None, 'emoji': None,
        'rarity': 'common', 'stock': None, 'max_per_user': None, 'required_role_id': None,
        'available_until': None, 'remove_role_id': None, 'reply_message': None,
    }
    row.update(extra)
    return row


class _FakeEconomyRepo:
    def __init__(self, items: list[dict[str, Any]], owned: dict[int, int]) -> None:
        self.items, self.owned = items, owned

    async def get_items(self, _guild_id: int, *, for_sale_only: bool = False) -> list[dict[str, Any]]:
        return [i for i in self.items if i.get('for_sale', True) or not for_sale_only]

    async def ensure_builtins(self, *_a: Any) -> None:
        return None

    async def get_item_by_id(self, _guild_id: int, item_id: int) -> dict[str, Any] | None:
        return next((i for i in self.items if i['id'] == item_id), None)

    async def get_inventory(self, _user_id: int, _guild_id: int) -> list[dict[str, Any]]:
        return [
            {**{k: v for k, v in i.items() if k != 'id'}, 'item_id': i['id'], 'quantity': self.owned[i['id']]}
            for i in self.items if self.owned.get(i['id'])
        ]

    async def get_quantity(self, _user_id: int, _guild_id: int, item_id: int) -> int:
        return self.owned.get(item_id, 0)

    async def item_stats(self, _guild_id: int, item_id: int) -> dict[str, Any]:
        held = sum(q for i, q in self.owned.items() if i == item_id)
        return getattr(self, 'stats', None) or {
            'held': held, 'holders': int(held > 0), 'listings': 0, 'listed': 0, 'lowest': None, 'average': None,
        }

    async def get_active_boosts(self, _user_id: int, _guild_id: int) -> list[Any]:
        return []


def _browser(monkeypatch: pytest.MonkeyPatch, items: list[dict[str, Any]], owned: dict[int, int], **kwargs: Any) -> ItemBrowser:
    monkeypatch.setattr(item_ui, 'get_asset_url', lambda _obj: 'https://cdn.discordapp.com/embed/avatars/0.png')
    repo = _FakeEconomyRepo(items, owned)

    async def get_user_balance(_user_id: int, _guild_id: int) -> Any:
        return SimpleNamespace(cash=1_000, bank=0, total=1_000)

    bot = SimpleNamespace(db=SimpleNamespace(economy=repo, get_user_balance=get_user_balance))
    cog = SimpleNamespace(bot=bot, _effect_line=lambda _guild, _item: None)
    viewer = SimpleNamespace(id=1, display_name='Viewer', guild=SimpleNamespace(id=10, name='Guild'))
    return ItemBrowser(cog, viewer, **kwargs)  # type: ignore[arg-type]


def _count(components: list[dict[str, Any]]) -> int:
    total = 0
    for c in components:
        total += 1 + _count(c.get('components', []))
        if 'accessory' in c:
            total += 1
    return total


def _text(view: discord.ui.LayoutView) -> str:
    return '\n'.join(
        item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)
    ) + '\n'.join(
        child.content for item in view.walk_children() if isinstance(item, discord.ui.Section)
        for child in item.children if isinstance(child, discord.ui.TextDisplay)
    )


async def test_shop_list_stays_within_cv2_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [_item(i, rarity=RARITIES[i % len(RARITIES)].id, stock=i % 3) for i in range(1, 23)]
    browser = _browser(monkeypatch, items, {1: 2}, mode='shop')
    await browser.build()
    payload = browser.to_components()
    assert _count(payload) <= 40
    assert sum(len(i.content) for i in browser.walk_children() if isinstance(i, discord.ui.TextDisplay)) <= 4000
    buttons = [b for b in browser.walk_children() if isinstance(b, discord.ui.Button) and b.label == 'View']
    assert len(buttons) == PAGE_SIZE
    assert 'Page 1/5' in [b.label for b in browser.walk_children() if isinstance(b, discord.ui.Button)]


async def test_shop_detail_shows_listing_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [_item(7, stock=0, max_per_user=2, required_role_id=555, rarity='epic', emoji='<:coin:123456789012345678>')]
    browser = _browser(monkeypatch, items, {}, mode='shop', item_id=7)
    await browser.build()
    text = _text(browser)
    assert 'Sold out' in text and 'Limit: **2**' in text and '<@&555>' in text and 'Epic' in text
    labels = {b.label for b in browser.walk_children() if isinstance(b, discord.ui.Button)}
    assert {'Buy 1', 'Buy…', 'Back'} <= labels
    # A custom emoji becomes the detail card's thumbnail.
    assert any(isinstance(i, discord.ui.Thumbnail) for i in browser.walk_children())


async def test_detail_shows_image_and_market(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [_item(4, image_url='https://i.imgur.com/a.png', emoji='<:coin:123456789012345678>')]
    browser = _browser(monkeypatch, items, {4: 2}, mode='shop', item_id=4)
    browser.cog.bot.db.economy.stats = {  # type: ignore[attr-defined]
        'held': 9, 'holders': 3, 'listings': 2, 'listed': 5, 'lowest': 80, 'average': 120,
    }
    await browser.build()
    thumbs = [i for i in browser.walk_children() if isinstance(i, discord.ui.Thumbnail)]
    assert [t.media.url for t in thumbs] == ['https://i.imgur.com/a.png']  # the image beats the emoji
    text = _text(browser)
    assert '×9' in text and '**3** members' in text and 'Active listings: **2**' in text and '120' in text


async def test_own_inventory_offers_actions_others_are_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [_item(1, effect='cash', effect_value=50)]
    own = _browser(monkeypatch, items, {1: 3}, mode='inventory', item_id=1)
    await own.build()
    labels = {b.label for b in own.walk_children() if isinstance(b, discord.ui.Button)}
    assert {'Use', 'Sell 1', 'Sell all (3)', 'Gift…'} <= labels

    other = _browser(monkeypatch, items, {1: 3}, mode='inventory', item_id=1)
    other.target = SimpleNamespace(id=2, display_name='Other')  # type: ignore[assignment]
    await other.build()
    labels = {b.label for b in other.walk_children() if isinstance(b, discord.ui.Button)}
    assert labels == {'Back'}


async def test_vanished_item_falls_back_to_list(monkeypatch: pytest.MonkeyPatch) -> None:
    browser = _browser(monkeypatch, [_item(1)], {}, mode='inventory', item_id=1)
    await browser.build()
    assert browser.item_id is None
    assert 'No items yet' in _text(browser)


# -- hub pages and bulk use ------------------------------------------------------


def _hub(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> Any:
    from app.cogs.economy import ui

    monkeypatch.setattr(ui, 'get_asset_url', lambda _obj: 'https://cdn.discordapp.com/embed/avatars/0.png')

    class Repo:
        async def get_active_boosts(self, *_a: Any) -> list[Any]:
            return []

        async def get_prestige(self, *_a: Any) -> int:
            return 0

        async def inventory_value(self, *_a: Any) -> int:
            return 0

    async def get_user_balance(_user_id: int, _guild_id: int) -> Any:
        return SimpleNamespace(cash=5, bank=0, total=5)

    async def settings(_guild_id: int) -> Any:
        return SimpleNamespace(payout_multiplier=1.0)

    db = SimpleNamespace(economy=Repo(), get_user_balance=get_user_balance)
    cog = SimpleNamespace(bot=SimpleNamespace(db=db), _settings=settings)
    guild = SimpleNamespace(id=10, name='Guild')
    author = SimpleNamespace(id=1, display_name='Me', guild=guild)
    ctx = SimpleNamespace(author=author, guild=guild, db=db)
    return ui.EconomyHub(cog, ctx, **kwargs)  # type: ignore[arg-type]


async def test_hub_opens_on_requested_page(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = _hub(monkeypatch, page='perks')
    await hub.build()
    assert 'Perks & Boosts' in _text(hub)
    assert any(isinstance(i, discord.ui.Select) for i in hub.walk_children())
    assert _hub(monkeypatch, page='nonsense').page == 'overview'


async def test_hub_for_another_member_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    other = SimpleNamespace(id=2, display_name='Other')
    hub = _hub(monkeypatch, page='perks', target=other)
    await hub.build()
    assert not any(isinstance(i, discord.ui.Select) for i in hub.walk_children())


class _UseRepo:
    def __init__(self, effect: str, owned: int) -> None:
        self.item = _item(1, effect=effect, effect_value=50, duration_minutes=30)
        self.owned = owned

    async def get_item_by_id(self, _guild_id: int, _item_id: int) -> dict[str, Any]:
        return self.item

    async def consume_item(self, _u: int, _g: int, _i: int, quantity: int = 1) -> int | None:
        if self.owned < quantity:
            return None
        self.owned -= quantity
        return self.owned

    async def get_quantity(self, *_a: Any) -> int:
        return self.owned


async def test_use_quantity_only_for_cash_and_lootbox() -> None:
    from app.cogs.economy.cog import Economy

    added: list[int] = []

    async def add(cash: int) -> None:
        added.append(cash)

    async def get_user_balance(*_a: Any) -> Any:
        return SimpleNamespace(add=add)

    member = SimpleNamespace(id=1, guild=SimpleNamespace(id=10))

    def cog(effect: str, owned: int) -> Any:
        repo = _UseRepo(effect, owned)
        fake = SimpleNamespace(bot=SimpleNamespace(db=SimpleNamespace(economy=repo, get_user_balance=get_user_balance)))
        fake._use_item = lambda *a: Economy._use_item(fake, *a)  # type: ignore[arg-type]
        return fake

    ok, message = await Economy.use_item_action(cog('xp_boost', 5), member, 1, 3)  # type: ignore[arg-type]
    assert not ok and 'one at a time' in message

    ok, _ = await Economy.use_item_action(cog('cash', 5), member, 1, 3)  # type: ignore[arg-type]
    assert ok and added == [150]

    ok, message = await Economy.use_item_action(cog('cash', 2), member, 1, 3)  # type: ignore[arg-type]
    assert not ok and 'only own **2' in message


# -- daily sale, expiry, listing extras --------------------------------------------


def test_daily_sale_is_deterministic_and_bounded() -> None:
    day = datetime.date(2026, 10, 23)
    ids = list(range(1, 9))
    deals = daily_sale(10, day, ids)
    assert deals == daily_sale(10, day, list(reversed(ids)))
    assert len(deals) == 2 and set(deals) <= set(ids)
    assert all(10 <= percent <= 40 for percent in deals.values())
    assert daily_sale(10, day, [5]).keys() == {5}
    assert daily_sale(10, day, []) == {}
    # Another day eventually picks something else.
    assert any(daily_sale(10, day + datetime.timedelta(days=n), ids) != deals for n in range(1, 10))


def test_sale_price_rounds_and_never_hits_zero() -> None:
    assert sale_price(100, 30) == 70
    assert sale_price(15, 10) == 14
    assert sale_price(1, 40) == 1


def test_expiry_and_message() -> None:
    now = datetime.datetime(2026, 10, 23, 12, 0)
    assert not item_expired({'available_until': None}, now)
    assert not item_expired({}, now)
    assert not item_expired({'available_until': now + datetime.timedelta(seconds=1)}, now)
    assert item_expired({'available_until': now}, now)
    assert 'no longer on sale' in (purchase_error(PurchaseStatus.expired, name='x') or '')
    assert validate_item_meta(reply_message='x' * 501) is not None
    assert validate_item_meta(reply_message='x' * 500) is None


def test_validate_item_image_url() -> None:
    assert validate_item_meta(image_url='https://i.imgur.com/a.png') is None
    assert validate_item_meta(image_url='http://i.imgur.com/a.png') is not None
    assert validate_item_meta(image_url='https://x.com/a b.png') is not None
    assert validate_item_meta(image_url='https://' + 'x' * 500) is not None
    assert 'expire' in (validate_item_meta(image_url='https://cdn.discordapp.com/attachments/1/2/a.png') or '')


async def test_shop_shows_deals_and_disables_expired_buy(monkeypatch: pytest.MonkeyPatch) -> None:
    past = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(days=1)
    items = [_item(1, available_until=past), _item(2)]
    browser = _browser(monkeypatch, items, {}, mode='shop')
    await browser.build()
    assert sorted(browser.deals) == [1, 2]  # only two items exist, so both are discounted
    text = _text(browser)
    assert "Today's deals" in text and '% today' in text and '~~' in text and 'no longer on sale' in text

    detail = _browser(monkeypatch, items, {}, mode='shop', item_id=1)
    await detail.build()
    buy = [b for b in detail.walk_children() if isinstance(b, discord.ui.Button) and b.label in ('Buy 1', 'Buy…')]
    assert buy and all(b.disabled for b in buy)
    assert 'No longer on sale' in _text(detail)


# -- trade ----------------------------------------------------------------------------


def test_trade_helpers() -> None:
    assert trade_error(1, 1, partner_is_bot=False) is not None
    assert trade_error(1, 2, partner_is_bot=True) is not None
    assert trade_error(1, 2, partner_is_bot=False) is None
    offer = TradeOffer()
    assert offer.empty and offer.lines == 0 and summarize_offer(offer, {}) == '*Nothing yet*'
    offer.items[7] = 3
    offer.cash = 50
    assert not offer.empty and offer.lines == 2
    text = summarize_offer(offer, {7: 'Cookie'}, 'CASH')
    assert '×**3** Cookie' in text and text.endswith('CASH')


async def test_trade_card_renders_within_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.cogs.economy.trade_ui import TradeView

    items = [_item(i) for i in range(1, 11)]
    repo = _FakeEconomyRepo(items, {})
    cog = SimpleNamespace(bot=SimpleNamespace(db=SimpleNamespace(economy=repo)))
    guild = SimpleNamespace(id=10)
    a = SimpleNamespace(id=1, guild=guild, mention='<@1>', display_name='A')
    b = SimpleNamespace(id=2, guild=guild, mention='<@2>', display_name='B')
    view = TradeView(cog, a, b)  # type: ignore[arg-type]
    view.offers[1].items = {i: i for i in range(1, 11)}
    view.offers[1].cash = 999
    view.offers[1].confirmed = True
    await view.build()
    assert _count(view.to_components()) <= 40
    assert sum(len(i.content) for i in view.walk_children() if isinstance(i, discord.ui.TextDisplay)) <= 4000
    labels = {b.label for b in view.walk_children() if isinstance(b, discord.ui.Button)}
    assert {'Add item', 'Add cash', 'Remove…', 'Confirm', 'Cancel'} <= labels
    assert 'confirmed' in _text(view)
    view._changed()
    assert not any(o.confirmed for o in view.offers.values())


# -- built-in catalogue (phase 2) ----------------------------------------------------


def test_catalogue_integrity() -> None:
    keys = [i.key for i in CATALOGUE]
    assert len(keys) == len(set(keys))
    assert len({i.name.lower() for i in CATALOGUE}) == len(CATALOGUE)
    rarities = {r.id for r in RARITIES}
    for item in CATALOGUE:
        assert item.price > 0 and item.rarity in rarities and item.effect in ITEM_EFFECTS
        if item.for_sale and item.tool_for is None and item.drop_from is None:  # shop consumable or passive item
            continue
        assert (item.tool_for is None) != (item.drop_from is None)  # a tool or a drop, never both
        if item.tool_for:
            assert item.for_sale and (item.durability or 0) > 0
        else:
            assert not item.for_sale and item.durability is None
        assert sell_price(item.price) >= 0
    for activity in ('fish', 'hunt', 'dig'):
        assert tool_for_activity(activity) is not None


def test_roll_drop_is_seeded_and_bounded() -> None:
    first = [roll_drop('fish', random.Random(7)) for _ in range(3)]
    assert first == [roll_drop('fish', random.Random(7)) for _ in range(3)]
    rng = random.Random(1)
    rolls = [roll_drop('dig', rng) for _ in range(2_000)]
    hits = [r for r in rolls if r is not None]
    assert all(r.drop_from == 'dig' for r in hits)
    assert 0.28 < len(hits) / len(rolls) < 0.42
    assert roll_drop('beg') is None


async def test_shop_list_hides_non_for_sale(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [_item(1, name='Rod', for_sale=True), _item(2, name='Sardine', for_sale=False)]
    shop = _browser(monkeypatch, items, {2: 1}, mode='shop')
    await shop.build()
    assert 'Rod' in _text(shop) and 'Sardine' not in _text(shop)
    inv = _browser(monkeypatch, items, {2: 1}, mode='inventory')
    await inv.build()
    assert 'Sardine' in _text(inv)


async def test_tool_detail_shows_durability_and_badge(monkeypatch: pytest.MonkeyPatch) -> None:
    rod = _item(1, name='Fishing Rod', builtin_key='fishing_rod', for_sale=True)
    browser = _browser(monkeypatch, [rod], {1: 1}, mode='inventory', item_id=1)
    browser.cog.bot.db.economy.get_inventory = lambda *_a: _async([{**rod, 'item_id': 1, 'quantity': 1, 'durability': 7}])
    await browser.build()
    text = _text(browser)
    assert 'Percy item' in text and 'Uses left on current: **7/25**' in text


async def _async(value: Any) -> Any:
    return value


def test_builtin_refusals() -> None:
    assert builtin_error({'name': 'Shovel', 'builtin_key': 'shovel'}, 'edit') == (
        '**Shovel** is a built-in Percy item and cannot be edited. Its name, price and drops are managed by Percy.'
    )
    assert 'removed' in (builtin_error({'name': 'Shovel', 'builtin_key': 'shovel'}, 'remove') or '')
    assert builtin_error({'name': 'Cookie', 'builtin_key': None}, 'edit') is None
    assert name_reserved_error('  fishing ROD ') is not None
    assert name_reserved_error('Cookie') is None
