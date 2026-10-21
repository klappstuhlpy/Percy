"""Dashboard test sends: where the embed goes, and the plain refusals."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from fastapi import HTTPException

from app.internal_api.routers import guild as api


def _env(**cfg: Any) -> tuple[MagicMock, MagicMock, MagicMock]:
    channel = MagicMock(spec=discord.TextChannel)
    channel.name = 'mod-log'
    channel.send = AsyncMock()
    guild = MagicMock()
    guild.id = 1
    guild.get_channel = MagicMock(return_value=channel)
    config = SimpleNamespace(
        mod_log_channel_id=cfg.get('mod', 5), audit_log_webhook=cfg.get('audit'), alert_webhook=None
    )
    bot = MagicMock()
    bot.db.get_guild_config = AsyncMock(return_value=config)
    return guild, bot, channel


async def test_unknown_feature_is_rejected() -> None:
    guild, bot, _ = _env()
    with pytest.raises(HTTPException) as exc:
        await api.send_test_message(guild, bot, api.TestMessageBody(feature='nope'))
    assert exc.value.status_code == 400


async def test_channel_feature_sends_without_pings() -> None:
    guild, bot, channel = _env()
    result = await api.send_test_message(guild, bot, api.TestMessageBody(feature='mod_log'))
    assert result['message'] == 'Test message sent to #mod-log.'
    kwargs = channel.send.await_args.kwargs
    assert kwargs['embed'].title == 'Test message from the dashboard'
    assert not kwargs['allowed_mentions'].everyone


async def test_unset_channel_and_missing_webhook_are_plain_400s() -> None:
    guild, bot, _ = _env(mod=None)
    for feature in ('mod_log', 'audit_log'):
        with pytest.raises(HTTPException) as exc:
            await api.send_test_message(guild, bot, api.TestMessageBody(feature=feature))
        assert exc.value.status_code == 400 and 'No channel is set' in exc.value.detail


async def test_forbidden_becomes_400() -> None:
    guild, bot, channel = _env()
    channel.send.side_effect = discord.Forbidden(MagicMock(status=403), 'no')
    with pytest.raises(HTTPException) as exc:
        await api.send_test_message(guild, bot, api.TestMessageBody(feature='mod_log'))
    assert 'cannot post in #mod-log' in exc.value.detail


async def test_audit_log_goes_through_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    sender = AsyncMock()
    monkeypatch.setattr(api, 'send_webhook', sender)
    webhook = MagicMock()
    guild, bot, _ = _env(audit=webhook)
    await api.send_test_message(guild, bot, api.TestMessageBody(feature='audit_log'))
    assert sender.await_args.args[0] is webhook
