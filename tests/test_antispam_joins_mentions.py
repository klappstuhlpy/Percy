"""Join-spam alerts, mention spam, Sentinel bypass and the ignore list behave as documented."""

from __future__ import annotations

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from fastapi import HTTPException

from app.cogs.moderation.antispam import SpamChecker, check_raid, mention_threshold
from app.cogs.moderation.cog import Moderation
from app.cogs.moderation.models import SpammerSequence
from app.database.base import Sentinel
from app.internal_api.routers.guild import _build_config_updates, _check_mention_count

START = discord.utils.utcnow()
GUILD = SimpleNamespace(id=1)


@dataclass(eq=False)
class _Member:
    id: int
    joined_at: datetime.datetime = START
    created_at: datetime.datetime = START - datetime.timedelta(days=400)
    bot: bool = False
    guild: SimpleNamespace = field(default_factory=lambda: GUILD)

    def __str__(self) -> str:
        return f"member-{self.id}"


# -- mention spam -------------------------------------------------------------------------------


def _mentions(seconds: float, author: _Member, *mentioned: _Member) -> SimpleNamespace:
    return SimpleNamespace(
        author=author, mentions=list(mentioned), created_at=START + datetime.timedelta(seconds=seconds)
    )


def _people(n: int, start: int = 100) -> list[_Member]:
    return [_Member(start + i) for i in range(n)]


@pytest.mark.parametrize(("stored", "threshold"), [(None, None), (0, None), (-1, None), (1, 3), (2, 3), (3, 3), (8, 8)])
def test_mention_threshold_is_off_or_at_least_three(stored: int | None, threshold: int | None) -> None:
    assert mention_threshold(SimpleNamespace(mention_count=stored)) == threshold  # type: ignore[arg-type]


def test_one_message_over_the_threshold_is_reported_with_its_own_count() -> None:
    checker = SpamChecker()
    author = _Member(1)

    assert checker.mention_spam(_mentions(0, author, *_people(3)), 3) is None  # exactly the threshold is allowed
    assert checker.mention_spam(_mentions(1, _Member(2), *_people(5)), 3) == (5, False)


def test_mentions_add_up_over_the_window_and_report_the_real_total() -> None:
    checker = SpamChecker()
    author = _Member(1)

    assert checker.mention_spam(_mentions(0, author, *_people(2)), 3) is None
    assert checker.mention_spam(_mentions(5, author, *_people(3, start=200)), 3) == (5, True)
    # The trip cleared the history, so the next ping starts from zero.
    assert checker.mention_spam(_mentions(6, author, *_people(1)), 3) is None


def test_mentions_older_than_the_window_do_not_count() -> None:
    checker = SpamChecker()
    author = _Member(1)

    assert checker.mention_spam(_mentions(0, author, *_people(3)), 3) is None
    assert checker.mention_spam(_mentions(20, author, *_people(3)), 3) is None


def test_bots_and_self_mentions_do_not_count() -> None:
    checker = SpamChecker()
    author = _Member(1)
    bots = [_Member(300 + i, bot=True) for i in range(5)]

    assert checker.mention_spam(_mentions(0, author, author, *bots, *_people(3)), 3) is None


# -- dashboard API validation -------------------------------------------------------------------


@pytest.mark.parametrize("value", [1, 2, -1, True, "5", 2.5])
def test_api_rejects_a_mention_count_below_the_floor(value: object) -> None:
    with pytest.raises(HTTPException) as exc:
        _check_mention_count(value)
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException):
        _build_config_updates({"mention_count": value}, SimpleNamespace())


@pytest.mark.parametrize("value", [None, 0, 3, 50])
def test_api_accepts_off_or_at_least_three(value: int | None) -> None:
    assert _build_config_updates({"mention_count": value}, SimpleNamespace()) == {"mention_count": value}


# -- Sentinel bypass ----------------------------------------------------------------------------


def test_members_held_by_the_auto_trigger_count_as_bypassing() -> None:
    raider = _Member(7, joined_at=START - datetime.timedelta(seconds=3))  # joined before Sentinel started
    sentinel = SimpleNamespace(started_at=START, is_blocked=lambda user_id: user_id == 7)

    assert Sentinel.is_bypassing(sentinel, raider)  # type: ignore[arg-type]
    assert not Sentinel.is_bypassing(sentinel, _Member(8))  # type: ignore[arg-type]
    assert not Sentinel.is_bypassing(SimpleNamespace(started_at=None, is_blocked=lambda _: True), raider)  # type: ignore[arg-type]


# -- raid ban stops further handling ------------------------------------------------------------


async def test_check_raid_reports_whether_the_author_was_banned() -> None:
    config = SimpleNamespace(flags=SimpleNamespace(raid=True), send_alert=AsyncMock())
    guild = MagicMock(ban=AsyncMock())
    guild.me.guild_permissions.ban_members = True
    author, other = _Member(1), _Member(2)
    message = SimpleNamespace(channel=SimpleNamespace(id=9))

    def checker(members: list[_Member]) -> MagicMock:
        return MagicMock(is_spamming=MagicMock(return_value=SpammerSequence(members)), claim=list)

    assert await check_raid(checker([author]), config, guild, author, message) is True  # type: ignore[arg-type]
    assert await check_raid(checker([other]), config, guild, author, message) is False  # type: ignore[arg-type]


# -- the cog listeners --------------------------------------------------------------------------


def _cog(config: SimpleNamespace, sentinel: object = None) -> Moderation:
    cog = object.__new__(Moderation)
    cog.bot = MagicMock()  # type: ignore[assignment]
    cog.bot.db.get_guild_config = AsyncMock(return_value=config)
    cog.bot.db.get_guild_sentinel = AsyncMock(return_value=sentinel)
    cog._spam_check = defaultdict(SpamChecker)
    return cog


def _join_config(*, sentinel: bool) -> SimpleNamespace:
    return SimpleNamespace(
        flags=SimpleNamespace(alerts=True, sentinel=sentinel), is_muted=lambda _: False, send_alert=AsyncMock()
    )


async def test_join_burst_alerts_with_sentinel_off_and_feeds_the_massban_button() -> None:
    config = _join_config(sentinel=False)
    cog = _cog(config)
    now = discord.utils.utcnow()  # the join limiter (10 per 5 s) starts its window when the checker is made
    joins = [_Member(i, joined_at=now + datetime.timedelta(seconds=1)) for i in range(11)]

    for member in joins:
        await Moderation.on_member_join(cog, member)  # type: ignore[arg-type]

    config.send_alert.assert_awaited_once()
    assert "11 members" in config.send_alert.await_args.args[0]
    checker = cog._spam_check[GUILD.id]
    assert {s.id for s in checker.raid_suspects()} == {m.id for m in joins}
    assert not checker.flagged_users  # an alert asks for review; it does not flag anyone


async def test_join_burst_stays_quiet_when_a_sentinel_auto_trigger_is_configured() -> None:
    config = _join_config(sentinel=True)
    sentinel = MagicMock(started_at=START, rate=(20, 5), requires_setup=False, block=AsyncMock())
    cog = _cog(config, sentinel)

    now = discord.utils.utcnow()
    for i in range(11):  # enough to trip the alert-only check, were it consulted
        await Moderation.on_member_join(cog, _Member(i, joined_at=now + datetime.timedelta(seconds=1)))  # type: ignore[arg-type]

    assert sentinel.block.await_count == 11
    config.send_alert.assert_not_awaited()


def _message(channel: SimpleNamespace, author: MagicMock) -> MagicMock:
    message = MagicMock(channel=channel, author=author)
    message.guild.id = 1
    message.is_system.return_value = False
    return message


def _author(user_id: int = 5) -> MagicMock:
    author = MagicMock(spec=discord.Member, id=user_id, bot=False, _roles=[])
    author.guild_permissions.manage_messages = False
    return author


async def test_threads_under_an_ignored_channel_are_ignored() -> None:
    config = SimpleNamespace(safe_automod_entity_ids={42})
    cog = _cog(config)
    cog._schedule_ai_moderation = MagicMock()  # type: ignore[method-assign]
    thread = SimpleNamespace(id=77, parent_id=42, permissions_for=lambda _: SimpleNamespace(manage_messages=False))

    await Moderation.on_message(cog, _message(thread, _author()))

    cog._schedule_ai_moderation.assert_not_called()


async def test_channel_moderators_are_exempt_where_they_moderate() -> None:
    cog = _cog(SimpleNamespace(safe_automod_entity_ids=set()))
    channel = SimpleNamespace(id=77, permissions_for=lambda _: SimpleNamespace(manage_messages=True))

    await Moderation.on_message(cog, _message(channel, _author()))

    cog.bot.db.get_guild_config.assert_not_awaited()
