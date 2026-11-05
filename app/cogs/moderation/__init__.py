"""Moderation cog package.

Lazy on purpose: ``app.internal_api`` imports submodules (``member_actions``, ``sentinel``, ...) and must not
drag in ``cog``/``ui`` with them.  discord.py finds ``setup`` below; package-root names resolve on first access.
"""

from __future__ import annotations

lazy from app.cogs.moderation.cog import Moderation, setup
lazy from app.cogs.moderation.models import FlaggedMember, SpamCheckerResult, SpammerSequence

__all__ = (
    'FlaggedMember',
    'Moderation',
    'SpamCheckerResult',
    'SpammerSequence',
    'setup',
)
