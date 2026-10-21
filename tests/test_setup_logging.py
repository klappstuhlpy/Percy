"""One-step logging setup: validation before Discord is touched, and the config it writes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from fastapi import HTTPException

from app.internal_api.routers import guild as api


def _channel(**perms: bool) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 42
    channel.name = 'logs'
    channel.mention = '<#42>'
    channel.permissions_for = MagicMock(return_value=discord.Permissions(**perms))
    return channel


def _setup(channel: MagicMock) -> tuple[MagicMock, MagicMock]:
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=channel)
    config = MagicMock()
    config.flags = SimpleNamespace(value=0)
    config.update = AsyncMock()
    bot = MagicMock()
    bot.db.get_guild_config = AsyncMock(return_value=config)
    return guild, bot


async def test_unknown_log_is_rejected() -> None:
    guild, bot = _setup(_channel())
    with pytest.raises(HTTPException) as exc:
        await api.setup_logging(guild, bot, api.SetupLoggingBody(channel_id=42, logs=['everything']))
    assert exc.value.status_code == 400


async def test_existing_channel_without_permissions_names_them() -> None:
    guild, bot = _setup(_channel(view_channel=True))
    with pytest.raises(HTTPException) as exc:
        await api.setup_logging(guild, bot, api.SetupLoggingBody(channel_id=42, logs=['mod']))
    assert 'Send Messages' in exc.value.detail and '#logs' in exc.value.detail
    (await bot.db.get_guild_config(1)).update.assert_not_awaited()


async def test_points_every_chosen_log_at_the_channel() -> None:
    channel = _channel(view_channel=True, send_messages=True, embed_links=True, manage_webhooks=True)
    guild, bot = _setup(channel)
    result = await api.setup_logging(guild, bot, api.SetupLoggingBody(channel_id=42, logs=['mod', 'message']))
    config = await bot.db.get_guild_config(1)
    config.update.assert_awaited_once_with(mod_log_channel_id=42, message_log_channel_id=42, flags=0)
    assert result['message'] == '2 logs now go to #logs.'
