"""One-step Sentinel setup: permission refusals before Discord is touched, and the shared paths it reuses."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from fastapi import HTTPException

from app.internal_api.routers import guild as api


def _setup(monkeypatch: pytest.MonkeyPatch, *, problem: str | None = None, **perms: bool) -> SimpleNamespace:
    guild = MagicMock()
    guild.id = 1
    guild.me.guild_permissions = discord.Permissions(**perms)
    guild.create_role = AsyncMock(return_value=SimpleNamespace(id=7))
    guild.create_text_channel = AsyncMock(return_value=SimpleNamespace(id=9))
    guild.get_channel = MagicMock(return_value=SimpleNamespace(name='verify'))
    guild.get_role = MagicMock(return_value=SimpleNamespace(name='Unverified'))

    sentinel = MagicMock(started_at=None, starter_role_id=None, members=set())
    sentinel.enable = AsyncMock()
    config = MagicMock()
    config.flags = SimpleNamespace(sentinel=False, value=0)
    bot = MagicMock()
    bot.db.get_guild_config = AsyncMock(return_value=config)

    fakes = SimpleNamespace(
        guild=guild, bot=bot, sentinel=sentinel,
        apply=AsyncMock(return_value={'permission_sync': True, 'warnings': []}),
        post=AsyncMock(return_value={'ok': True, 'warnings': []}),
        config_updates=AsyncMock(),
    )
    monkeypatch.setattr(api, '_get_or_create_sentinel', AsyncMock(return_value=sentinel))
    monkeypatch.setattr(api, '_check_lockdown_role_unworn', MagicMock())
    monkeypatch.setattr(api, '_check_sentinel_channel', MagicMock())
    monkeypatch.setattr(api, '_check_sentinel_role', MagicMock())
    monkeypatch.setattr(api, '_apply_sentinel_settings', fakes.apply)
    monkeypatch.setattr(api, 'send_sentinel_message', fakes.post)
    monkeypatch.setattr(api, '_apply_config_updates', fakes.config_updates)
    monkeypatch.setattr(api, '_sentinel_start_problem', AsyncMock(return_value=problem))
    return fakes


async def test_needs_manage_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    f = _setup(monkeypatch, manage_channels=True)
    with pytest.raises(HTTPException) as exc:
        await api.setup_sentinel(f.guild, f.bot, api.SetupSentinelBody())
    assert exc.value.status_code == 400 and 'Manage Roles' in exc.value.detail
    f.guild.create_role.assert_not_awaited()


async def test_creating_the_channel_needs_manage_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    f = _setup(monkeypatch, manage_roles=True)
    with pytest.raises(HTTPException) as exc:
        await api.setup_sentinel(f.guild, f.bot, api.SetupSentinelBody())
    assert 'Manage Channels' in exc.value.detail
    f.guild.create_role.assert_not_awaited()


async def test_creates_both_and_reuses_the_shared_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    f = _setup(monkeypatch, manage_roles=True, manage_channels=True)
    result = await api.setup_sentinel(f.guild, f.bot, api.SetupSentinelBody())
    f.apply.assert_awaited_once_with(f.guild, f.bot, {'channel_id': 9, 'role_id': 7})
    assert f.post.await_args.args[2].channel_id == 9
    assert result['started'] is False and '#verify' in result['message']
    f.sentinel.enable.assert_not_awaited()


async def test_start_blocked_reports_what_was_done(monkeypatch: pytest.MonkeyPatch) -> None:
    f = _setup(monkeypatch, problem='the lockdown role no longer exists', manage_roles=True, manage_channels=True)
    with pytest.raises(HTTPException) as exc:
        await api.setup_sentinel(f.guild, f.bot, api.SetupSentinelBody(start=True))
    assert 'Sentinel is set up' in exc.value.detail and 'no longer exists' in exc.value.detail
    f.sentinel.enable.assert_not_awaited()


async def test_start_turns_protection_on_and_enables(monkeypatch: pytest.MonkeyPatch) -> None:
    f = _setup(monkeypatch, manage_roles=True)
    result = await api.setup_sentinel(f.guild, f.bot, api.SetupSentinelBody(channel_id=9, role_id=7, start=True))
    f.guild.create_role.assert_not_awaited()
    f.guild.create_text_channel.assert_not_awaited()
    assert f.config_updates.await_args.args[3] == {'flags': api._FLAG_MAP['sentinel']}
    f.sentinel.enable.assert_awaited_once()
    assert result['started'] is True
