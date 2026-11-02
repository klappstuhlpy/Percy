from __future__ import annotations

import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cogs.moderation.member_actions import MemberActionError, apply_member_action
from app.services.notifications import ACTION_PERMISSIONS, REQUESTABLE_ACTIONS
from app.services.restrictions import MAX_NICK_LENGTH, RESTRICTION_ACTIONS, is_current, normalize_nickname


@pytest.mark.parametrize(('raw', 'expected'), [('  Bob ', 'Bob'), ('', None), ('   ', None), (None, None)])
def test_normalize_nickname(raw: str | None, expected: str | None) -> None:
    assert normalize_nickname(raw) == expected


def test_normalize_nickname_rejects_too_long() -> None:
    assert normalize_nickname('x' * MAX_NICK_LENGTH) is not None
    with pytest.raises(ValueError):
        normalize_nickname('x' * (MAX_NICK_LENGTH + 1))


def test_is_current_only_for_the_timer_that_owns_the_row() -> None:
    now = datetime.datetime(2026, 1, 1, 12, 0)
    assert is_current(now, now + datetime.timedelta(seconds=1))
    assert not is_current(now + datetime.timedelta(hours=1), now)  # re-applied with a later expiry
    assert not is_current(None, now)  # made permanent since
    aware = now.replace(tzinfo=datetime.UTC)
    assert is_current(now, aware)  # naive stored vs aware in-memory timer


def test_every_restriction_action_is_gated_and_none_requestable() -> None:
    assert set(ACTION_PERMISSIONS) >= RESTRICTION_ACTIONS
    assert not RESTRICTION_ACTIONS & REQUESTABLE_ACTIONS


def _guild(member: SimpleNamespace) -> MagicMock:
    guild = MagicMock()
    guild.owner_id = 999
    guild.get_member.return_value = member
    guild.me = SimpleNamespace(id=50, top_role=100, guild=guild)
    return guild


def _member(**kw: Any) -> SimpleNamespace:
    return SimpleNamespace(
        id=2, top_role=1, bot=False, voice=None, guild_permissions=SimpleNamespace(administrator=False), **kw
    )


async def test_voicekick_requires_voice() -> None:
    guild = _guild(_member())
    mod = SimpleNamespace(id=1, top_role=10, guild_permissions=SimpleNamespace(administrator=True))
    with pytest.raises(MemberActionError) as exc:
        await apply_member_action(MagicMock(), guild, mod, 2, action='voicekick')  # type: ignore[arg-type]
    assert exc.value.status == 400


async def test_voicekick_disconnects_and_records_a_case() -> None:
    member = _member()
    member.voice = SimpleNamespace()
    member.move_to = AsyncMock()
    bot = MagicMock()
    mod = SimpleNamespace(id=1, top_role=10, guild_permissions=SimpleNamespace(administrator=True))
    guild = _guild(member)
    result = await apply_member_action(bot, guild, mod, 2, action='voicekick', reason='r')  # type: ignore[arg-type]
    member.move_to.assert_awaited_once_with(None, reason='r')
    bot.dispatch.assert_called_once_with('mod_action', guild.id, 'voicekick', 2, 1, 'r')
    assert result['action'] == 'voicekick'


async def test_nickfreeze_requires_a_name_and_persists_it() -> None:
    member = _member()
    member.edit = AsyncMock()
    bot = MagicMock()
    bot.db.restrictions.upsert = AsyncMock()
    mod = SimpleNamespace(id=1, top_role=10, guild_permissions=SimpleNamespace(administrator=True))
    guild = _guild(member)
    member.guild = guild
    with pytest.raises(MemberActionError):
        await apply_member_action(bot, guild, mod, 2, action='nickfreeze', nickname=' ')  # type: ignore[arg-type]
    await apply_member_action(bot, guild, mod, 2, action='nickfreeze', nickname=' Bob ', reason='r')  # type: ignore[arg-type]
    member.edit.assert_awaited_once_with(nick='Bob', reason='r')
    bot.db.restrictions.upsert.assert_awaited_once_with(guild.id, 2, 'nickfreeze', value='Bob')


async def test_channelmute_needs_a_channel() -> None:
    guild = _guild(_member())
    guild.get_channel.return_value = None
    mod = SimpleNamespace(id=1, top_role=10, guild_permissions=SimpleNamespace(administrator=True))
    with pytest.raises(MemberActionError) as exc:
        await apply_member_action(MagicMock(), guild, mod, 2, action='channelmute')  # type: ignore[arg-type]
    assert exc.value.status == 400


async def test_voiceban_duration_is_bounded() -> None:
    mod = SimpleNamespace(id=1, top_role=10, guild_permissions=SimpleNamespace(administrator=True))
    with pytest.raises(MemberActionError):
        await apply_member_action(MagicMock(), _guild(_member()), mod, 2, action='voiceban', duration=5)  # type: ignore[arg-type]
