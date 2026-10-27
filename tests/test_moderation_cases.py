"""Every moderation action leaves a correct mod case.

Automatic actions (raid and mention-spam bans, Sentinel bypass removals, timer expiries) are
recorded under Percy's name with the automatic reason, through the same ``mod_action`` dispatch
the commands use; a softban whose unban fails is recorded as the ban it became; a long reason
never breaks the case embed; and a deleted newest case never hands its number on.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from app.cogs.moderation.antispam import SpamChecker, check_raid, mention_spam_ban
from app.cogs.moderation.cog import Moderation
from app.cogs.moderation.infractions import softban
from app.cogs.moderation.models import SpamCheckerResult
from app.database.repositories import CasesRepository

PERCY = 999


def _http(status: int) -> discord.HTTPException:
    cls = {403: discord.Forbidden, 404: discord.NotFound}.get(status, discord.HTTPException)
    return cls(MagicMock(status=status, reason="x"), "error")


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        flags=SimpleNamespace(raid=True), send_alert=AsyncMock(), alert_missing_permission=AsyncMock(), bot=MagicMock()
    )


def _dispatched(bot: MagicMock) -> list[tuple]:
    return [c.args for c in bot.dispatch.call_args_list if c.args[0] == "mod_action"]


# -- automatic bans -----------------------------------------------------------------------------


async def test_raid_ban_is_recorded_under_percy() -> None:
    config = _config()
    guild = MagicMock(id=1, ban=AsyncMock())
    guild.me.id = PERCY
    guild.me.guild_permissions.ban_members = True
    raider = SimpleNamespace(id=7)
    checker = MagicMock(is_spamming=MagicMock(return_value=SpamCheckerResult.spammer()), claim=lambda u: list(u))

    await check_raid(checker, config, guild, raider, SimpleNamespace(channel=SimpleNamespace(id=9)))  # type: ignore[arg-type]

    [(_, guild_id, action, target, moderator, reason)] = _dispatched(config.bot)
    assert (guild_id, action, target, moderator) == (1, "ban", 7, PERCY)
    assert reason == SpamCheckerResult.spammer().reason


async def test_failed_raid_ban_records_no_case() -> None:
    config = _config()
    guild = MagicMock(id=1, ban=AsyncMock(side_effect=_http(500)))
    guild.me.guild_permissions.ban_members = True
    checker = MagicMock(is_spamming=MagicMock(return_value=SpamCheckerResult.spammer()), claim=lambda u: list(u))

    await check_raid(checker, config, guild, SimpleNamespace(id=7), SimpleNamespace(channel=SimpleNamespace(id=9)))  # type: ignore[arg-type]

    assert _dispatched(config.bot) == []


async def test_mention_spam_ban_is_recorded_under_percy() -> None:
    config = _config()
    member = MagicMock(id=7, ban=AsyncMock(), top_role=1)
    member.guild.id = 1
    member.guild.me.id = PERCY
    member.guild.me.top_role = 5
    member.guild.me.guild_permissions.ban_members = True

    await mention_spam_ban(SpamChecker(), config, member, 6)  # type: ignore[arg-type]

    [(_, _, action, target, moderator, reason)] = _dispatched(config.bot)
    assert (action, target, moderator) == ("ban", 7, PERCY)
    assert "6 mentions" in reason


# -- sentinel bypass ----------------------------------------------------------------------------


def _bypasser(**kwargs: object) -> MagicMock:
    member = MagicMock(id=7, **{"kick": AsyncMock(), "ban": AsyncMock(), **kwargs})
    member.guild.id = 1
    member.guild.me.id = PERCY
    return member


def _sentinel_cog() -> SimpleNamespace:
    return SimpleNamespace(bot=MagicMock(), _spam_check=defaultdict(SpamChecker))


async def test_sentinel_bypass_kick_is_recorded_under_percy() -> None:
    cog, config, member = _sentinel_cog(), _config(), _bypasser()

    await Moderation._remove_sentinel_bypasser(cog, config, member, "kick", "Bypassing sentinel")  # type: ignore[arg-type]

    member.kick.assert_awaited_once()
    assert _dispatched(cog.bot) == [("mod_action", 1, "kick", 7, PERCY, "Bypassing sentinel")]


async def test_sentinel_bypass_failure_alerts_once_and_records_nothing() -> None:
    cog, config = _sentinel_cog(), _config()
    member = _bypasser(ban=AsyncMock(side_effect=_http(403)))

    for _ in range(3):  # the bypasser keeps talking
        await Moderation._remove_sentinel_bypasser(cog, config, member, "ban", "Bypassing sentinel")  # type: ignore[arg-type]

    config.send_alert.assert_awaited_once()
    assert "Could not ban" in config.send_alert.await_args.args[0]
    assert _dispatched(cog.bot) == []
    member.ban.assert_awaited_once()  # one attempt, not one Discord call per message


async def test_sentinel_bypass_without_the_permission_alerts_without_calling_discord() -> None:
    cog, config, member = _sentinel_cog(), _config(), _bypasser()
    member.guild.me.guild_permissions.kick_members = False

    for _ in range(3):
        await Moderation._remove_sentinel_bypasser(cog, config, member, "kick", "Bypassing sentinel")  # type: ignore[arg-type]

    member.kick.assert_not_awaited()
    config.send_alert.assert_awaited_once()
    assert "Kick Members" in config.send_alert.await_args.args[0]


async def test_mute_check_says_a_deleted_mute_role_no_longer_exists() -> None:
    from app.utils import checks

    def command() -> None: ...

    checks.can_mute()(command)
    predicate = command.__commands_checks__[0]  # type: ignore[attr-defined]

    for role_id, expected in ((42, "no longer exists"), (None, "has not been set up")):
        ctx = MagicMock(send_error=AsyncMock())
        ctx.bot.db.get_guild_config = AsyncMock(return_value=SimpleNamespace(mute_role=None, mute_role_id=role_id))
        assert await predicate(ctx) is False
        assert expected in ctx.send_error.await_args.args[0]


# -- timer expiry -------------------------------------------------------------------------------


def _timer(event: str, *args: object) -> SimpleNamespace:
    return SimpleNamespace(id=1, event=event, args=list(args), kwargs={}, created=datetime.datetime(2026, 1, 1), timezone="UTC")


def _timer_cog(guild: MagicMock) -> SimpleNamespace:
    bot = MagicMock()
    bot.wait_until_ready = AsyncMock()
    bot.get_guild.return_value = guild
    bot.get_or_fetch_member = AsyncMock(return_value="mod")
    bot.timers.create = AsyncMock()
    bot.db.get_guild_config = AsyncMock(return_value=MagicMock(mute_role_id=None))
    return SimpleNamespace(bot=bot, _mute_data_batch=defaultdict(list))


def _timer_guild(**kwargs: object) -> MagicMock:
    guild = MagicMock(unavailable=False, **kwargs)
    guild.me.id = PERCY
    return guild


async def test_tempban_expiry_records_an_unban_under_percy() -> None:
    cog = _timer_cog(_timer_guild(unban=AsyncMock()))
    await Moderation.on_tempban_timer_complete(cog, _timer("tempban", 1, 2, 3))  # type: ignore[arg-type]

    [(_, guild_id, action, target, moderator, reason)] = _dispatched(cog.bot)
    assert (guild_id, action, target, moderator) == (1, "unban", 3, PERCY)
    assert reason.startswith("Automatic unban")


async def test_tempban_expiry_already_unbanned_records_nothing() -> None:
    cog = _timer_cog(_timer_guild(unban=AsyncMock(side_effect=_http(404))))
    await Moderation.on_tempban_timer_complete(cog, _timer("tempban", 1, 2, 3))  # type: ignore[arg-type]
    assert _dispatched(cog.bot) == []


def _muted(member_id: int) -> MagicMock:
    member = MagicMock(id=member_id, remove_roles=AsyncMock())
    member._roles.has.side_effect = lambda r: r == 40
    return member


async def test_tempmute_expiry_records_an_unmute_under_percy() -> None:
    cog = _timer_cog(_timer_guild(get_member=MagicMock(return_value=_muted(3))))
    await Moderation.on_tempmute_timer_complete(cog, _timer("tempmute", 1, 2, 3, 40))  # type: ignore[arg-type]

    [(_, _, action, target, moderator, reason)] = _dispatched(cog.bot)
    assert (action, target, moderator) == ("unmute", 3, PERCY)
    assert reason.startswith("Automatic unmute")


async def test_self_mute_expiry_records_nothing() -> None:
    cog = _timer_cog(_timer_guild(get_member=MagicMock(return_value=_muted(3))))
    await Moderation.on_tempmute_timer_complete(cog, _timer("tempmute", 1, 3, 3, 40))  # type: ignore[arg-type]
    assert _dispatched(cog.bot) == []


# -- softban ------------------------------------------------------------------------------------


async def test_softban_whose_unban_fails_reports_the_standing_ban() -> None:
    bot = MagicMock()
    bot.timers.delete_member_timer = AsyncMock()
    guild = MagicMock(id=1, ban=AsyncMock(), unban=AsyncMock(side_effect=_http(500)))

    assert await softban(bot, guild, SimpleNamespace(id=7), reason="r") is False

    guild.ban.assert_awaited_once()
    bot.timers.delete_member_timer.assert_awaited_once_with("tempban", 1, 7)


async def test_softban_success() -> None:
    bot = MagicMock()
    bot.timers.delete_member_timer = AsyncMock()
    guild = MagicMock(id=1, ban=AsyncMock(), unban=AsyncMock())

    assert await softban(bot, guild, SimpleNamespace(id=7), reason="r") is True
    bot.timers.delete_member_timer.assert_not_awaited()


# -- case numbering (the case card is covered in test_modlog_ui.py) ---------------------------


async def test_case_number_comes_from_the_high_water_mark() -> None:
    con = MagicMock(execute=AsyncMock(), fetchrow=AsyncMock(return_value="row"))
    con.transaction.return_value.__aenter__ = AsyncMock()
    con.transaction.return_value.__aexit__ = AsyncMock(return_value=None)
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=con)
    acquire.__aexit__ = AsyncMock(return_value=None)
    repo = CasesRepository(MagicMock(acquire=MagicMock(return_value=acquire)))

    assert await repo.create_case(1, "ban", 7, 8, "r") == "row"

    query = con.fetchrow.await_args.args[0]
    # The stored maximum, never just MAX(case_index) + 1, which reuses a deleted newest number.
    assert "last_case_index" in query
    assert "GREATEST(modlog_config.last_case_index + 1, EXCLUDED.last_case_index)" in query
