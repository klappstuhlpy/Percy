"""Raid protection bans only the members who spammed, once, and tells the moderators either way."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from app.cogs.moderation.antispam import SpamChecker, check_raid, mention_spam_ban, raid_ban_alert
from app.cogs.moderation.models import SpamCheckerResult, SpammerSequence
from app.utils.helpers import ListedRateLimit


class _User:
    def __init__(self, user_id: int, name: str) -> None:
        self.id = user_id
        self.name = name

    def __str__(self) -> str:
        return self.name


def _user(user_id: int, name: str) -> _User:
    return _User(user_id, name)


def _config(*, raid: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        flags=SimpleNamespace(raid=raid), send_alert=AsyncMock(), alert_missing_permission=AsyncMock(), bot=MagicMock()
    )


def _guild(*, ban: AsyncMock | None = None, can_ban: bool = True) -> MagicMock:
    guild = MagicMock(ban=ban or AsyncMock())
    guild.me.guild_permissions.ban_members = can_ban
    return guild


def _checker(result: SpamCheckerResult | None) -> MagicMock:
    return MagicMock(is_spamming=MagicMock(return_value=result), claim=lambda users: list(users))


def test_alert_names_users_channel_and_reason() -> None:
    text = "\n".join(raid_ban_alert([_user(1, "spam#1"), _user(2, "spam#2")], "Auto-ban for spamming", 42))

    assert "**spam#1** (ID: `1`)" in text
    assert "**spam#2** (ID: `2`)" in text
    assert "<#42>" in text
    assert "Auto-ban for spamming" in text


async def test_ban_sends_alert_without_message_content() -> None:
    config = _config()
    guild = _guild()
    member = _user(7, "raider")
    message = SimpleNamespace(channel=SimpleNamespace(id=99), content="FREE NITRO discord.gg/xyz")
    checker = _checker(SpamCheckerResult.spammer())

    await check_raid(checker, config, guild, member, message)

    guild.ban.assert_awaited_once()
    config.send_alert.assert_awaited_once()
    alert = config.send_alert.await_args.args[0]
    assert "raider" in alert
    assert "<#99>" in alert
    assert "FREE NITRO" not in alert


async def test_failed_ban_alerts_the_moderators() -> None:
    config = _config()
    response = MagicMock(status=403, reason="Forbidden")
    guild = _guild(ban=AsyncMock(side_effect=discord.Forbidden(response, "Missing Permissions")))
    message = SimpleNamespace(channel=SimpleNamespace(id=99), content="spam")

    await check_raid(_checker(SpamCheckerResult.spammer()), config, guild, _user(7, "raider"), message)

    config.send_alert.assert_awaited_once()
    alert = config.send_alert.await_args.args[0]
    assert "could **not** ban" in alert
    assert "raider" in alert
    assert config.send_alert.await_args.kwargs["force"] is True


def _real_checker(result: SpamCheckerResult) -> SpamChecker:
    checker = SpamChecker()
    checker.is_spamming = MagicMock(return_value=result)  # type: ignore[method-assign]
    return checker


async def test_missing_ban_permission_is_reported_once_per_raid() -> None:
    config = _config()
    guild = _guild(can_ban=False)
    message = SimpleNamespace(channel=SimpleNamespace(id=99), content="spam")
    checker = _real_checker(SpamCheckerResult.spammer())

    for raider_id in range(5):  # five different raiders trip the limiters
        await check_raid(checker, config, guild, _user(raider_id, "raider"), message)  # type: ignore[arg-type]

    guild.ban.assert_not_awaited()
    config.alert_missing_permission.assert_awaited_once()


def _pinger(*, can_ban: bool = True, ban: AsyncMock | None = None) -> MagicMock:
    member = MagicMock(id=7, ban=ban or AsyncMock(), top_role=1)
    member.__str__.return_value = "pinger"
    member.guild.id = 1
    member.guild.me.top_role = 5
    member.guild.me.guild_permissions.ban_members = can_ban
    return member


async def test_unbannable_mention_spammer_alerts_once_and_forced() -> None:
    config = _config()
    response = MagicMock(status=403, reason="Forbidden")
    member = _pinger(ban=AsyncMock(side_effect=discord.Forbidden(response, "Missing Permissions")))
    checker = SpamChecker()

    for _ in range(5):  # the member keeps pinging; every message trips the mention check
        await mention_spam_ban(checker, config, member, 6, multiple=True)  # type: ignore[arg-type]

    member.ban.assert_awaited_once()
    config.send_alert.assert_awaited_once()
    assert "Could not ban **pinger**" in config.send_alert.await_args.args[0]
    assert config.send_alert.await_args.kwargs["force"] is True


async def test_mention_spam_without_ban_permission_reports_missing_permission() -> None:
    config = _config()
    member = _pinger(can_ban=False)

    await mention_spam_ban(SpamChecker(), config, member, 6)  # type: ignore[arg-type]

    member.ban.assert_not_awaited()
    config.alert_missing_permission.assert_awaited_once()


async def test_member_above_percy_is_reported_not_banned() -> None:
    config = _config()
    guild = _guild()
    guild.me.top_role = 5
    raider = _user(7, "raider")
    raider.top_role = 9  # type: ignore[attr-defined]
    message = SimpleNamespace(channel=SimpleNamespace(id=99), content="spam")

    await check_raid(_checker(SpamCheckerResult.spammer()), config, guild, raider, message)

    guild.ban.assert_not_awaited()
    assert "could **not** ban" in config.send_alert.await_args.args[0]


async def test_a_member_is_banned_and_reported_once_per_raid() -> None:
    config = _config()
    guild = _guild()
    checker = SpamChecker()
    checker.is_spamming = MagicMock(return_value=SpamCheckerResult.spammer())  # type: ignore[method-assign]
    message = SimpleNamespace(channel=SimpleNamespace(id=99), content="spam")
    raider = _user(7, "raider")

    for _ in range(5):
        await check_raid(checker, config, guild, raider, message)  # type: ignore[arg-type]

    guild.ban.assert_awaited_once()
    config.send_alert.assert_awaited_once()


def test_large_raid_alert_is_split_under_discords_limit() -> None:
    users = [_user(10**17 + i, f"raider-with-a-long-name-{i}") for i in range(200)]

    pages = raid_ban_alert(users, "Auto-ban for spamming", 42)

    assert len(pages) > 1
    assert all(len(page) < 1990 for page in pages)
    assert sum(page.count("raider-with") for page in pages) == 200


async def test_raid_protection_off_does_nothing() -> None:
    config = _config(raid=False)
    guild = MagicMock(ban=AsyncMock())
    checker = MagicMock()

    await check_raid(checker, config, guild, _user(7, "raider"), SimpleNamespace())

    checker.is_spamming.assert_not_called()
    guild.ban.assert_not_awaited()
    config.send_alert.assert_not_awaited()


START = discord.utils.utcnow() + datetime.timedelta(hours=1)
OLD = START - datetime.timedelta(days=400)


@dataclass(frozen=True)
class _Member:
    id: int
    created_at: datetime.datetime = OLD
    joined_at: datetime.datetime = OLD


def _member(user_id: int) -> _Member:
    return _Member(user_id)


def _message(seconds: float, author: _Member, content: str = "free nitro here") -> SimpleNamespace:
    return SimpleNamespace(
        created_at=START + datetime.timedelta(seconds=seconds),
        content=content,
        guild=object(),
        channel=SimpleNamespace(id=10),
        author=author,
        raw_mentions=[],
    )


def test_identical_messages_from_different_people_ban_nobody() -> None:
    checker = SpamChecker()

    results = [checker.is_spamming(_message(i * 0.5, _member(i))) for i in range(10)]  # type: ignore[arg-type]

    assert results == [None] * 10


def test_identical_content_bans_only_the_member_who_repeated_it() -> None:
    checker = SpamChecker()
    spammer = _member(1)
    authors = [spammer, _member(2), _member(3), spammer, _member(4), _member(5)]

    results = [checker.is_spamming(_message(i * 0.5, a)) for i, a in enumerate(authors)]  # type: ignore[arg-type]

    assert isinstance(results[5], SpammerSequence)
    assert list(results[5].members) == [spammer]


def test_attachment_only_and_short_messages_are_not_compared() -> None:
    checker = SpamChecker()
    author = _member(1)

    empty = [checker.is_spamming(_message(i * 2.0, author, content="")) for i in range(6)]  # type: ignore[arg-type]
    short = [checker.is_spamming(_message(20 + i * 2.0, author, content="gg")) for i in range(6)]  # type: ignore[arg-type]

    assert empty == [None] * 6
    assert short == [None] * 6


def test_join_burst_alerts_once_per_batch() -> None:
    limit = ListedRateLimit(10, 5, key=lambda m: m.joined_at)
    joins = [_Member(i, joined_at=START) for i in range(30)]

    batches = [batch for batch in (limit.is_ratelimited(m) for m in joins) if batch]

    # Before the fix every join after the first trip tripped again, one member per alert.
    assert all(len(batch) > 1 for batch in batches)
    assert sum(len(batch) for batch in batches) <= 30
