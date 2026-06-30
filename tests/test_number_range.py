"""Tests for :class:`app.core.command.NumberRange` and the prefix-command Range rewrite.

These cover the two prefix-command shortcomings papered over by
:meth:`Command._normalize_range_param`:

* ``app_commands.Range`` (a ``RangeTransformer``) is unusable by discord.py's prefix converter on its
  own -- it must be rewritten into a real converter.
* ``float`` ranges should accept a decimal comma (``7,5``) as well as a dot (``7.5``).

The conversions run through discord.py's real :func:`run_converters`, so the tests exercise the same
path a prefix invocation takes -- only the ``Context`` is faked (the converters only touch
``ctx.current_parameter``).
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

import pytest
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Parameter, RangeError, run_converters

from app.core.command import Command, NumberRange


def _ctx() -> Any:
    """A minimal stand-in carrying the only attribute the Range converters read."""
    param = Parameter(name="value", kind=inspect.Parameter.POSITIONAL_OR_KEYWORD)
    return SimpleNamespace(current_parameter=param)


async def _convert(annotation: Any, value: str, *, default: Any = inspect.Parameter.empty) -> Any:
    """Rewrite a parameter the way :meth:`Command.transform` does, then run discord.py's converters."""
    param = Parameter(name="value", kind=inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation, default=default)
    param = Command._normalize_range_param(param)
    return await run_converters(_ctx(), param.converter, value, param)


async def test_app_commands_range_float_is_usable_in_prefix() -> None:
    # Before the fix this raised BadArgument because RangeTransformer is not a callable converter.
    assert await _convert(app_commands.Range[float, -0.25, 1.0], "0.5") == pytest.approx(0.5)


async def test_app_commands_range_int_is_usable_in_prefix() -> None:
    result = await _convert(app_commands.Range[int, 1, 15], "7")
    assert result == 7
    assert isinstance(result, int)


@pytest.mark.parametrize("raw", ["7,5", "7.5"])
async def test_decimal_comma_and_dot_both_parse(raw: str) -> None:
    assert await _convert(commands.Range[float, 1.0, 10.0], raw) == pytest.approx(7.5)


@pytest.mark.parametrize("raw", ["0,5", "0.5"])
async def test_app_range_float_accepts_comma_and_dot(raw: str) -> None:
    assert await _convert(app_commands.Range[float, -0.25, 1.0], raw) == pytest.approx(0.5)


async def test_optional_range_still_converts() -> None:
    assert await _convert(app_commands.Range[float, -0.25, 1.0] | None, "0,75", default=None) == pytest.approx(0.75)


async def test_out_of_range_raises_range_error() -> None:
    with pytest.raises(RangeError):
        await _convert(app_commands.Range[float, -0.25, 1.0], "5.0")


async def test_comma_value_out_of_range_raises_range_error() -> None:
    # "9,5" normalises to 9.5 which is above the max, so it should fail the bound check, not parsing.
    with pytest.raises(RangeError):
        await _convert(commands.Range[float, 1.0, 5.0], "9,5")


async def test_integer_range_is_not_comma_normalized() -> None:
    # Comma normalisation only applies to floats; an int range leaves "1,000" to fail conversion
    # rather than silently becoming 1000 or 1.0.
    with pytest.raises(commands.BadArgument):
        await _convert(commands.Range[int, 1, 5000], "1,000")


def test_from_converter_ignores_non_range() -> None:
    assert NumberRange.from_converter(str) is None
    assert NumberRange.from_converter(int) is None
