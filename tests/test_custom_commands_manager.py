"""The interactive custom-command manager: pure text helpers, and the CV2 screens built without a bot."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ui.select import BaseSelect

from app.cogs.custom_commands.ui import CustomCommandManager, FormModal
from app.services.custom_commands.manager import (
    describe_cooldown,
    describe_signature,
    placeholder_help,
    placeholder_hint,
    sample_values,
)
from app.services.custom_commands.models import CommandAlias, CustomCommand, CustomCommandIndex
from app.services.custom_commands.signature import parse_signature
from app.services.custom_commands.template import render

MAX_COMPONENTS = 40
MAX_TEXT = 4000
ME = {'': 'Ann', 'id': '5', 'name': 'ann', 'mention': '<@5>'}


def cc(cid: int, name: str, **kw: object) -> CustomCommand:
    return CustomCommand(id=cid, guild_id=10, name=name, responses=kw.pop('responses', ('hi',)), **kw)  # type: ignore[arg-type]


# -- pure helpers -----------------------------------------------------------


def test_signature_described_in_words() -> None:
    lines = describe_signature(parse_signature('<who:member> [n:int=2] [why:str...] --times:int --loud'))
    assert lines == [
        '`who` - a server member, required',
        '`n` - a whole number, optional (default `2`)',
        '`why` - text (takes the rest of the message), optional',
        '`--times` - a whole number, optional, typed as `--times value`',
        '`--loud` - an on/off switch, on when `--loud` is typed',
    ]
    assert describe_signature(()) == []


def test_placeholder_hint_fits_and_leads_with_arguments() -> None:
    params = parse_signature('<who:member> [n:int]')
    hint = placeholder_hint(params)
    assert hint.startswith('Use {who} {n} {author}') and len(hint) <= 100
    long = parse_signature(' '.join(f'[a_very_long_argument_name_{i}:str]' for i in range(8)))
    assert len(placeholder_hint(long)) <= 100
    assert '`{who.mention}`' in placeholder_help(params) and '`{n}`' in placeholder_help(params)
    assert 'Your arguments' not in placeholder_help(())


def test_sample_values_render_every_placeholder() -> None:
    params = parse_signature('<who:member> <r:role> [n:int=4] --loud')
    values, tokens = sample_values(params, ME)
    assert tokens == ['<@5>', '@Sample Role', '4', '--loud']
    assert render('{who} {who.mention} {r.name} {n} {loud}', values) == 'Ann <@5> Sample Role 4 true'


def test_describe_cooldown() -> None:
    assert describe_cooldown(None, None) == 'none'
    assert describe_cooldown(1, 30.0) == '1 use per 30 seconds, per member'
    assert describe_cooldown(3, 10.5) == '3 uses per 10.5 seconds, per member'


# -- view construction -----------------------------------------------------


def _manager(commands: list[CustomCommand], *, can_manage: bool, aliases: list[CommandAlias] = ()) -> CustomCommandManager:  # type: ignore[assignment]
    index = CustomCommandIndex.build(commands, aliases)
    bot = MagicMock()
    bot.db.get_custom_command_index = AsyncMock(return_value=index)
    cog = SimpleNamespace(bot=bot, save=AsyncMock(), alias_problem=AsyncMock(return_value=None))
    author = SimpleNamespace(id=5)
    return CustomCommandManager(cog, author=author, guild_id=10, prefix='!', index=index, can_manage=can_manage)  # type: ignore[arg-type]


def _labels(view: discord.ui.LayoutView) -> set[str]:
    return {c.label for c in view.walk_children() if isinstance(c, discord.ui.Button) and c.label}


def _selects(view: discord.ui.LayoutView) -> list[str | None]:
    return [c.placeholder for c in view.walk_children() if isinstance(c, BaseSelect)]


def _text(view: discord.ui.LayoutView) -> str:
    return '\n'.join(c.content for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay))


def _assert_limits(view: discord.ui.LayoutView) -> None:
    assert view.total_children_count <= MAX_COMPONENTS
    assert view.content_length() <= MAX_TEXT
    view.to_components()


FULL = cc(
    1, 'hug', signature='<who:member> [n:int=2]', aliases=('cuddle',), responses=tuple('x' * 2000 for _ in range(20)),
    required_permissions=(1 << 13) | (1 << 30), allowed_role_ids=(7,), cooldown_rate=3, cooldown_per=10.0,
    owner_id=5, uses=12, created_at=datetime.datetime(2026, 1, 1),
)


@pytest.mark.parametrize('can_manage', [True, False])
async def test_list_screen(can_manage: bool) -> None:
    view = _manager([cc(i, f'c{i}', signature='<a:str>') for i in range(12)], can_manage=can_manage)
    await view.build()
    _assert_limits(view)
    assert ('New command' in _labels(view)) is can_manage
    assert {'Aliases', 'Close'} <= _labels(view)
    assert _selects(view) == ['Open a command...']
    assert 'Page 1/3' in _text(view) and '`!c0 <a:str>`' in _text(view)


async def test_empty_list_says_what_to_do() -> None:
    view = _manager([], can_manage=True)
    await view.build()
    assert 'New command' in _text(view) and _selects(view) == []
    view = _manager([], can_manage=False)
    await view.build()
    assert 'Manage Server' in _text(view)


@pytest.mark.parametrize('can_manage', [True, False])
async def test_detail_screen(can_manage: bool) -> None:
    view = _manager([FULL], can_manage=can_manage)
    view.selected, view.screen = FULL, 'detail'
    await view.build()
    _assert_limits(view)
    text = _text(view)
    assert '`who` - a server member, required' in text and '3 uses per 10 seconds' in text
    assert 'Manage Messages' in text and '<@&7>' in text and '`cuddle`' in text
    edit_buttons = {'Name & aliases', 'Arguments', 'Responses', 'Who can use it', 'Cooldown', 'Delete'}
    assert {'Test', 'Back'} <= _labels(view)
    assert (edit_buttons <= _labels(view)) is can_manage
    assert not (edit_buttons & _labels(view)) or can_manage


@pytest.mark.parametrize('screen', ['responses', 'permissions', 'delete', 'aliases'])
async def test_sub_screens_fit(screen: str) -> None:
    aliases = [CommandAlias(10, f'a{i}', 'ban {0} ' + 'y' * 1500) for i in range(30)]
    view = _manager([FULL], can_manage=True, aliases=aliases)
    view.selected, view.screen = FULL, screen
    view.notice = 'notice'
    await view.build()
    _assert_limits(view)
    assert 'Back' in _labels(view) or 'Cancel' in _labels(view)
    assert view.notice is None  # shown once


async def test_permission_screen_preselects_current_gate() -> None:
    view = _manager([FULL], can_manage=True)
    view.selected, view.screen = FULL, 'permissions'
    await view.build()
    perms, roles = (c for c in view.walk_children() if isinstance(c, BaseSelect))
    assert {o.value for o in perms.options if o.default} == {'manage_messages'}  # type: ignore[attr-defined]
    assert [v.id for v in roles.default_values] == [7]


async def test_aliases_screen_hides_edits_from_non_managers() -> None:
    view = _manager([], can_manage=False, aliases=[CommandAlias(10, 'sb', 'ban {0}')])
    view.screen = 'aliases'
    await view.build()
    assert _labels(view) == {'Back'} and _selects(view) == []


# -- interactions ---------------------------------------------------------


def _interaction(*, manage: bool = True) -> MagicMock:
    interaction = MagicMock()
    interaction.permissions = discord.Permissions(manage_guild=manage)
    interaction.response.is_done.return_value = True
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


async def test_rejected_edit_shows_error_and_keeps_draft_then_saves() -> None:
    view = _manager([FULL], can_manage=True)
    view.selected, view.screen = FULL, 'detail'
    opening = _interaction()
    await view.edit_signature(opening)
    modal: FormModal = opening.response.send_modal.await_args.args[0]
    assert modal.inputs['signature'].default == FULL.signature

    view.cog.save.side_effect = ValueError('Unknown type `nope` for `x`; use one of: str.')
    submit = _interaction()
    await modal._callback(submit, {'signature': '<x:nope>'})
    assert 'Unknown type `nope`' in _text(submit.edit_original_response.await_args.kwargs['view'])
    assert view.drafts['signature'] == {'signature': '<x:nope>'}

    reopen = _interaction()
    await view.edit_signature(reopen)
    modal = reopen.response.send_modal.await_args.args[0]
    assert modal.inputs['signature'].default == '<x:nope>'  # what they typed is kept

    view.cog.save.side_effect = None
    view.cog.save.return_value = cc(1, 'hug', signature='<x:int>')
    await modal._callback(_interaction(), {'signature': '<x:int>'})
    assert 'signature' not in view.drafts and view.selected.signature == '<x:int>'
    assert view.cog.save.await_args.kwargs == {'command_id': 1}
    assert view.cog.save.await_args.args[2]['signature'] == '<x:int>'


async def test_writes_need_manage_server_at_the_time() -> None:
    view = _manager([FULL], can_manage=True)
    view.selected = FULL
    interaction = _interaction(manage=False)
    await view.toggle_delete(interaction)
    view.cog.save.assert_not_awaited()
    assert 'Manage Server' in interaction.response.send_message.await_args.args[0]


async def test_test_button_renders_with_sample_values_ephemerally() -> None:
    view = _manager([], can_manage=False)
    view.selected = cc(1, 'hug', signature='<who:member>', responses=('{author} hugs {who.mention}',))
    view.cog.context_values = lambda *a: {'author': 'Ann'}
    interaction = _interaction()
    interaction.user = SimpleNamespace(display_name='Ann', id=5, name='ann', mention='<@5>')
    await view.test(interaction)
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs['ephemeral'] is True and 'Ann hugs <@5>' in _text(kwargs['view'])


async def test_create_wizard_lands_on_the_new_command() -> None:
    view = _manager([], can_manage=True)
    opening = _interaction()
    await view.new_command(opening)
    modal: FormModal = opening.response.send_modal.await_args.args[0]
    assert set(modal.inputs) == {'name', 'signature', 'response'}
    created = cc(3, 'hug', signature='<who:member>')
    view.cog.save.return_value = created
    submit = _interaction()
    await modal._callback(submit, {'name': ' Hug ', 'signature': '<who:member>', 'response': '{author} hugs {who}'})
    assert view.cog.save.await_args.args[2] == {'name': 'hug', 'signature': '<who:member>', 'responses': ['{author} hugs {who}']}
    assert view.cog.save.await_args.kwargs == {'command_id': None}
    assert view.screen == 'detail' and view.selected is created
    assert 'Created `!hug <who:member>`' in _text(submit.edit_original_response.await_args.kwargs['view'])
