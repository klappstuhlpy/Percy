"""Lockdown bookkeeping: a channel's saved overwrites are deleted only once its restore succeeded."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from fastapi import HTTPException

from app.cogs.moderation.lockdown import end_lockdown, lock_channels
from app.internal_api.routers.content import _channel_ids


def _http_error() -> discord.HTTPException:
    return discord.HTTPException(MagicMock(status=403, reason="Forbidden"), "Missing Permissions")


def _channel(channel_id: int, *, fails: bool = False, spec: type = discord.TextChannel) -> MagicMock:
    channel = MagicMock(spec=spec)
    channel.id = channel_id
    channel.mention = f"<#{channel_id}>"
    channel.overwrites_for.return_value = discord.PermissionOverwrite()
    channel.set_permissions = AsyncMock(side_effect=_http_error() if fails else None)
    return channel


def _bot(rows: list[tuple[int, int, int]]) -> MagicMock:
    bot = MagicMock()
    bot.db.moderation.get_lockdowns = AsyncMock(return_value=rows)
    bot.db.moderation.remove_lockdowns = AsyncMock()
    bot.db.moderation.add_lockdowns = AsyncMock()
    bot.timers.drop_lockdown_channels = AsyncMock()
    return bot


async def test_end_lockdown_keeps_rows_of_failed_channels() -> None:
    ok, failing, uncached = _channel(1), _channel(2, fails=True), _channel(3)
    cached = {1: ok, 2: failing}
    guild = MagicMock(id=99)
    guild.get_channel = cached.get
    # 3 is only reachable through the fetch fallback; 4 was deleted.
    guild.fetch_channels = AsyncMock(return_value=[uncached])
    bot = _bot([(1, 0, 0), (2, 0, 0), (3, 0, 0), (4, 0, 0)])

    failures = await end_lockdown(bot, guild)

    assert failures == [failing]
    uncached.set_permissions.assert_awaited_once()
    guild.fetch_channels.assert_awaited_once()
    bot.db.moderation.remove_lockdowns.assert_awaited_once_with(99, [1, 3, 4])
    # Restored channels leave their timers; the failed one keeps its timer for a retry.
    bot.timers.drop_lockdown_channels.assert_awaited_once_with(99, [1, 3, 4])


async def test_end_lockdown_deletes_nothing_when_every_restore_fails() -> None:
    failing = _channel(2, fails=True)
    guild = MagicMock(id=99)
    guild.get_channel = {2: failing}.get
    bot = _bot([(2, 0, 0)])

    assert await end_lockdown(bot, guild) == [failing]
    bot.db.moderation.remove_lockdowns.assert_not_awaited()
    bot.timers.drop_lockdown_channels.assert_not_awaited()


async def test_lock_channels_denies_connect_and_speak_on_voice() -> None:
    voice, text = _channel(1, spec=discord.VoiceChannel), _channel(2)
    guild = MagicMock(id=99)
    bot = _bot([])

    await lock_channels(bot, guild, [voice, text], reason="test")

    voice_ow = voice.set_permissions.await_args.kwargs["overwrite"]
    text_ow = text.set_permissions.await_args.kwargs["overwrite"]
    assert voice_ow.connect is False and voice_ow.speak is False and voice_ow.send_messages is False
    assert text_ow.connect is None and text_ow.send_messages is False


def test_channel_ids_rejects_bad_input_with_400() -> None:
    assert _channel_ids(["1", 2]) == [1, 2]
    for raw in ([], ["abc"], ["-1"], [str(2**64)]):
        with pytest.raises(HTTPException) as exc:
            _channel_ids(raw)
        assert exc.value.status_code == 400
