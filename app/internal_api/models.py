"""Shared base for the InternalAPI handler mixins."""
from __future__ import annotations

from discord.ext import commands


class InternalAPIHandlers:
    """Typing anchor + shared helpers mixed into :class:`InternalAPI`."""

    bot: commands.Bot

    @staticmethod
    def _resolve_channel(guild, channel_id: int | None) -> dict | None:
        if channel_id is None:
            return None
        ch = guild.get_channel(channel_id)
        return {
            'id': str(channel_id),
            'name': ch.name if ch else 'deleted-channel',
            'type': str(ch.type) if ch else 'unknown',
        }

    @staticmethod
    def _resolve_role(guild, role_id: int | None) -> dict | None:
        if role_id is None:
            return None
        role = guild.get_role(role_id)
        return {
            'id': str(role_id),
            'name': role.name if role else 'deleted-role',
            'color': role.color.value if role else 0,
        }