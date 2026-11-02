"""Economy/leveling response models keep their pre-typing wire shape (FastAPI drops undeclared keys)."""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.internal_api.routers import economy, leveling

SHOP_ITEM = {
    'id': 3, 'name': 'Sword', 'description': 'sharp', 'price': 500, 'effect': 'none', 'effect_value': None,
    'duration_minutes': None, 'emoji': None, 'rarity': 'common', 'category': 'other', 'stock': 4, 'max_per_user': None,
    'required_role_id': '12', 'available_until': '2026-01-01T00:00:00', 'remove_role_id': None, 'reply_message': None,
    'image_url': None, 'builtin_key': None, 'for_sale': True,
}
CONFIG = {
    'enabled': True, 'configured': True, 'level_up_channel': 5, 'level_up_message': 'gg',
    'special_level_up_messages': {'5': 'x'}, 'blacklisted_roles': [1], 'blacklisted_channels': [2],
    'blacklisted_users': [3], 'level_roles': {'9': 5}, 'multiplier_roles': {'9': 1.5},
    'multiplier_channels': {'8': 2.0}, 'role_stack': False, 'voice_enabled': True, 'delete_after_leave': False,
    'factor': 1.0, 'base': 100, 'min_gain': 8, 'max_gain': 15, 'cooldown_per': 40,
    'card_style': {'theme': 'dark'}, 'card_locked': False,
    'card_options': {'theme': [{'value': 'dark', 'label': 'Dark'}]},
    'level_up_variables': [{'name': 'user', 'description': 'The member'}],
}
SETTINGS = {
    'payout_multiplier': 1.5, 'rob_enabled': True, 'daily_base': 250, 'max_bet': None,
    'bank_cap': True, 'drops_enabled': False,
}


@pytest.mark.parametrize(('model', 'sample'), [
    (economy.ShopItem, SHOP_ITEM),
    (economy.LotteryInfo, {'ticket_price': 5, 'jackpot': 50, 'channel_id': '7', 'ends_at': None}),
    (economy.EconomyInfo, {'items': [SHOP_ITEM], 'lottery': None, 'settings': SETTINGS}),
    (economy.BalancesResponse, {'entries': [
        {'user_id': '1', 'username': 'a', 'avatar_url': None, 'cash': 1, 'bank': 2, 'total': 3}], 'total': 1}),
    (economy.MemberBalance, {'user_id': '1', 'cash': 1, 'bank': 2, 'total': 3}),
    (economy.MemberInventory, {'user_id': '1', 'items': [
        {'item_id': 3, 'name': 'Sword', 'quantity': 2, 'emoji': None, 'rarity': 'common', 'effect': 'none'}]}),
    (economy.InventoryChange, {'ok': True, 'item_id': 3, 'quantity': 2}),
    (leveling.LevelingConfig, CONFIG),
    (leveling.LeaderboardResponse, {'entries': [
        {'user_id': '1', 'username': 'a', 'avatar_url': None, 'level': 2, 'xp': 3, 'total_xp': 40}], 'total': 1}),
    (leveling.XpHistoryResponse, {'points': [{'day': '2026-01-01', 'total_xp': 5, 'gainers': 2}], 'days': 30}),
    (leveling.MemberLevel, {'user_id': '1', 'level': 2, 'xp': 3, 'messages': 4, 'rank': 1}),
])
def test_models_round_trip(model, sample) -> None:
    assert model.model_validate(sample).model_dump(mode='json', by_alias=True) == sample


async def test_unconfigured_leveling_config_stays_two_keys() -> None:
    bot = MagicMock()
    bot.db.leveling.get_guild_config_record = AsyncMock(return_value=None)
    out = await leveling.get_leveling_config(MagicMock(id=1), bot)
    assert out.model_dump(mode='json', exclude_unset=True) == {'enabled': False, 'configured': False}


async def test_economy_handler_shape() -> None:
    row = {'id': 3, 'name': 'Sword', 'price': 500, 'available_until': datetime.datetime(2026, 1, 1)}
    eco = MagicMock()
    eco.ensure_builtins = AsyncMock()
    eco.get_items = AsyncMock(return_value=[row])
    eco.get_lottery = AsyncMock(return_value=None)
    eco.get_settings = AsyncMock(return_value=None)
    bot = MagicMock()
    bot.db.economy = eco
    body = (await economy.get_economy(MagicMock(id=1), bot)).model_dump(mode='json')
    assert body['items'][0]['available_until'] == '2026-01-01T00:00:00'
    assert body['items'][0]['rarity'] == 'common' and body['settings']['daily_base'] > 0
