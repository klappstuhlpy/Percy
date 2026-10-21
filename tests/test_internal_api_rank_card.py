"""Rank card endpoints: validation before storage, and a server lock wins over the member."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.internal_api.routers import users as api

GUILD, USER = 10, 20


def _bot(*, guild_default: dict, user_style: dict, locked: bool) -> MagicMock:
    member = SimpleNamespace(id=USER, guild=SimpleNamespace(id=GUILD))
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=member)
    cog = MagicMock()
    cog.card_layers = AsyncMock(return_value=(guild_default, user_style, locked))
    bot = MagicMock()
    bot.get_cog = MagicMock(return_value=cog)
    bot.get_guild = MagicMock(return_value=guild)
    config = MagicMock()
    config.update = AsyncMock()
    bot.db.get_user_config = AsyncMock(return_value=config)
    return bot


async def test_get_reports_lock_and_effective_style() -> None:
    bot = _bot(guild_default={'theme': 'ink'}, user_style={'accent': 'rose'}, locked=True)
    payload = await api.get_rank_card(USER, bot, guild_id=GUILD)
    assert payload['locked'] is True
    assert payload['effective'] == {'theme': 'ink', 'accent': 'coral', 'bar': 'gradient', 'font': 'rubik'}
    assert {o['value'] for o in payload['options']['theme']} >= {'panel', 'paper'}


async def test_put_rejects_unknown_preset_without_writing() -> None:
    bot = _bot(guild_default={}, user_style={}, locked=False)
    with pytest.raises(HTTPException) as exc:
        await api.put_rank_card(USER, bot, api.RankCardBody(style={'theme': 'https://evil.example/bg.png'}))
    assert exc.value.status_code == 400
    config = await bot.db.get_user_config(USER)
    config.update.assert_not_awaited()


async def test_put_stores_cleaned_partial_style() -> None:
    bot = _bot(guild_default={}, user_style={}, locked=False)
    result = await api.put_rank_card(USER, bot, api.RankCardBody(style={'accent': 'mint', 'font': None}))
    assert result['style'] == {'accent': 'mint'}
    (await bot.db.get_user_config(USER)).update.assert_awaited_once_with(rank_card={'accent': 'mint'})
