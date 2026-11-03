"""The dashboard command manager's endpoints: sections, explicit allows, custom commands, bulk plonks."""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord.ext import commands
from fastapi import HTTPException

from app.internal_api.routers import content
from app.services.custom_commands.models import CustomCommand, CustomCommandIndex


async def _noop(_ctx: object) -> None: ...


class _Mod:
    qualified_name = 'Moderation'
    help_sections: ClassVar[dict[str, tuple[str, ...]]] = {'Bans': ('ban',)}


class _Config:
    qualified_name = 'Config'


def _cmd(name: str, cog: object) -> commands.Command:
    cmd = commands.Command(_noop, name=name)
    cmd.cog = cog  # type: ignore[assignment]
    return cmd


def _bot(rows: list[dict]) -> SimpleNamespace:
    mod = _Mod()
    cmds = [_cmd('purge', mod), _cmd('ban', mod), _cmd('config', _Config())]
    guilds = SimpleNamespace(
        get_command_config=AsyncMock(return_value=rows),
        get_plonks=AsyncMock(return_value=[]),
        set_command_config=AsyncMock(),
        bulk_add_plonks=AsyncMock(),
        clear_plonks=AsyncMock(),
        add_plonk=AsyncMock(),
    )
    index = CustomCommandIndex.build([CustomCommand(id=1, guild_id=1, name='hello', aliases=('hi',))], [])
    db = SimpleNamespace(
        guilds=guilds,
        get_command_overrides=AsyncMock(return_value={}),
        get_custom_command_index=AsyncMock(return_value=index),
    )
    return SimpleNamespace(
        walk_commands=lambda: iter(cmds), db=db, get_cog=lambda _name: None,
        get_or_fetch_member=AsyncMock(return_value=None),
    )


def _guild() -> SimpleNamespace:
    return SimpleNamespace(
        id=1, get_member=lambda _i: None, get_channel=lambda i: object() if i == 5 else None,
        text_channels=[SimpleNamespace(id=5), SimpleNamespace(id=6)],
    )


async def test_commands_carry_sections_allows_and_custom_commands_but_not_config() -> None:
    rows = [
        {'name': 'ban', 'channel_id': None, 'whitelist': False},
        {'name': 'ban', 'channel_id': 5, 'whitelist': True},
        {'name': 'purge', 'channel_id': 6, 'whitelist': False},
    ]
    out = await content.get_commands(_guild(), _bot(rows))  # type: ignore[arg-type]
    by_name = {c['name']: c for c in out['commands']}

    assert 'config' not in by_name
    assert [c['name'] for c in out['commands']] == ['hello', 'purge', 'ban']  # category, then section order
    assert (by_name['ban']['section'], by_name['ban']['globally_disabled'], by_name['ban']['enabled_in']) == (
        'Bans', True, ['5'])
    assert (by_name['purge']['section'], by_name['purge']['disabled_in']) == ('General', ['6'])
    assert by_name['hello']['custom'] and by_name['hello']['category'] == content.CUSTOM_COMMANDS_CATEGORY


async def test_toggle_allow_writes_a_whitelist_row_and_needs_a_channel() -> None:
    bot = _bot([])
    body = content.ToggleCommandBody(name='ban', enabled=True, channel_id='5', allow=True)
    await content.toggle_command(_guild(), bot, body)  # type: ignore[arg-type]
    bot.db.guilds.set_command_config.assert_awaited_once_with(1, 5, 'ban', whitelist=True)

    with pytest.raises(HTTPException):
        await content.toggle_command(_guild(), bot, content.ToggleCommandBody(name='ban', enabled=True, allow=True))  # type: ignore[arg-type]


async def test_plonk_bulk_actions_and_unknown_entity() -> None:
    bot = _bot([])
    bot.get_cog = lambda _name: SimpleNamespace(is_plonked=MagicMock())
    await content.manage_plonk(_guild(), bot, content.ManagePlonkBody(action='all'))  # type: ignore[arg-type]
    bot.db.guilds.bulk_add_plonks.assert_awaited_once_with(1, [5, 6])
    await content.manage_plonk(_guild(), bot, content.ManagePlonkBody(action='clear'))  # type: ignore[arg-type]
    bot.db.guilds.clear_plonks.assert_awaited_once_with(1)

    with pytest.raises(HTTPException):
        await content.manage_plonk(_guild(), bot, content.ManagePlonkBody(action='add', entity_id='7'))  # type: ignore[arg-type]
    await content.manage_plonk(_guild(), bot, content.ManagePlonkBody(action='add', entity_id='5'))  # type: ignore[arg-type]
    bot.db.guilds.add_plonk.assert_awaited_once_with(1, 5)
