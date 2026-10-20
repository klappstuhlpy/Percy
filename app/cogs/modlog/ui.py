"""Embed construction for moderation cases."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from app.cogs.modlog.models import ModerationCase
    from app.core.bot import Bot

__all__ = ('build_case_embed',)

#: Discord's limit on an embed field value; a longer reason would make the whole embed a 400.
FIELD_LIMIT = 1024


def _shown_reason(reason: str | None) -> str:
    if not reason:
        return '*No reason provided.*'
    return reason if len(reason) <= FIELD_LIMIT else reason[: FIELD_LIMIT - 1] + '\N{HORIZONTAL ELLIPSIS}'


def _format_user(bot: Bot, user_id: int | None) -> str:
    if user_id is None:
        return 'Unknown'
    user = bot.get_user(user_id)
    return f'{user} (`{user_id}`)' if user is not None else f'<@{user_id}> (`{user_id}`)'


def build_case_embed(bot: Bot, case: ModerationCase) -> discord.Embed:
    """Builds the embed announcing or displaying a moderation case."""
    case_type = case.type
    title_label = case_type.label if case_type else case.action.title()
    emoji = case_type.emoji if case_type else '\N{MEMO}'
    colour = case_type.colour if case_type else 0x95A5A6

    embed = discord.Embed(
        title=f'{emoji} Case #{case.index} • {title_label}',
        colour=colour,
        timestamp=case.created_at,
    )
    embed.add_field(name='User', value=_format_user(bot, case.target_id), inline=True)
    embed.add_field(name='Moderator', value=_format_user(bot, case.moderator_id), inline=True)
    embed.add_field(name='Reason', value=_shown_reason(case.reason), inline=False)
    embed.set_footer(text=f'Case ID: {case.id}')
    return embed
