"""Command aliases: ``alias`` -> ``expansion`` with ``{args}`` and ``{0}``..``{9}``.

The expansion is re-parsed as a command, so a token with whitespace is re-quoted when it is spliced
in. An expansion with no placeholder gets the invocation's arguments appended (``ban`` -> ``ban @x spam``).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('expand', 'first_word')

_PLACEHOLDER_RE = re.compile(r'\{(args|[0-9])\}')


def _quote(token: str) -> str:
    return f'"{token}"' if any(c.isspace() for c in token) else token


def expand(expansion: str, raw_args: str, tokens: Sequence[str]) -> str:
    """Splice the invocation's raw argument text and tokens into ``expansion``."""
    used = False

    def sub(match: re.Match[str]) -> str:
        nonlocal used
        used = True
        key = match.group(1)
        if key == 'args':
            return raw_args
        index = int(key)
        return _quote(tokens[index]) if index < len(tokens) else ''

    out = _PLACEHOLDER_RE.sub(sub, expansion).strip()
    if not used and raw_args:
        out = f'{out} {raw_args}'
    return out


def first_word(expansion: str) -> str:
    """The command name an expansion starts with (lowercased; empty for a blank expansion)."""
    parts = expansion.split(None, 1)
    return parts[0].lower() if parts else ''
