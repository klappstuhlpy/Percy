"""One-step leveling setup: validation before anything changes, and the config it writes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from fastapi import HTTPException

from app.internal_api.routers import leveling as api


def _channel(**perms: bool) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 42
    channel.name = 'levels'
    channel.permissions_for = MagicMock(return_value=discord.Permissions(**perms))
    return channel


def _setup(channel: MagicMock | None = None, manage_roles: bool = True, record: object = None) -> tuple[MagicMock, MagicMock]:
    guild = MagicMock()
    guild.id = 7
    guild.roles = []
    guild.get_channel = MagicMock(return_value=channel)
    guild.me.guild_permissions = discord.Permissions(manage_roles=manage_roles)
    bot = MagicMock()
    bot.db.leveling.get_guild_config_record = AsyncMock(return_value=record)
    bot.db.leveling.create_guild_config = AsyncMock()
    bot.db.leveling.update_guild_config = AsyncMock()
    return guild, bot


async def test_channel_without_permissions_names_them_and_changes_nothing() -> None:
    guild, bot = _setup(_channel(view_channel=True))
    with pytest.raises(HTTPException) as exc:
        await api.setup_leveling(guild, bot, api.SetupLevelingBody(level_up_channel=42))
    assert exc.value.status_code == 400 and 'Send Messages' in exc.value.detail and 'Attach Files' in exc.value.detail
    bot.db.leveling.update_guild_config.assert_not_awaited()


async def test_preset_roles_need_manage_roles_before_anything_changes() -> None:
    guild, bot = _setup(manage_roles=False)
    with pytest.raises(HTTPException) as exc:
        await api.setup_leveling(guild, bot, api.SetupLevelingBody(level_up_channel=1, preset_roles=True))
    assert exc.value.status_code == 400 and 'Manage Roles' in exc.value.detail
    bot.db.leveling.create_guild_config.assert_not_awaited()
    bot.db.leveling.update_guild_config.assert_not_awaited()


async def test_enables_creates_row_and_invalidates_cache() -> None:
    channel = _channel(view_channel=True, send_messages=True, attach_files=True)
    guild, bot = _setup(channel)
    result = await api.setup_leveling(guild, bot, api.SetupLevelingBody(level_up_channel=42))
    bot.db.leveling.create_guild_config.assert_awaited_once_with(7, True)
    bot.db.leveling.update_guild_config.assert_awaited_once_with(7, {'enabled': True, 'level_up_channel': 42})
    bot.get_cog('Leveling').get_guild_level_config.invalidate.assert_called_once_with(7)
    assert result['ok'] and '#levels' in result['message']


async def test_preset_roles_use_the_shared_helper() -> None:
    guild, bot = _setup(record={'level_roles': {}})
    role = MagicMock()
    role.id = 1
    guild.create_role = AsyncMock(return_value=role)
    result = await api.setup_leveling(guild, bot, api.SetupLevelingBody(level_up_channel=2, preset_roles=True))
    assert guild.create_role.await_count == len(api._PRESET_LEVEL_ROLES)
    assert '12 milestone roles created' in result['message'] and 'DM' in result['message']
