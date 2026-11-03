"""Moderation cog package.

Lazy on purpose: ``app.internal_api`` imports submodules (``member_actions``, ``sentinel``, ...) and must not
drag in ``cog``/``ui`` with them.  discord.py finds ``setup`` below; package-root names resolve on first access.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.cogs.moderation.cog import Moderation
    from app.cogs.moderation.models import FlaggedMember, SpamCheckerResult, SpammerSequence
    from app.core import Bot

__all__ = (
    'FlaggedMember',
    'Moderation',
    'SpamCheckerResult',
    'SpammerSequence',
    'setup',
)

_LAZY = {'Moderation': 'cog', 'FlaggedMember': 'models', 'SpamCheckerResult': 'models', 'SpammerSequence': 'models'}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        return getattr(import_module(f'{__name__}.{_LAZY[name]}'), name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


async def setup(bot: Bot) -> None:
    from app.cogs.moderation.cog import setup as _setup

    await _setup(bot)
