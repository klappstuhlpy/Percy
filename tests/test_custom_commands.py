"""Custom commands and aliases: signature parser, binder, renderer, alias expansion, rules, dispatch, API."""

from __future__ import annotations

import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.services.custom_commands.aliases import expand, first_word
from app.services.custom_commands.models import (
    ADMINISTRATOR,
    CommandAlias,
    CustomCommand,
    CustomCommandIndex,
    alias_problem,
    check_name,
    gate_passes,
    parse_cooldown,
    validate_command,
)
from app.services.custom_commands.signature import Param, SignatureError, bind, format_signature, parse_signature
from app.services.custom_commands.template import render

# -- signature ------------------------------------------------------------


def test_parses_every_syntax_and_round_trips() -> None:
    text = '<who:member> [times:int=2] [reason:str...] --target:channel --silent'
    params = parse_signature(text)
    assert params == (
        Param('who', 'member', 'required'),
        Param('times', 'int', 'optional', '2'),
        Param('reason', 'str', 'optional', greedy=True),
        Param('target', 'channel', 'flag'),
        Param('silent', 'bool', 'switch'),
    )
    assert format_signature(params) == text
    assert parse_signature(format_signature(params)) == params


def test_type_defaults_to_str_and_names_lowercase() -> None:
    assert parse_signature('<Name>') == (Param('name', 'str', 'required'),)
    assert parse_signature('') == ()
    assert parse_signature('[note:str=hello there]')[0].default == 'hello there'


@pytest.mark.parametrize(
    ('text', 'needle'),
    [
        ('<x:float>', 'Unknown type'),
        ('<x:int> <x:str>', 'used twice'),
        ('[a:str...] [b:str]', 'last argument'),
        ('[a:str] <b:str>', 'cannot come after'),
        ('[n:int=abc]', 'not a valid int'),
        ('[m:member=@someone]', 'cannot have a default'),
        ('<author:str>', 'built-in placeholder'),
        ('<1abc:str>', 'names are'),
        ('<x:str> oops', 'Could not read'),
        ('<x:str', 'Could not read'),
        ('x' * 501, 'at most 500'),
        (' '.join(f'<a{i}:str>' for i in range(11)), 'at most 10'),
    ],
)
def test_signature_errors_are_actionable(text: str, needle: str) -> None:
    with pytest.raises(SignatureError, match=needle):
        parse_signature(text)


def test_bind_positionals_defaults_greedy_and_flags() -> None:
    params = parse_signature('<who:member> [times:int=2] [reason:str...] --target:channel --silent')
    assert bind(params, ['@a', '3', 'very', 'rude', '--silent', '--target', '#x']) == {
        'who': '@a', 'times': '3', 'reason': 'very rude', 'target': '#x', 'silent': True
    }
    assert bind(params, ['@a']) == {'who': '@a', 'times': '2', 'reason': None, 'target': None, 'silent': False}


def test_bind_errors_and_extras() -> None:
    params = parse_signature('<who:str> --target:channel')
    with pytest.raises(SignatureError, match='Missing the required argument `<who:str>`'):
        bind(params, [])
    with pytest.raises(SignatureError, match='needs a channel'):
        bind(params, ['a', '--target'])
    assert bind(params, ['a', 'b', '--unknown'])['who'] == 'a'  # extras stay reachable via {args}/{0}..


# -- template -------------------------------------------------------------

VALUES = {'author': 'Ann', 'author.id': '1', 'who': 'Bob', 'who.mention': '<@2>', 'uses': '7', '0': 'x'}


def test_known_placeholders_render() -> None:
    assert render('{author} hugs {who.mention} ({uses}) {0}', VALUES) == 'Ann hugs <@2> (7) x'


@pytest.mark.parametrize(
    'template',
    ['{author.__class__}', '{author._state.http.token}', '{who.__init__.__globals__}', '{nope}', '{author!r}', '{0:>10}'],
)
def test_unknown_and_injection_attempts_stay_literal(template: str) -> None:
    assert render(template, VALUES) == template


def test_braces_escape_and_values_are_not_rescanned() -> None:
    assert render('{{author}}', VALUES) == '{author}'
    assert render('{who}', {'who': '{uses} {author.__class__}', 'uses': '9'}) == '{uses} {author.__class__}'


def test_choice_and_random() -> None:
    rng = random.Random(1)
    assert render('{choice:a|b|c}', {}, rng=rng) in {'a', 'b', 'c'}
    assert 1 <= int(render('{random:1-6}', {}, rng=rng)) <= 6
    assert -3 <= int(render('{random:5--3}', {}, rng=rng)) <= 5  # reversed bounds swap
    assert render('{random:1-99999999999}', {}) == '{random:1-99999999999}'
    assert render('{random:a-b}', {}) == '{random:a-b}'


def test_output_capped() -> None:
    assert len(render('{author}' * 1000, {'author': 'abc'})) == 2000


# -- aliases --------------------------------------------------------------


def test_alias_expansion() -> None:
    assert expand('ban {0} spamming', '@x extra', ['@x', 'extra']) == 'ban @x spamming'
    assert expand('say {args}', 'hi there', ['hi', 'there']) == 'say hi there'
    assert expand('ban', '@x spam', ['@x', 'spam']) == 'ban @x spam'  # appended without placeholders
    assert expand('ban {0} {3}', 'a', ['a']) == 'ban a'
    assert expand('tag {0}', '"two words"', ['two words']) == 'tag "two words"'
    assert first_word('  Ban @x') == 'ban' and first_word('') == ''


# -- rules ----------------------------------------------------------------


def cc(id_: int = 1, name: str = 'hug', **kw: object) -> CustomCommand:
    return CustomCommand(id=id_, guild_id=10, name=name, **{'responses': ('hi',), **kw})  # type: ignore[arg-type]


INDEX = CustomCommandIndex.build([cc(1, 'hug', aliases=('cuddle',)), cc(2, 'pat', aliases=('hug',))], [CommandAlias(10, 'sb', 'ban')])
REAL = {'ban', 'help', 'h'}


def test_index_name_beats_other_alias() -> None:
    assert INDEX.lookup['hug'].id == 1 and INDEX.lookup['cuddle'].id == 1


@pytest.mark.parametrize('name', ['', 'Upper', 'has space', 'x' * 33, 'emoji\N{GRINNING FACE}'])
def test_bad_names(name: str) -> None:
    assert check_name(name)


def test_validate_command_normalises_and_rejects_shadowing() -> None:
    out = validate_command(
        {'name': 'wave', 'aliases': ['Hi', 'hi', 'wave'], 'responses': ['o/'], 'signature': '<x>'}, INDEX, REAL
    )
    assert out['aliases'] == ['hi'] and out['signature'] == '<x:str>'
    for name, needle in (('ban', 'built-in'), ('h', 'built-in'), ('cuddle', 'custom command `hug`'), ('sb', 'alias')):
        with pytest.raises(ValueError, match=needle):
            validate_command({'name': name, 'responses': ['x']}, INDEX, REAL)
    # editing a command may keep its own name/aliases
    assert validate_command({'name': 'hug', 'aliases': ['cuddle'], 'responses': ['x']}, INDEX, REAL, command_id=1)
    with pytest.raises(ValueError, match='at least one response'):
        validate_command({'name': 'new', 'responses': []}, INDEX, REAL)
    with pytest.raises(ValueError, match='both a rate and a period'):
        validate_command({'name': 'new', 'responses': ['x'], 'cooldown_rate': 1}, INDEX, REAL)
    with pytest.raises(ValueError, match='Unknown type'):
        validate_command({'name': 'new', 'responses': ['x'], 'signature': '<a:float>'}, INDEX, REAL)


def test_parse_cooldown() -> None:
    assert parse_cooldown('3/10') == (3, 10.0)
    for bad in ('3', 'a/b', '0/10', '3/0', '101/5'):
        with pytest.raises(ValueError):
            parse_cooldown(bad)


def test_gate() -> None:
    ban = 1 << 2
    assert gate_passes(0, [], None, ())
    assert gate_passes(ADMINISTRATOR, [], ban, (5,))
    assert gate_passes(ban, [], ban, ())
    assert not gate_passes(0, [], ban, ())
    assert gate_passes(0, [5], ban, (5,))  # a listed role bypasses the permission
    assert not gate_passes(0, [6], None, (5,))


def test_alias_problem() -> None:
    assert alias_problem('spam', 'ban {0}', INDEX, REAL) is None
    assert alias_problem('greet', 'hug', INDEX, REAL) is None  # custom command target
    assert 'another alias' in (alias_problem('x', 'sb', INDEX, REAL) or '')
    assert 'not a command' in (alias_problem('x', 'nope', INDEX, REAL) or '')
    assert 'built-in' in (alias_problem('ban', 'help', INDEX, REAL) or '')
    assert alias_problem('sb', 'help', INDEX, REAL) is None  # replacing an alias keeps its name


# -- dispatch -------------------------------------------------------------


def _bot(index: CustomCommandIndex) -> MagicMock:
    bot = MagicMock()
    bot.all_commands = {'ban': object(), 'help': object()}
    bot.db.get_custom_command_index = AsyncMock(return_value=index)
    bot.db.get_command_rules = AsyncMock(return_value=())
    bot.db.custom_commands.bump_uses = AsyncMock(return_value=4)
    bot.spam_control.is_spam = AsyncMock(return_value=False)
    bot.can_run = AsyncMock(return_value=True)
    bot.invoke_matched = AsyncMock()
    return bot


def _ctx(content: str, word: str) -> MagicMock:
    import discord

    from app.core import Context

    guild = MagicMock(id=10, get_role=MagicMock(return_value=None))
    guild.name = 'G'
    author = MagicMock(spec=discord.Member, id=1, display_name='Ann', mention='<@1>', roles=[])
    author.name = 'ann'
    channel = MagicMock(id=3)
    channel.name = 'general'
    channel.permissions_for.return_value = MagicMock(value=0)
    ctx = MagicMock(spec=Context, guild=guild, author=author, channel=channel, prefix='!', invoked_with=word)
    ctx.message = MagicMock(content=content)
    ctx.clean_prefix = '!'
    return ctx


async def test_real_command_always_wins() -> None:
    from app.cogs.custom_commands import CustomCommands

    index = CustomCommandIndex.build([cc(1, 'ban')], [CommandAlias(10, 'help', 'ban')])
    cog = CustomCommands(_bot(index))
    assert await cog.try_dispatch(_ctx('!ban x', 'ban')) is False
    assert await cog.try_dispatch(_ctx('!help', 'help')) is False

    # Bot.process_commands only offers prefix misses to the custom dispatcher.
    from app.core.bot import Bot

    fake = SimpleNamespace(blacklist={}, _maybe_dispatch_custom=AsyncMock(return_value=True), invoke_matched=AsyncMock())
    fake.get_context = AsyncMock(return_value=SimpleNamespace(command=object(), author=SimpleNamespace(id=1), guild=None))
    await Bot.process_commands(fake, MagicMock())  # type: ignore[arg-type]
    fake._maybe_dispatch_custom.assert_not_awaited()
    fake.invoke_matched.assert_awaited_once()


async def test_custom_command_renders_and_sends() -> None:
    from app.cogs.custom_commands import CustomCommands

    hug = cc(1, 'hug', signature='<who:str> [n:int=2]', responses=('{author} hugs {who} x{n} #{uses} [{args}]',))
    bot = _bot(CustomCommandIndex.build([hug], []))
    ctx = _ctx('!hug Bob', 'hug')
    assert await CustomCommands(bot).try_dispatch(ctx) is True
    ctx.send.assert_awaited_once()
    assert ctx.send.await_args.args[0] == 'Ann hugs Bob x2 #4 [Bob]'
    assert ctx.send.await_args.kwargs['allowed_mentions'].everyone is False


async def test_custom_command_errors_show_usage_and_gate() -> None:
    from app.cogs.custom_commands import CustomCommands

    hug = cc(1, 'hug', signature='<n:int>')
    bot = _bot(CustomCommandIndex.build([hug, cc(2, 'mod', required_permissions=1 << 2)], []))
    ctx = _ctx('!hug abc', 'hug')
    await CustomCommands(bot).try_dispatch(ctx)
    assert 'Usage: `!hug <n:int>`' in ctx.send_error.await_args.args[0]
    ctx = _ctx('!mod', 'mod')
    await CustomCommands(bot).try_dispatch(ctx)
    assert 'not allowed' in ctx.send_error.await_args.args[0]
    ctx.send.assert_not_awaited()
    bot.can_run = AsyncMock(return_value=False)  # ignored / disabled / denied: silent
    ctx = _ctx('!hug 1', 'hug')
    await CustomCommands(bot).try_dispatch(ctx)
    ctx.send.assert_not_awaited()
    ctx.send_error.assert_not_awaited()


async def test_alias_redispatches_once() -> None:
    from app.cogs.custom_commands import CustomCommands

    bot = _bot(CustomCommandIndex.build([], [CommandAlias(10, 'sb', 'ban {0} spamming')]))
    new_ctx = SimpleNamespace(command=object())
    bot.get_context = AsyncMock(return_value=new_ctx)
    ctx = _ctx('!sb @x', 'sb')
    assert await CustomCommands(bot).try_dispatch(ctx) is True
    assert bot.get_context.await_args.args[0].content == '!ban @x spamming'
    bot.invoke_matched.assert_awaited_once()
    # depth 1: an alias reached from an alias is not expanded again
    assert await CustomCommands(bot).try_dispatch(_ctx('!sb', 'sb'), allow_alias=False) is True
    assert bot.get_context.await_count == 1


# -- API ------------------------------------------------------------------


def _api_bot(stored: list[CustomCommand]) -> MagicMock:
    bot = MagicMock()
    bot.all_commands = {'ban': object()}
    bot.db.get_custom_command_index = AsyncMock(return_value=CustomCommandIndex.build(stored, []))
    repo = bot.db.custom_commands
    repo.count_commands = AsyncMock(return_value=len(stored))
    repo.get_command = AsyncMock(side_effect=lambda g, i: next((c for c in stored if c.id == i), None))
    repo.create_command = AsyncMock(side_effect=lambda g, o, **v: cc(9, owner_id=o, **{**v, 'aliases': tuple(v['aliases'])}))
    repo.update_command = AsyncMock(side_effect=lambda g, i, **v: cc(i, **{**v, 'aliases': tuple(v['aliases'])}))
    repo.list_aliases = AsyncMock(return_value=[])
    repo.put_alias = AsyncMock()
    return bot


async def test_api_create_validates_and_shapes() -> None:
    from app.internal_api.routers import custom_commands as api

    guild = MagicMock(id=10, get_role=MagicMock(side_effect=lambda r: MagicMock() if r == 55 else None))
    bot = _api_bot([])
    body = api.CustomCommandBody(name='Hug', responses=['hi {who}'], signature='<who:member>', allowed_role_ids=['55'],
                                 required_permissions='4', owner_id='77')
    out = (await api.create_custom_command(guild, bot, body)).model_dump(mode='json')
    assert out['name'] == 'hug' and out['signature'] == '<who:member>'
    assert out['allowed_role_ids'] == ['55'] and out['required_permissions'] == '4' and out['owner_id'] == '77'
    for bad in (
        {'name': 'ban', 'responses': ['x']},
        {'name': 'ok', 'responses': ['x'], 'signature': '[a] <b>'},
        {'name': 'ok', 'responses': ['x'], 'allowed_role_ids': ['56']},
        {'name': 'ok', 'responses': ['x'], 'required_permissions': 'all'},
    ):
        with pytest.raises(HTTPException) as err:
            await api.create_custom_command(guild, bot, api.CustomCommandBody(**bad))
        assert err.value.status_code == 400


async def test_api_patch_merges_and_404s() -> None:
    from app.internal_api.routers import custom_commands as api

    guild = MagicMock(id=10)
    bot = _api_bot([cc(1, 'hug', signature='<a:str>')])
    out = await api.patch_custom_command(guild, bot, 1, api.PatchCustomCommandBody(cooldown_rate=2, cooldown_per=5))
    assert out.signature == '<a:str>' and out.cooldown_rate == 2
    with pytest.raises(HTTPException) as err:
        await api.patch_custom_command(guild, bot, 2, api.PatchCustomCommandBody(name='x'))
    assert err.value.status_code == 404
    with pytest.raises(HTTPException):
        await api.patch_custom_command(guild, bot, 1, api.PatchCustomCommandBody(responses=None))


async def test_api_alias_put() -> None:
    from app.internal_api.routers import custom_commands as api

    guild = MagicMock(id=10)
    bot = _api_bot([])
    await api.put_alias(guild, bot, 'SB', api.PutAliasBody(expansion='ban {0}'))
    bot.db.custom_commands.put_alias.assert_awaited_once_with(10, 'sb', 'ban {0}', None)
    with pytest.raises(HTTPException):
        await api.put_alias(guild, bot, 'x', api.PutAliasBody(expansion='nope'))


# -- management flags (Percy's Flags on a bare bot) -----------------------


async def test_cog_loads_and_flags_parse_even_without_text() -> None:
    import discord
    from discord.ext import commands
    from discord.ext.commands.view import StringView

    from app.core import Context

    bot = commands.Bot(command_prefix='!', intents=discord.Intents.none(), help_command=None)
    await bot.load_extension('app.cogs.custom_commands')
    for command in bot.walk_commands():
        command.transform_flag_parameters()  # type: ignore[attr-defined]  # Percy's Bot does this on add

    async def parse(name: str, text: str) -> tuple[list[object], dict[str, object]]:
        message = MagicMock(content=text)
        ctx = Context(prefix='!', view=StringView(text), bot=bot, message=message)  # type: ignore[arg-type]
        ctx.command = bot.get_command(name)
        await ctx.command._parse_arguments(ctx)  # type: ignore[union-attr]
        return ctx.args[2:], ctx.kwargs

    args, kwargs = await parse('customcommand create', 'hug {author} hugs {who} --args "<who:member> --loud" --cooldown 3/10')
    assert args == ['hug', '{author} hugs {who}']
    assert kwargs['flags'].args == '<who:member> --loud' and kwargs['flags'].cooldown == '3/10'
    # an optional response followed directly by a flag must not swallow the flag
    args, kwargs = await parse('customcommand edit', 'hug --keep')
    assert args == ['hug', None] and kwargs['flags'].keep is True
