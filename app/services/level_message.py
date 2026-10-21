"""Level-up message templates: a closed set of ``{placeholders}``, substituted safely.

The template is written by server admins. It used to go through ``str.format(user=member)``,
which resolves *attribute paths* -- ``{user._state.http.token}`` would have printed the bot
token. Substitution here only ever replaces the names in :data:`LEVEL_UP_VARIABLES` with
pre-rendered strings; anything else is left exactly as typed.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    import discord

__all__ = ('LEVEL_UP_VARIABLES', 'level_up_values', 'render_level_up')

#: placeholder -> what it becomes (shown beside the dashboard input).
LEVEL_UP_VARIABLES: dict[str, str] = {
    'user': "the member's username",
    'user.mention': 'a mention that pings the member',
    'user.display_name': "the member's server nickname",
    'level': 'the level they reached',
    'server': "the server's name",
}

# ``{{`` / ``}}`` stay the literal braces they were under str.format.
_TOKEN_RE = re.compile(r'\{\{|\}\}|\{([\w.]+)\}')


def render_level_up(template: str, values: Mapping[str, str]) -> str:
    """Replace known ``{placeholders}``; unknown ones and stray braces are left untouched."""

    def sub(match: re.Match[str]) -> str:
        token = match.group(0)
        if token in ('{{', '}}'):
            return token[0]
        return values.get(match.group(1), token)

    return _TOKEN_RE.sub(sub, template)


def level_up_values(member: discord.Member | discord.User | None, level: int) -> dict[str, str]:
    """The strings each placeholder becomes for ``member`` reaching ``level``."""
    values = {'level': str(level)}
    if member is not None:
        values |= {'user': str(member), 'user.mention': member.mention, 'user.display_name': member.display_name}
        guild = getattr(member, 'guild', None)
        if guild is not None:
            values['server'] = guild.name
    return values
