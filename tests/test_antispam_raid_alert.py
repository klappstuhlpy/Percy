"""A raid ban tells the moderators, and the alert never quotes the spam itself."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from app.cogs.moderation.antispam import check_raid, raid_ban_alert
from app.cogs.moderation.models import SpamCheckerResult


class _User:
    def __init__(self, user_id: int, name: str) -> None:
        self.id = user_id
        self.name = name

    def __str__(self) -> str:
        return self.name


def _user(user_id: int, name: str) -> _User:
    return _User(user_id, name)


def _config(*, raid: bool = True) -> SimpleNamespace:
    return SimpleNamespace(flags=SimpleNamespace(raid=raid), send_alert=AsyncMock())


def test_alert_names_users_channel_and_reason() -> None:
    text = raid_ban_alert([_user(1, "spam#1"), _user(2, "spam#2")], "Auto-ban for spamming", 42)

    assert "**spam#1** (ID: `1`)" in text
    assert "**spam#2** (ID: `2`)" in text
    assert "<#42>" in text
    assert "Auto-ban for spamming" in text


async def test_ban_sends_alert_without_message_content() -> None:
    config = _config()
    guild = MagicMock(ban=AsyncMock())
    member = _user(7, "raider")
    message = SimpleNamespace(channel=SimpleNamespace(id=99), content="FREE NITRO discord.gg/xyz")
    checker = MagicMock(is_spamming=MagicMock(return_value=SpamCheckerResult.spammer()))

    await check_raid(checker, config, guild, member, message)

    guild.ban.assert_awaited_once()
    config.send_alert.assert_awaited_once()
    alert = config.send_alert.await_args.args[0]
    assert "raider" in alert
    assert "<#99>" in alert
    assert "FREE NITRO" not in alert


async def test_failed_ban_sends_no_alert() -> None:
    config = _config()
    response = MagicMock(status=403, reason="Forbidden")
    guild = MagicMock(ban=AsyncMock(side_effect=discord.Forbidden(response, "Missing Permissions")))
    message = SimpleNamespace(channel=SimpleNamespace(id=99), content="spam")
    checker = MagicMock(is_spamming=MagicMock(return_value=SpamCheckerResult.spammer()))

    await check_raid(checker, config, guild, _user(7, "raider"), message)

    config.send_alert.assert_not_awaited()


async def test_raid_protection_off_does_nothing() -> None:
    config = _config(raid=False)
    guild = MagicMock(ban=AsyncMock())
    checker = MagicMock()

    await check_raid(checker, config, guild, _user(7, "raider"), SimpleNamespace())

    checker.is_spamming.assert_not_called()
    guild.ban.assert_not_awaited()
    config.send_alert.assert_not_awaited()
