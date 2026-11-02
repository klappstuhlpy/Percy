"""Custom-command responses: a closed set of ``{placeholders}``, substituted in one pass.

Never ``str.format``: that resolves attribute paths (``{author.__class__}``, ``{author._state...}``).
Only keys present in the prepared ``values`` mapping (built by the cog from plain strings) and the two
generators ``{choice:a|b}`` / ``{random:1-100}`` are replaced; anything else stays exactly as typed,
and substituted values are never scanned again, so an argument cannot smuggle in a placeholder.
"""

from __future__ import annotations

import random
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ('MAX_OUTPUT', 'PLACEHOLDERS', 'render')

MAX_OUTPUT = 2000

#: placeholder -> what it becomes (shown in help and beside the dashboard input).
PLACEHOLDERS: dict[str, str] = {
    '{param}': 'an argument from the signature (by its name)',
    '{param.id} {param.name} {param.mention}': 'for member/user/role/channel arguments',
    '{author} {author.id} {author.name} {author.mention}': 'whoever ran the command',
    '{channel} {channel.id} {channel.name} {channel.mention}': 'the channel it ran in',
    '{guild} {guild.id} {guild.name}': 'the server',
    '{args}': 'everything typed after the command name',
    '{0} .. {9}': 'one word (or quoted phrase) of what was typed',
    '{uses}': 'how often the command has been used, this time included',
    '{choice:a|b|c}': 'one option picked at random',
    '{random:1-100}': 'a random whole number in the range',
}

_RANDOM_SPAN = 10**9
_TOKEN_RE = re.compile(
    r'\{\{|\}\}'
    r'|\{choice:([^{}]*)\}'
    r'|\{random:(-?\d{1,10})-(-?\d{1,10})\}'
    r'|\{([\w.]+)\}'
)


def render(template: str, values: Mapping[str, str], *, rng: random.Random | None = None) -> str:
    """Substitute known placeholders; unknown ones and stray braces are left untouched."""
    rng = rng or random.Random()

    def sub(match: re.Match[str]) -> str:
        token = match.group(0)
        if token in ('{{', '}}'):
            return token[0]
        choices, low, high, key = match.groups()
        if choices is not None:
            return rng.choice(choices.split('|'))
        if low is not None:
            a, b = sorted((int(low), int(high)))
            if b - a > _RANDOM_SPAN:
                return token
            return str(rng.randint(a, b))
        return values.get(key, token)

    return _TOKEN_RE.sub(sub, template)[:MAX_OUTPUT]
