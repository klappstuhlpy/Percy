from __future__ import annotations

import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cogs.moderation.member_actions import MemberActionError, apply_member_action
from app.services.guild.notifications import ACTION_PERMISSIONS, REQUESTABLE_ACTIONS
from app.services.moderation.restrictions import MAX_NICK_LENGTH, RESTRICTION_ACTIONS, is_current, normalize_nickname


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


def _bot() -> MagicMock:
    bot = MagicMock()
    bot.db.get_guild_config = AsyncMock(
        return_value=SimpleNamespace(mod_require_reason=False, mod_default_ban_delete_days=0, mod_dm_on_action=False)
    )
    return bot


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
        await apply_member_action(_bot(), guild, mod, 2, action='voicekick')  # type: ignore[arg-type]
    assert exc.value.status == 400


async def test_voicekick_disconnects_and_records_a_case() -> None:
    member = _member()
    member.voice = SimpleNamespace()
    member.move_to = AsyncMock()
    bot = _bot()
    mod = SimpleNamespace(id=1, top_role=10, guild_permissions=SimpleNamespace(administrator=True))
    guild = _guild(member)
    result = await apply_member_action(bot, guild, mod, 2, action='voicekick', reason='r')  # type: ignore[arg-type]
    member.move_to.assert_awaited_once_with(None, reason='r')
    bot.dispatch.assert_called_once_with('mod_action', guild.id, 'voicekick', 2, 1, 'r')
    assert result['action'] == 'voicekick'


async def test_nickfreeze_requires_a_name_and_persists_it() -> None:
    member = _member()
    member.edit = AsyncMock()
    bot = _bot()
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
        await apply_member_action(_bot(), guild, mod, 2, action='channelmute')  # type: ignore[arg-type]
    assert exc.value.status == 400


async def test_voiceban_duration_is_bounded() -> None:
    mod = SimpleNamespace(id=1, top_role=10, guild_permissions=SimpleNamespace(administrator=True))
    with pytest.raises(MemberActionError):
        await apply_member_action(_bot(), _guild(_member()), mod, 2, action='voiceban', duration=5)  # type: ignore[arg-type]


class _FakeDB:
    """Stands in for ``Database``: records what the real repository sends to the base query helpers."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[Any, ...]] = []

    async def fetch(self, query: str, *args: Any, timeout: float | None = None) -> list[dict[str, Any]]:
        assert isinstance(query, str), 'BaseRepository.fetch must receive SQL first'
        self.calls.append(args)
        return self.rows

    async def fetchrow(self, query: str, *args: Any, timeout: float | None = None) -> None:
        assert isinstance(query, str)
        return None


async def test_repository_for_member_and_get_reach_the_base_helpers() -> None:
    from app.database.repositories.restrictions import RestrictionsRepository

    db = _FakeDB([{'kind': 'nickfreeze'}])
    repo = RestrictionsRepository(db)  # type: ignore[arg-type]
    assert await repo.for_member(1, 2) == [{'kind': 'nickfreeze'}]
    assert await repo.get(1, 2, 'voiceban') is None
    assert db.calls == [(1, 2)]


async def test_reapply_restores_a_frozen_nick_through_the_real_repository() -> None:
    from app.cogs.moderation.restriction_actions import reapply
    from app.database.repositories.restrictions import RestrictionsRepository

    row = {'kind': 'nickfreeze', 'value': 'Bob', 'expires_at': None, 'channel_id': 0}
    bot = _bot()
    bot.db.restrictions = RestrictionsRepository(_FakeDB([row]))  # type: ignore[arg-type]
    member = SimpleNamespace(id=2, guild=SimpleNamespace(id=1), edit=AsyncMock())
    await reapply(bot, member)  # type: ignore[arg-type]
    member.edit.assert_awaited_once()
    assert member.edit.await_args.kwargs['nick'] == 'Bob'


async def test_reapply_gives_back_sticky_roles_it_can_assign() -> None:
    from app.cogs.moderation.restriction_actions import reapply
    from app.database.repositories.restrictions import RestrictionsRepository

    class Role:
        def __init__(self, role_id: int, position: int, managed: bool = False) -> None:
            self.id, self.position, self.managed = role_id, position, managed

        def is_default(self) -> bool:
            return False

        def __lt__(self, other: Role) -> bool:
            return self.position < other.position

    roles = [Role(10, 1), Role(11, 2, managed=True), Role(12, 50)]  # 11 is an integration's, 12 outranks Percy
    guild = SimpleNamespace(id=1, roles=roles, me=SimpleNamespace(top_role=Role(99, 10)))
    rows = [{'kind': 'stickyrole', 'value': None, 'expires_at': None, 'channel_id': r} for r in (10, 11, 12, 13)]
    bot = _bot()
    bot.db.restrictions = RestrictionsRepository(_FakeDB(rows))  # type: ignore[arg-type]
    member = SimpleNamespace(id=2, guild=guild, roles=[], add_roles=AsyncMock())
    await reapply(bot, member)  # type: ignore[arg-type]
    member.add_roles.assert_awaited_once()
    assert [r.id for r in member.add_roles.await_args.args] == [10]


async def test_nickfreeze_persists_before_editing_and_nick_follows_the_freeze() -> None:
    from app.cogs.moderation import restriction_actions as actions

    order: list[str] = []
    member = _member()
    member.guild = SimpleNamespace(id=7)
    member.edit = AsyncMock(side_effect=lambda **kw: order.append('edit'))
    bot = _bot()
    bot.db.restrictions.upsert = AsyncMock(side_effect=lambda *a, **kw: order.append('upsert'))
    await actions.apply_nickfreeze(bot, member, 'Bob', None)  # type: ignore[arg-type]
    assert order == ['upsert', 'edit']

    order.clear()
    bot.db.restrictions.get = AsyncMock(return_value={'value': 'Bob'})
    await actions.apply_nick(bot, member, 'Al', None)  # type: ignore[arg-type]
    assert order == ['upsert', 'edit']
    bot.db.restrictions.upsert.assert_awaited_with(7, 2, 'nickfreeze', value='Al')
    with pytest.raises(MemberActionError):
        await actions.apply_nick(bot, member, ' ', None)  # type: ignore[arg-type]
