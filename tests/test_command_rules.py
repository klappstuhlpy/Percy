"""Precedence table of the command-rule resolver (pure; owner bypass lives in the cog)."""

from __future__ import annotations

import pytest

from app.services.command_rules import Invocation, Rule, resolve, subjects_for

G, U, R_HI, R_LO, CH, CAT = 1, 10, 21, 22, 30, 40


def inv(name: str = 'tag create', cog: str | None = 'Tags') -> Invocation:
    return Invocation(name, U, G, cog, role_ids=[R_HI, R_LO], channel_id=CH, category_id=CAT)


def rule(subject: str, kind: str, target: int, allow: bool) -> Rule:
    return Rule(subject, kind, target, allow)


def test_no_rules_falls_through() -> None:
    d = resolve([], inv())
    assert d.allowed is None and d.rule is None


def test_subject_order() -> None:
    assert subjects_for('a b c', 'Cog') == ['a b c', 'a b', 'a', 'cog:Cog', '*']


@pytest.mark.parametrize(
    ('winner', 'loser'),
    [
        (('user', U), ('role', R_HI)),
        (('role', R_HI), ('channel', CH)),
        (('channel', CH), ('category', CAT)),
        (('category', CAT), ('guild', G)),
    ],
)
def test_target_layers_each_beat_the_next(winner: tuple[str, int], loser: tuple[str, int]) -> None:
    rules = [rule('tag create', *winner, True), rule('tag create', *loser, False)]
    assert resolve(rules, inv()).allowed is True
    rules = [rule('tag create', *winner, False), rule('tag create', *loser, True)]
    assert resolve(rules, inv()).allowed is False


def test_highest_role_wins() -> None:
    rules = [rule('tag create', 'role', R_LO, False), rule('tag create', 'role', R_HI, True)]
    assert resolve(rules, inv()).allowed is True


def test_unrelated_targets_ignored() -> None:
    assert resolve([rule('tag create', 'user', 999, False), rule('tag create', 'role', 998, False)], inv()).allowed is None


@pytest.mark.parametrize(
    ('stronger', 'weaker'),
    [('tag create', 'tag'), ('tag', 'cog:Tags'), ('cog:Tags', '*')],
)
def test_subject_specificity_beats_target_layer(stronger: str, weaker: str) -> None:
    # A guild-wide rule on the more specific subject beats a user rule on the less specific one.
    rules = [rule(stronger, 'guild', G, True), rule(weaker, 'user', U, False)]
    d = resolve(rules, inv())
    assert d.allowed is True and d.rule == rules[0]


def test_nearest_parent_group_wins() -> None:
    i = inv('a b c', 'X')
    rules = [rule('a', 'user', U, False), rule('a b', 'guild', G, True)]
    assert resolve(rules, i).allowed is True


def test_star_matches_everything_and_cog_other_cog_not() -> None:
    assert resolve([rule('*', 'guild', G, False)], inv()).allowed is False
    assert resolve([rule('cog:Other', 'guild', G, False)], inv()).allowed is None


def test_no_cog_custom_command() -> None:
    assert resolve([rule('hello', 'role', R_LO, True)], Invocation('hello', U, G, role_ids=[R_LO])).allowed is True


def test_missing_channel_and_category_ok() -> None:
    d = resolve([rule('ping', 'channel', CH, False)], Invocation('ping', U, G, role_ids=[]))
    assert d.allowed is None


# -- check_rules adapter + API ------------------------------------------------

from unittest.mock import AsyncMock, MagicMock

from app.core.permissions import check_rules
from app.internal_api.routers import command_rules as api
from app.services.custom_commands.models import CustomCommandIndex


def _member() -> tuple[MagicMock, MagicMock, MagicMock]:
    guild = MagicMock(id=G)
    low, high = MagicMock(id=R_LO, position=1), MagicMock(id=R_HI, position=5)
    member = MagicMock(id=U, roles=[low, high, MagicMock(id=G, position=0)])
    channel = MagicMock(id=CH, category_id=CAT, spec=['id', 'category_id'])
    return guild, member, channel


async def test_check_rules_adapter_orders_roles_and_resolves() -> None:
    guild, member, channel = _member()
    db = MagicMock(get_command_rules=AsyncMock(return_value=(rule('ping', 'role', R_HI, True), rule('ping', 'role', R_LO, False))))
    assert (await check_rules(db, guild, member, channel, 'ping', 'Meta')).allowed is True


async def test_check_rules_never_applies_to_config_cog() -> None:
    guild, member, channel = _member()
    db = MagicMock(get_command_rules=AsyncMock(return_value=(rule('*', 'guild', G, False),)))
    assert (await check_rules(db, guild, member, channel, 'config rule deny', 'Config')).allowed is None


async def test_api_roundtrip_and_validation() -> None:
    from fastapi import HTTPException

    guild = MagicMock(id=G)
    guild.get_role.side_effect = lambda i: MagicMock() if i == R_HI else None
    stored = [Rule('ping', 'role', R_HI, False)]
    bot = MagicMock()
    bot.get_command.side_effect = lambda n: MagicMock(qualified_name=n, cog_name='Meta') if n == 'ping' else None
    bot.db.command_rules.list_rules = AsyncMock(return_value=tuple(stored))
    bot.db.command_rules.set_rule = AsyncMock()
    bot.db.command_rules.remove_rule = AsyncMock(return_value=True)
    bot.db.get_custom_command_index = AsyncMock(return_value=CustomCommandIndex())

    body = api.PutCommandRuleBody(subject='ping', target_type='role', target_id=str(R_HI), allow=False)
    out = (await api.put_command_rule(guild, bot, body)).model_dump(mode='json')
    assert out == {'rules': [{'subject': 'ping', 'target_type': 'role', 'target_id': str(R_HI), 'allow': False}]}
    bot.db.command_rules.set_rule.assert_awaited_once_with(G, 'ping', 'role', R_HI, False)

    for bad in (
        {'subject': 'nope', 'target_type': 'role', 'target_id': str(R_HI)},
        {'subject': 'ping', 'target_type': 'role', 'target_id': '999'},
        {'subject': 'ping', 'target_type': 'weird', 'target_id': '1'},
        {'subject': 'ping', 'target_type': 'guild', 'target_id': '2'},
    ):
        with pytest.raises(HTTPException):
            await api.put_command_rule(guild, bot, api.PutCommandRuleBody(allow=True, **bad))

    await api.delete_command_rule(guild, bot, subject='ping', target_type='role', target_id=str(R_HI))
