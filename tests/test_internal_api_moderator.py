"""The dashboard's moderation endpoints check the acting moderator, not just the bearer token.

Handlers are called directly with small fakes: the permission each action needs, the shared
hierarchy rule, role limits, id/duration/timestamp validation, and that a refusal is a 4xx
raised before Discord is touched.
"""

from __future__ import annotations

import datetime
import functools
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from fastapi import HTTPException

from app.internal_api.routers import guild as guild_api
from app.internal_api.routers import members as api
from app.internal_api.routers import moderation as mod_api

OWNER, ME, MOD, TARGET = 1, 2, 3, 4


@functools.total_ordering
class _Role:
    def __init__(self, role_id: int, position: int, *, default: bool = False, managed: bool = False) -> None:
        self.id = role_id
        self.name = f'role{role_id}'
        self.position = position
        self.managed = managed
        self._default = default
        self.guild: Any = None

    def is_default(self) -> bool:
        return self._default

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Role) and self.position == other.position

    def __lt__(self, other: _Role) -> bool:
        return self.position < other.position

    def __hash__(self) -> int:
        return self.id


def _member(member_id: int, top: _Role, **perms: bool) -> Any:
    member = MagicMock()
    member.id = member_id
    member.top_role = top
    member.bot = False
    member.guild_permissions = discord.Permissions(**perms)
    member.timed_out_until = None
    for name in ('kick', 'ban', 'timeout', 'add_roles', 'remove_roles', 'send'):
        setattr(member, name, AsyncMock())
    member.get_role = MagicMock(return_value=None)
    return member


def _setup(*, mod_top: int = 5, target_top: int = 3, me_top: int = 10, **perms: bool) -> tuple[Any, Any, Any]:
    guild = MagicMock()
    guild.owner_id = OWNER
    roles = {pos: _Role(100 + pos, pos) for pos in range(12)}
    everyone = _Role(99, 0, default=True)
    managed = _Role(98, 2, managed=True)
    for role in (*roles.values(), everyone, managed):
        role.guild = guild
    guild.roles = [everyone, managed, *roles.values()]
    me = _member(ME, roles[me_top])
    me.guild = guild
    guild.me = me
    moderator = _member(MOD, roles[mod_top], **perms)
    target = _member(TARGET, roles[target_top])
    members = {ME: me, MOD: moderator, TARGET: target}
    guild.get_member = MagicMock(side_effect=members.get)
    guild.unban = AsyncMock()
    guild.fetch_member = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), 'unknown member'))
    return guild, moderator, target


def _bot() -> Any:
    timers = SimpleNamespace(delete_member_timer=AsyncMock(), create=AsyncMock())
    return SimpleNamespace(dispatch=MagicMock(), timers=timers, db=MagicMock())


async def _act(guild: Any, bot: Any, action: str, *, user_id: int = TARGET, **body: Any) -> dict:
    body.setdefault('moderator_id', str(MOD))
    return await api.member_action(user_id, api.MemberActionBody(action=action, **body), bot, guild)


def _status(exc: pytest.ExceptionInfo[HTTPException]) -> int:
    return exc.value.status_code


# -- permission matrix ----------------------------------------------------------


@pytest.mark.parametrize(('action', 'permission'), sorted(api.ACTION_PERMISSIONS.items()))
async def test_every_action_needs_its_permission(action: str, permission: str) -> None:
    guild, _, target = _setup()  # the moderator holds no permissions at all
    with pytest.raises(HTTPException) as exc:
        await _act(guild, _bot(), action)
    assert _status(exc) == 403
    assert permission.replace('_', ' ').title() in exc.value.detail
    target.kick.assert_not_awaited()
    target.ban.assert_not_awaited()
    guild.unban.assert_not_awaited()


async def test_manage_server_alone_cannot_ban() -> None:
    guild, _, target = _setup(manage_guild=True)
    with pytest.raises(HTTPException) as exc:
        await _act(guild, _bot(), 'ban')
    assert _status(exc) == 403
    target.ban.assert_not_awaited()


async def test_kick_with_permission_records_the_moderator() -> None:
    guild, _, target = _setup(kick_members=True)
    bot = _bot()
    result = await _act(guild, bot, 'kick', reason='spam')
    assert result['ok']
    target.kick.assert_awaited_once()
    bot.dispatch.assert_called_once_with('mod_action', guild.id, 'kick', TARGET, MOD, 'spam')


# -- hierarchy -------------------------------------------------------------------


async def test_target_ranked_above_the_moderator_is_refused() -> None:
    guild, _, target = _setup(mod_top=3, target_top=5, ban_members=True)
    with pytest.raises(HTTPException) as exc:
        await _act(guild, _bot(), 'ban')
    assert _status(exc) == 403
    assert 'equal to or higher than yours' in exc.value.detail
    target.ban.assert_not_awaited()


async def test_moderator_cannot_target_themselves() -> None:
    guild, _, _ = _setup(kick_members=True)
    with pytest.raises(HTTPException) as exc:
        await _act(guild, _bot(), 'kick', user_id=MOD)
    assert _status(exc) == 403


async def test_owner_outranks_everyone_but_percy_must_still_outrank_the_target() -> None:
    guild, moderator, target = _setup(mod_top=1, target_top=10)
    moderator.id = guild.owner_id = MOD
    moderator.guild_permissions = discord.Permissions.all()  # what discord.py reports for the owner
    with pytest.raises(HTTPException) as exc:
        await _act(guild, _bot(), 'kick')
    assert 'than mine' in exc.value.detail  # the owner passes; Percy (top 10) does not outrank the target
    target.kick.assert_not_awaited()


async def test_mute_does_not_need_percy_to_outrank_the_member() -> None:
    guild, _, target = _setup(mod_top=11, target_top=10, manage_roles=True)
    bot = _bot()
    mute_role = next(r for r in guild.roles if r.position == 4)
    bot.db.get_guild_config = AsyncMock(return_value=SimpleNamespace(mute_role_id=mute_role.id))
    guild.get_role = MagicMock(return_value=mute_role)
    result = await _act(guild, bot, 'mute')
    assert result['action'] == 'mute'
    target.add_roles.assert_awaited_once()


# -- moderator_id ----------------------------------------------------------------


@pytest.mark.parametrize(('moderator_id', 'status'), [(None, 400), ('', 400), ('abc', 400), ('-5', 400), ('77', 403)])
async def test_missing_malformed_or_unknown_moderator_id(moderator_id: Any, status: int) -> None:
    guild, _, target = _setup(kick_members=True)
    with pytest.raises(HTTPException) as exc:
        await _act(guild, _bot(), 'kick', moderator_id=moderator_id)
    assert _status(exc) == status
    target.kick.assert_not_awaited()


async def test_discord_forbidden_is_a_403_not_a_500() -> None:
    guild, _, target = _setup(kick_members=True)
    target.kick.side_effect = discord.Forbidden(MagicMock(status=403, reason='Forbidden'), 'Missing Permissions')
    with pytest.raises(HTTPException) as exc:
        await _act(guild, _bot(), 'kick')
    assert _status(exc) == 403


# -- mute duration ---------------------------------------------------------------


@pytest.mark.parametrize('duration', [-60, 0, 59, 10**12])
async def test_out_of_range_mute_duration_is_refused_before_the_role_goes_on(duration: int) -> None:
    guild, _, target = _setup(manage_roles=True)
    with pytest.raises(HTTPException) as exc:
        await _act(guild, _bot(), 'mute', duration=duration)
    assert _status(exc) == 400
    target.add_roles.assert_not_awaited()


# -- purge / unban without a member ----------------------------------------------


async def test_purge_works_for_a_user_who_left(monkeypatch: pytest.MonkeyPatch) -> None:
    guild, _, _ = _setup(manage_messages=True)
    purge = AsyncMock(return_value=3)
    monkeypatch.setattr(api, '_purge_member_messages', purge)
    result = await _act(guild, _bot(), 'purge', user_id=555)
    assert result['deleted'] == 3


# -- roles PATCH -----------------------------------------------------------------


async def _roles(guild: Any, *, add: list[str], moderator_id: Any = MOD) -> dict:
    body = api.MemberRolesBody(add=add, moderator_id=moderator_id)
    return await api.member_roles(TARGET, body, guild)


def _role_at(guild: Any, position: int) -> _Role:
    return next(r for r in guild.roles if r.position == position and not r.is_default() and not r.managed)


async def test_role_above_the_moderators_top_role_is_refused() -> None:
    guild, _, target = _setup(mod_top=5, manage_roles=True)
    with pytest.raises(HTTPException) as exc:
        await _roles(guild, add=[str(_role_at(guild, 7).id)])
    assert _status(exc) == 403
    assert 'your highest role' in exc.value.detail
    target.add_roles.assert_not_awaited()


async def test_role_below_the_moderator_is_given() -> None:
    guild, _, target = _setup(mod_top=5, manage_roles=True)
    await _roles(guild, add=[str(_role_at(guild, 4).id)])
    target.add_roles.assert_awaited_once()


async def test_roles_need_manage_roles() -> None:
    guild, _, _ = _setup(manage_guild=True)
    with pytest.raises(HTTPException) as exc:
        await _roles(guild, add=[str(_role_at(guild, 1).id)])
    assert _status(exc) == 403


@pytest.mark.parametrize('which', ['managed', 'default', 'above_percy'])
async def test_roles_percy_cannot_hand_out_are_refused_without_a_moderator(which: str) -> None:
    guild, _, target = _setup()
    role = {
        'managed': next(r for r in guild.roles if r.managed),
        'default': next(r for r in guild.roles if r.is_default()),
        'above_percy': _role_at(guild, 11),
    }[which]
    with pytest.raises(HTTPException) as exc:
        await _roles(guild, add=[str(role.id)], moderator_id=None)
    assert _status(exc) == 403
    target.add_roles.assert_not_awaited()


# -- bulk ------------------------------------------------------------------------


async def test_bulk_malformed_id_is_refused_before_anyone_is_actioned() -> None:
    guild, _, target = _setup(ban_members=True)
    body = mod_api.BulkActionBody(user_ids=[str(TARGET), 'abc'], action='ban', moderator_id=str(MOD))
    with pytest.raises(HTTPException) as exc:
        await mod_api.bulk_member_action(_bot(), guild, body)
    assert _status(exc) == 400
    target.ban.assert_not_awaited()


async def test_bulk_requires_the_permission_and_skips_members_above_the_moderator() -> None:
    guild, _, target = _setup(mod_top=3, target_top=5, kick_members=True)
    with pytest.raises(HTTPException):
        await mod_api.bulk_member_action(
            _bot(), guild, mod_api.BulkActionBody(user_ids=[TARGET], action='ban', moderator_id=MOD)
        )
    result = await mod_api.bulk_member_action(
        _bot(), guild, mod_api.BulkActionBody(user_ids=[TARGET], action='kick', moderator_id=MOD)
    )
    assert result['successes'] == 0 and 'yours' in result['failures'][0]['error']
    target.kick.assert_not_awaited()


# -- cases -----------------------------------------------------------------------


async def test_recent_cases_accepts_a_z_timestamp_as_naive_utc() -> None:
    guild, _, _ = _setup()
    bot = _bot()
    bot.db.cases.get_recent_cases = AsyncMock(return_value=[])
    await mod_api.get_recent_cases(bot, guild, since='2026-10-20T12:00:00.000Z')
    since = bot.db.cases.get_recent_cases.await_args.kwargs['since']
    assert since == datetime.datetime(2026, 10, 20, 12, 0) and since.tzinfo is None


async def test_recent_cases_malformed_since_is_a_400() -> None:
    guild, _, _ = _setup()
    with pytest.raises(HTTPException) as exc:
        await mod_api.get_recent_cases(_bot(), guild, since='yesterday')
    assert _status(exc) == 400


async def test_recent_cases_query_is_strictly_after_and_naive(mock_db: MagicMock) -> None:
    from app.database.repositories import CasesRepository  # after the routers: app.database alone is a cycle

    since = datetime.datetime(2026, 10, 20, 14, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
    await CasesRepository(mock_db).get_recent_cases(7, since=since)
    query, *params = mock_db.fetch.await_args.args
    assert 'created_at > $2' in query
    assert params == [7, datetime.datetime(2026, 10, 20, 12, 0)]


async def test_case_delete_with_a_moderator_needs_manage_server() -> None:
    guild, _, _ = _setup(ban_members=True)
    with pytest.raises(HTTPException) as exc:
        await mod_api.delete_case(1, _bot(), guild, moderator_id=str(MOD))
    assert _status(exc) == 403


# -- mention count ---------------------------------------------------------------


@pytest.mark.parametrize(('value', 'ok'), [(0, True), (3, True), (100, True), (2, False), (101, False), (2**40, False)])
def test_mention_count_bounds(value: int, ok: bool) -> None:
    if ok:
        assert guild_api._check_mention_count(value) == value
    else:
        with pytest.raises(HTTPException):
            guild_api._check_mention_count(value)


# -- task 4 polish ---------------------------------------------------------------


async def test_an_uncached_moderator_is_fetched_not_refused() -> None:
    guild, moderator, target = _setup(kick_members=True)
    guild.get_member = MagicMock(side_effect={ME: guild.me, TARGET: target}.get)  # moderator not cached
    guild.fetch_member = AsyncMock(return_value=moderator)
    assert (await _act(guild, _bot(), 'kick'))['ok']
    guild.fetch_member.assert_awaited_once_with(MOD)


async def test_purging_your_own_messages_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    guild, _, _ = _setup(manage_messages=True)
    monkeypatch.setattr(api, '_purge_member_messages', AsyncMock(return_value=2))
    result = await _act(guild, _bot(), 'purge', user_id=MOD)
    assert result['deleted'] == 2


async def test_timing_out_an_administrator_says_why() -> None:
    guild, _, target = _setup(moderate_members=True)
    target.guild_permissions = discord.Permissions(administrator=True)
    with pytest.raises(HTTPException) as exc:
        await _act(guild, _bot(), 'timeout', duration=3600)
    assert _status(exc) == 400
    assert 'administrators' in exc.value.detail
    target.timeout.assert_not_awaited()


@pytest.mark.parametrize('bad', ['abc', '0', '999999999999'])
async def test_roles_patch_rejects_malformed_or_unknown_ids(bad: str) -> None:
    guild, _, target = _setup(manage_roles=True)
    with pytest.raises(HTTPException) as exc:
        await _roles(guild, add=[str(_role_at(guild, 1).id), bad])
    assert _status(exc) == 400
    target.add_roles.assert_not_awaited()


async def test_a_bad_config_value_in_a_batch_applies_nothing() -> None:
    guild = MagicMock()
    config = SimpleNamespace(flags=SimpleNamespace(value=0), update=AsyncMock())
    bot = SimpleNamespace(db=SimpleNamespace(get_guild_config=AsyncMock(return_value=config)))
    body = guild_api.BatchGuildConfigBody.model_validate(
        {'operations': [{'type': 'config', 'data': {'prefixes': ['!']}}, {'type': 'config', 'data': {'mention_count': 2}}]}
    )
    with pytest.raises(HTTPException) as exc:
        await guild_api.batch_guild_config(guild, bot, body)  # type: ignore[arg-type]
    assert _status(exc) == 400
    config.update.assert_not_awaited()
