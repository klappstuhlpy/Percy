"""The sentinel setup menu and the dashboard sentinel endpoints enforce the same rules."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from fastapi import HTTPException

from app.cogs.moderation import sentinel as ui
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

    def is_default(self) -> bool:
        return self._default

    def __ge__(self, other: _Role) -> bool:
        return self.position >= other.position


def _guild(*roles: _Role, top: int = 10, manage_roles: bool = True, channels: dict[int, Any] | None = None) -> Any:
    guild = SimpleNamespace(
        id=1,
        me=SimpleNamespace(top_role=_Role(999, top), guild_permissions=SimpleNamespace(manage_roles=manage_roles)),
        get_role={r.id: r for r in roles}.get,
        get_channel=(channels or {}).get,
        channels=list((channels or {}).values()),
    )
    for role in roles:
        role.guild = guild
    return guild


def _text_channel(channel_id: int, *, can_send: bool = True) -> Any:
    channel = MagicMock(spec=discord.TextChannel, id=channel_id)
    channel.name = f"ch-{channel_id}"
    channel.permissions_for.return_value = SimpleNamespace(send_messages=can_send, embed_links=can_send)
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

    assert result == {"permission_sync": True}
    schedule.assert_called_once()


async def test_moving_the_channel_retires_the_old_card(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "_schedule_permission_sync", MagicMock(return_value=False))
    old_card = MagicMock(delete=AsyncMock())
    sentinel = _sentinel(channel_id=8, message_id=5, message=old_card)
    guild = _guild(channels={9: _text_channel(9)})

    await api._apply_sentinel_settings(guild, _bot(sentinel), {"channel_id": 9})

    sentinel.edit.assert_awaited_once_with(channel_id=9, message_id=None)
    old_card.delete.assert_awaited_once()


async def test_a_channel_percy_cannot_post_in_is_rejected() -> None:
    guild = _guild(channels={9: _text_channel(9, can_send=False)})

    with pytest.raises(HTTPException) as exc:
        await api._apply_sentinel_settings(guild, _bot(_sentinel()), {"channel_id": 9})
    assert "Send Messages" in exc.value.detail


async def test_batch_sentinel_op_validates_like_patch() -> None:
    body = api.BatchGuildConfigBody.model_validate({"operations": [{"type": "sentinel", "data": {"rate": "abc"}}]})

    with pytest.raises(HTTPException) as exc:
        await api.batch_guild_config(_guild(), _bot(_sentinel()), body)  # type: ignore[arg-type]
    assert exc.value.status_code == 400


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
