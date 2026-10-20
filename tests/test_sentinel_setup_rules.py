"""The sentinel setup menu and the dashboard sentinel endpoints enforce the same rules."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from fastapi import HTTPException

from app.cogs.moderation import sentinel as ui
from app.cogs.moderation.antispam import SpamChecker
from app.cogs.moderation.cog import Moderation
from app.database.base import Sentinel
from app.internal_api.routers import guild as api


class _Role:
    def __init__(self, role_id: int, position: int, *, default: bool = False, managed: bool = False) -> None:
        self.id = role_id
        self.name = f"role-{role_id}"
        self.mention = f"<@&{role_id}>"
        self.position = position
        self.managed = managed
        self._default = default
        self.guild: Any = None
        self.members: list[Any] = []

    def is_default(self) -> bool:
        return self._default

    def __ge__(self, other: _Role) -> bool:
        return self.position >= other.position


def _guild(
    *roles: _Role, top: int = 10, manage_roles: bool = True, channels: dict[int, Any] | None = None, chunked: bool = True
) -> Any:
    guild = SimpleNamespace(
        id=1,
        chunked=chunked,
        me=SimpleNamespace(top_role=_Role(999, top), guild_permissions=SimpleNamespace(manage_roles=manage_roles)),
        default_role=_Role(0, 0, default=True),
        get_role={r.id: r for r in roles}.get,
        get_channel=(channels or {}).get,
        channels=list((channels or {}).values()),
    )
    for role in roles:
        role.guild = guild
    return guild


def _text_channel(channel_id: int, *, can_send: bool = True, public: bool = False) -> Any:
    channel = MagicMock(spec=discord.TextChannel, id=channel_id)
    channel.name = f"ch-{channel_id}"
    channel.permissions_for.return_value = SimpleNamespace(
        send_messages=can_send, embed_links=can_send, read_messages=public
    )
    return channel


def _sentinel(**kwargs: Any) -> Any:
    values: dict[str, Any] = {
        "started_at": None, "role_id": None, "starter_role_id": None, "channel_id": None,
        "message_id": None, "message": None, "role": None,
    }
    values.update(kwargs)
    return MagicMock(edit=AsyncMock(), **values)


def _bot(sentinel: Any, *, flag: bool = True) -> Any:
    bot = MagicMock()
    bot.db.get_guild_sentinel = AsyncMock(return_value=sentinel)
    bot.db.get_guild_config = AsyncMock(return_value=SimpleNamespace(flags=SimpleNamespace(sentinel=flag)))
    return bot


# -- shared role rules (#2, #9) -----------------------------------------------------------------


def test_role_rules() -> None:
    lockdown, starter, everyone = _Role(1, 2), _Role(2, 3), _Role(3, 0, default=True)
    managed, above = _Role(4, 2, managed=True), _Role(5, 10)
    _guild(lockdown, starter, everyone, managed, above, top=10)

    assert ui.sentinel_role_problem(lockdown, other=starter.id) is None
    assert "@everyone" in (ui.sentinel_role_problem(everyone, other=None) or "")
    assert "integration" in (ui.sentinel_role_problem(managed, other=None) or "")
    assert "highest role" in (ui.sentinel_role_problem(above, other=None) or "")
    assert "different" in (ui.sentinel_role_problem(lockdown, other=lockdown.id) or "")


def test_lockdown_sync_covers_every_channel_but_the_verification_one() -> None:
    verify, general, category = object(), object(), object()
    guild = SimpleNamespace(channels=[verify, general, category])

    assert ui.lockdown_channels(guild, verify) == [general, category]  # type: ignore[arg-type]
    assert ui.lockdown_channels(guild, None) == [verify, general, category]  # type: ignore[arg-type]


async def test_dashboard_sync_alerts_when_channels_could_not_be_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    role = _Role(1, 2)
    guild = _guild(role)
    monkeypatch.setattr(ui, "update_role_permissions", AsyncMock(return_value=(3, 1, 1)))
    overwrite = AsyncMock()
    monkeypatch.setattr(ui.SentinelChannelSelect, "apply_verification_overwrites", overwrite)
    config = SimpleNamespace(send_alert=AsyncMock())
    sentinel = SimpleNamespace(role=role, channel=_text_channel(9), bot=MagicMock())
    sentinel.bot.db.get_guild_config = AsyncMock(return_value=config)

    await ui.sync_sentinel_permissions(sentinel, guild.me)  # type: ignore[arg-type]

    overwrite.assert_awaited_once()
    config.send_alert.assert_awaited_once()
    assert "2 channels could not be hidden" in config.send_alert.await_args.args[0]


# -- dashboard validation (#9) ------------------------------------------------------------------


@pytest.mark.parametrize("rate", ["abc", "5/0", "0/5", "5", 5, "-1/5"])
def test_bad_rates_are_a_400(rate: object) -> None:
    with pytest.raises(HTTPException) as exc:
        api._parse_sentinel_rate(rate)
    assert exc.value.status_code == 400


def test_rates_parse_or_clear() -> None:
    assert api._parse_sentinel_rate("5/10") == (5, 10)
    assert api._parse_sentinel_rate(None) is None


@pytest.mark.parametrize("value", ["abc", "", True, 1.5, -3])
def test_bad_ids_are_a_400(value: object) -> None:
    with pytest.raises(HTTPException) as exc:
        api._snowflake_or_none(value, "role_id")
    assert exc.value.status_code == 400


async def test_role_and_channel_cannot_change_while_active() -> None:
    role, other = _Role(1, 2), _Role(2, 3)
    guild = _guild(role, other)
    sentinel = _sentinel(started_at=discord.utils.utcnow(), role_id=1, channel_id=9)

    with pytest.raises(HTTPException) as exc:
        await api._apply_sentinel_settings(guild, _bot(sentinel), {"role_id": 2})
    assert exc.value.status_code == 400
    assert "stop Sentinel" in exc.value.detail
    sentinel.edit.assert_not_awaited()

    # The dashboard resends unchanged values on every save; those are not a change.
    await api._apply_sentinel_settings(guild, _bot(sentinel), {"role_id": 1, "channel_id": 9, "bypass_action": "kick"})
    sentinel.edit.assert_awaited_once_with(role_id=1, channel_id=9, bypass_action="kick")


async def test_starter_role_must_differ_from_the_lockdown_role() -> None:
    role = _Role(1, 2)
    sentinel = _sentinel(role_id=1)

    with pytest.raises(HTTPException) as exc:
        await api._apply_sentinel_settings(_guild(role), _bot(sentinel), {"starter_role_id": 1})
    assert "different" in exc.value.detail


async def test_a_role_above_percy_is_rejected() -> None:
    role = _Role(1, 50)

    with pytest.raises(HTTPException) as exc:
        await api._apply_sentinel_settings(_guild(role, top=10), _bot(_sentinel()), {"role_id": 1})
    assert "highest role" in exc.value.detail


async def test_explicit_nulls_clear_rate_and_starter_role() -> None:
    body = api.PatchSentinelBody.model_validate({"rate": None, "starter_role_id": None})
    sentinel = _sentinel(rate=(5, 5), starter_role_id=2)

    result = await api.patch_sentinel(_guild(), _bot(sentinel), body)  # type: ignore[arg-type]

    assert result["ok"] is True
    sentinel.edit.assert_awaited_once_with(rate=None, starter_role_id=None)


async def test_a_new_lockdown_role_is_synced(monkeypatch: pytest.MonkeyPatch) -> None:
    role = _Role(1, 2)
    schedule = MagicMock(return_value=True)
    monkeypatch.setattr(api, "_schedule_permission_sync", schedule)
    sentinel = _sentinel()

    result = await api._apply_sentinel_settings(_guild(role), _bot(sentinel), {"role_id": 1})

    assert result == {"permission_sync": True, "warnings": []}
    schedule.assert_called_once()


async def test_moving_the_channel_retires_the_old_card(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "_schedule_permission_sync", MagicMock(return_value=False))
    old_card = MagicMock(delete=AsyncMock())
    sentinel = _sentinel(channel_id=8, message_id=5, message=old_card)
    guild = _guild(channels={9: _text_channel(9)})

    await api._apply_sentinel_settings(guild, _bot(sentinel), {"channel_id": 9})

    sentinel.edit.assert_awaited_once_with(channel_id=9, message_id=None)
    old_card.delete.assert_awaited_once()


async def test_a_role_members_already_wear_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dashboard syncs the lockdown role unasked: a role like "Members" would lock everyone out."""
    schedule = MagicMock(return_value=True)
    monkeypatch.setattr(api, "_schedule_permission_sync", schedule)
    role = _Role(1, 2)
    role.members = [SimpleNamespace(id=5), SimpleNamespace(id=6)]

    with pytest.raises(HTTPException) as exc:
        await api._apply_sentinel_settings(_guild(role), _bot(_sentinel()), {"role_id": 1})
    assert exc.value.status_code == 400
    assert "2 member(s) already have" in exc.value.detail
    schedule.assert_not_called()

    # Members Sentinel itself holds wear it by design, and an unchanged role is not re-checked.
    held = _sentinel(members={5, 6})
    await api._apply_sentinel_settings(_guild(role), _bot(held), {"role_id": 1})
    schedule.assert_called_once()
    worn = _sentinel(role_id=1, members=set())
    await api._apply_sentinel_settings(_guild(role), _bot(worn), {"role_id": 1, "bypass_action": "ban"})


async def test_a_public_verification_channel_is_reported_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hiding a channel from @everyone is left to a human: the response says so instead."""
    monkeypatch.setattr(api, "_schedule_permission_sync", MagicMock(return_value=True))
    guild = _guild(channels={9: _text_channel(9, public=True)})

    result = await api._apply_sentinel_settings(guild, _bot(_sentinel()), {"channel_id": 9})

    assert len(result["warnings"]) == 1
    assert "visible to @everyone" in result["warnings"][0]


async def test_the_dashboard_sync_never_hides_the_channel_from_everyone(monkeypatch: pytest.MonkeyPatch) -> None:
    role = _Role(1, 2)
    monkeypatch.setattr(ui, "update_role_permissions", AsyncMock(return_value=(1, 0, 0)))
    channel = _text_channel(9, public=True)
    channel.guild = _guild(role)
    channel.overwrites_for.side_effect = lambda _: discord.PermissionOverwrite()
    channel.guild.me.guild_permissions = discord.Permissions.all()
    sentinel = SimpleNamespace(role=role, channel=channel, bot=MagicMock())

    await ui.sync_sentinel_permissions(sentinel, SimpleNamespace(id=1))  # type: ignore[arg-type]

    targets = [call.args[0] for call in channel.set_permissions.await_args_list]
    assert targets == [role]  # the lockdown role is opened; @everyone is left alone


async def test_a_failing_background_sync_is_logged(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(ui, "sync_sentinel_permissions", AsyncMock(side_effect=RuntimeError("boom")))
    sentinel = SimpleNamespace(role=object())

    assert api._schedule_permission_sync(sentinel, _guild()) is True  # type: ignore[arg-type]
    await asyncio.gather(*api._background_syncs)

    assert "Sentinel permission sync failed" in caplog.text


async def test_a_channel_percy_cannot_post_in_is_rejected() -> None:
    guild = _guild(channels={9: _text_channel(9, can_send=False)})

    with pytest.raises(HTTPException) as exc:
        await api._apply_sentinel_settings(guild, _bot(_sentinel()), {"channel_id": 9})
    assert "Send Messages" in exc.value.detail


async def test_batch_sentinel_op_validates_like_patch() -> None:
    # A bad sentinel op is reported in its result, like sentinel_toggle, not as a 400 after the
    # ops before it were already applied.
    body = api.BatchGuildConfigBody.model_validate(
        {"operations": [{"type": "sentinel", "data": {"rate": "abc"}}, {"type": "sentinel_toggle", "data": {}}]}
    )
    sentinel = _sentinel()

    result = await api.batch_guild_config(_guild(), _bot(sentinel), body)  # type: ignore[arg-type]

    first, second = result["results"]
    assert first["ok"] is False and "JOINS/SECONDS" in first["error"]
    assert second["ok"] is True  # the batch carries on
    sentinel.edit.assert_not_awaited()


async def test_an_unchanged_role_now_above_percy_is_a_warning_not_a_400() -> None:
    """The dashboard resends the role on every save; saving the rate must not fail on it."""
    lockdown, starter = _Role(1, 50), _Role(2, 60)  # both dragged above Percy (top 10) since
    sentinel = _sentinel(role_id=1, starter_role_id=2)

    result = await api._apply_sentinel_settings(
        _guild(lockdown, starter), _bot(sentinel), {"role_id": 1, "starter_role_id": 2, "rate": "5/10"}
    )

    sentinel.edit.assert_awaited_once_with(rate=(5, 10), role_id=1, starter_role_id=2)
    assert len(result["warnings"]) == 2
    assert all("highest role" in w for w in result["warnings"])

    # A *changed* starter role is still refused.
    with pytest.raises(HTTPException):
        await api._apply_sentinel_settings(_guild(lockdown, starter), _bot(_sentinel(role_id=1)), {"starter_role_id": 2})


async def test_a_new_lockdown_role_is_refused_while_the_member_list_is_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without every member cached, role.members could miss the people who wear the role."""
    schedule = MagicMock(return_value=True)
    monkeypatch.setattr(api, "_schedule_permission_sync", schedule)

    with pytest.raises(HTTPException) as exc:
        await api._apply_sentinel_settings(_guild(_Role(1, 2), chunked=False), _bot(_sentinel()), {"role_id": 1})
    assert "member list" in exc.value.detail
    schedule.assert_not_called()


async def test_enabling_requires_the_sentinel_flag() -> None:
    role = _Role(1, 2)
    sentinel = _sentinel(role_id=1, channel_id=9, message_id=5, role=role, requires_setup=False, enable=AsyncMock())
    guild = _guild(role)

    with pytest.raises(HTTPException) as exc:
        await api.toggle_sentinel(guild, _bot(sentinel, flag=False), api.ToggleSentinelBody(enabled=True))  # type: ignore[arg-type]
    assert "Sentinel protection" in exc.value.detail
    sentinel.enable.assert_not_awaited()

    body = api.BatchGuildConfigBody.model_validate({"operations": [{"type": "sentinel_toggle", "data": {"enabled": True}}]})
    result = await api.batch_guild_config(guild, _bot(sentinel, flag=False), body)  # type: ignore[arg-type]
    assert result["results"][0]["ok"] is False
    sentinel.enable.assert_not_awaited()

    await api.toggle_sentinel(guild, _bot(sentinel), api.ToggleSentinelBody(enabled=True))  # type: ignore[arg-type]
    sentinel.enable.assert_awaited_once()


async def test_reposting_the_card_replaces_the_old_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "SentinelVerifyView", MagicMock())
    channel = _text_channel(9)
    channel.send = AsyncMock(return_value=SimpleNamespace(id=77))
    order: list[str] = []
    old_card = MagicMock(delete=AsyncMock(side_effect=lambda: order.append("delete")))
    sentinel = _sentinel(started_at=discord.utils.utcnow(), channel_id=9, message_id=5, message=old_card)
    sentinel.edit.side_effect = lambda **_: order.append("edit")
    guild = _guild(channels={9: channel})

    result = await api.send_sentinel_message(guild, _bot(sentinel), api.SendSentinelMessageBody(channel_id="9"))  # type: ignore[arg-type]

    assert result["message_id"] == 77
    sentinel.edit.assert_awaited_once_with(channel_id=9, message_id=77)
    assert order == ["edit", "delete"]  # repoint first, so the deletion listener ignores the old card

    with pytest.raises(HTTPException) as exc:  # active: the card cannot move to another channel
        await api.send_sentinel_message(guild, _bot(sentinel), api.SendSentinelMessageBody(channel_id="10"))  # type: ignore[arg-type]
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await api.send_sentinel_message(guild, _bot(sentinel), api.SendSentinelMessageBody(channel_id="abc"))  # type: ignore[arg-type]
    assert exc.value.status_code == 400


# -- Discord buttons (#8, verify re-block) ------------------------------------------------------


def _interaction(**perms: bool) -> Any:
    interaction = MagicMock(guild_id=1)
    interaction.permissions = SimpleNamespace(**{"ban_members": False, "manage_messages": False, **perms})
    interaction.response.send_message = AsyncMock()
    return interaction


async def test_stand_down_needs_moderator_permissions() -> None:
    button = ui.SentinelAlertResolveButton(_sentinel(started_at=discord.utils.utcnow()))

    helper = _interaction(manage_messages=True)
    assert await button.interaction_check(helper) is False
    assert "Ban Members" in helper.response.send_message.await_args.args[0]

    assert await button.interaction_check(_interaction(ban_members=True, manage_messages=True)) is True


async def test_verify_does_not_newly_hold_a_member_with_a_stale_lockdown_role() -> None:
    sentinel = _sentinel(started_at=discord.utils.utcnow(), block=AsyncMock())
    sentinel.is_blocked.return_value = False
    sentinel.has_role.return_value = True
    button = ui.SentinelVerifyButton(SimpleNamespace(flags=SimpleNamespace(sentinel=True)), sentinel)  # type: ignore[arg-type]
    interaction = _interaction()
    interaction.user = MagicMock(spec=discord.Member, id=5)

    assert await button.interaction_check(interaction) is True
    sentinel.block.assert_not_awaited()  # abandoning the captcha must not expose them to the bypass ban


async def test_unblock_queues_removal_even_without_a_row() -> None:
    bot = MagicMock()
    bot.db.guilds.release_sentinel_member = AsyncMock()
    sentinel = SimpleNamespace(id=1, members={5}, bot=bot)

    await Sentinel.unblock(sentinel, SimpleNamespace(id=5))  # type: ignore[arg-type]

    bot.db.guilds.release_sentinel_member.assert_awaited_once_with(1, 5)
    bot.db.sentinel_roles.wake.assert_called_once_with(1)
    assert sentinel.members == set()


def _flagged_checker(*user_ids: int) -> SpamChecker:
    checker = SpamChecker()
    for user_id in user_ids:
        member = MagicMock(id=user_id, joined_at=discord.utils.utcnow())
        checker.flag_member(member)
        checker.join_spammers[user_id] = checker.flagged_users[user_id]
    return checker


async def test_passing_the_captcha_clears_the_raider_mark() -> None:
    checker = _flagged_checker(5, 6)
    sentinel = MagicMock(id=1, unblock=AsyncMock())
    sentinel.bot.db.get_guild_sentinel = AsyncMock(return_value=sentinel)
    sentinel.bot.get_cog.return_value = SimpleNamespace(_spam_check={1: checker})
    view = object.__new__(ui.CaptchaChallengeView)
    view.sentinel, view.member, view.text = sentinel, SimpleNamespace(id=5), "abc123"
    view.stop = MagicMock()  # type: ignore[method-assign]
    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view.check_answer(interaction, "abc123")

    sentinel.unblock.assert_awaited_once()
    assert not checker.is_flagged(5) and 5 not in checker.join_spammers
    assert checker.is_flagged(6)  # only the member who verified


async def test_stand_down_disables_sentinel() -> None:
    sentinel = _sentinel(started_at=discord.utils.utcnow(), id=1, members={5, 6}, pending_members=0, disable=AsyncMock())
    interaction = _interaction(ban_members=True, manage_messages=True)
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.message.edit = AsyncMock()

    await ui.SentinelAlertResolveButton(sentinel).callback(interaction)

    sentinel.disable.assert_awaited_once()  # disable() itself dispatches sentinel_disabled


async def test_ending_sentinel_forgets_every_raider_it_flagged() -> None:
    # Whatever ended it (Stand Down, the menu, the dashboard, a deleted channel), nobody Sentinel
    # flagged is a raider any more, held or not; alert-only suspects lose just the released ids.
    from app.cogs.moderation.cog import Moderation

    checker = _flagged_checker(5, 6, 7)
    cog = SimpleNamespace(_spam_check={1: checker})

    await Moderation.on_sentinel_disabled(cog, 1, [5, 6])  # type: ignore[arg-type]

    assert [checker.is_flagged(i) for i in (5, 6, 7)] == [False, False, False]
    assert sorted(checker.join_spammers) == [7]
    await Moderation.on_sentinel_disabled(cog, 2, [5])  # type: ignore[arg-type]  # no checker: harmless


def test_forgetting_suspects_without_the_moderation_cog_is_harmless() -> None:
    bot = MagicMock()
    bot.get_cog.return_value = None
    ui.forget_raid_suspects(bot, 1, [5])  # must not raise


# -- rejoin while muted (#15) -------------------------------------------------------------------


async def test_a_muted_member_who_rejoins_is_still_held() -> None:
    config = SimpleNamespace(
        flags=SimpleNamespace(alerts=False, sentinel=True), is_muted=lambda _: True, apply_mute=AsyncMock()
    )
    sentinel = MagicMock(started_at=discord.utils.utcnow(), rate=None, requires_setup=False, block=AsyncMock())
    cog = object.__new__(Moderation)
    cog.bot = MagicMock()  # type: ignore[assignment]
    cog.bot.db.get_guild_config = AsyncMock(return_value=config)
    cog.bot.db.get_guild_sentinel = AsyncMock(return_value=sentinel)
    cog._spam_check = MagicMock()
    member = SimpleNamespace(id=5, bot=False, guild=SimpleNamespace(id=1))

    await Moderation.on_member_join(cog, member)  # type: ignore[arg-type]

    config.apply_mute.assert_awaited_once()
    sentinel.block.assert_awaited_once_with(member)
