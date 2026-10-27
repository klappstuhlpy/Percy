"""Regression tests for the custom flag parser (app/core/flags.py)."""

from __future__ import annotations

import inspect
import types

from discord.ext.commands.view import StringView

from app.core.command import Command
from app.core.flags import ConsumeUntilFlag, Flags, flag, store_true


class AuditFlags(Flags):
    reason: str = flag(short='r')
    amount: int = flag(short='a', default=1)
    silent: bool = store_true(short='s')
    tags: list[str] = flag(default=None)


def _ctx() -> types.SimpleNamespace:
    param = inspect.Parameter('flags', inspect.Parameter.KEYWORD_ONLY, annotation=AuditFlags)
    return types.SimpleNamespace(command=types.SimpleNamespace(custom_flags=AuditFlags), current_parameter=param)


async def _parse(text: str) -> dict:
    return dict(await AuditFlags.convert(_ctx(), text))  # type: ignore[arg-type]


async def test_lone_dash_is_text() -> None:
    assert not AuditFlags.is_flag_starter('-')
    assert (await _parse('--reason a - b'))['reason'] == 'a - b'


async def test_quoted_value_protects_flag_like_text() -> None:
    ns = await _parse('--reason "see --amount 5"')
    assert ns['reason'] == 'see --amount 5'
    assert ns['amount'] == 1


async def test_equals_syntax_takes_whole_value() -> None:
    assert (await _parse('--reason=foo bar'))['reason'] == 'foo bar'


async def test_long_flag_is_case_insensitive() -> None:
    assert (await _parse('--REASON caps'))['reason'] == 'caps'


async def test_existing_behaviour_kept() -> None:
    ns = await _parse('-sa 3 --tags a b "c d"')
    assert ns['silent'] is True
    assert ns['amount'] == 3
    assert ns['tags'] == ['a', 'b', 'c d']
    assert (await _parse('--amount -5'))['amount'] == -5


async def test_consume_until_flag_stops_at_first_flag_not_last_repeat() -> None:
    async def callback(ctx, *, text: str, flags: AuditFlags) -> None: ...

    cmd = Command(callback)
    cmd.custom_flags = AuditFlags  # type: ignore[assignment]

    view = StringView('spam --reason spam')
    word = view.get_quoted_word()
    view.previous = 0  # discord.py's transform() records where the argument started
    ctx = types.SimpleNamespace(command=cmd, view=view, current_parameter=None)

    result = await ConsumeUntilFlag(None).convert(ctx, word or '')  # type: ignore[arg-type]

    assert result == 'spam'
    # The remaining view must still hold the flags, not jump past the repeated "spam".
    assert view.read_rest().strip() == '--reason spam'
