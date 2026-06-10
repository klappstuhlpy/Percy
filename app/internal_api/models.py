"""Shared base for the InternalAPI handler mixins."""
from __future__ import annotations

from typing import Any

from discord.ext import commands


class _dummy_bot(commands.Bot):
    """A dummy bot for the InternalAPI handlers."""
    db: Any


class InternalAPIHandlers:
    """Typing anchor + shared helpers mixed into :class:`InternalAPI`."""

    bot: _dummy_bot

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

    @staticmethod
    def _resolve_entity(guild, entity_id: int) -> dict:
        """Resolves an arbitrary snowflake to ``{id, type, name}`` (role/channel/member)."""
        role = guild.get_role(entity_id)
        if role is not None:
            return {'id': str(entity_id), 'type': 'role', 'name': role.name}
        channel = guild.get_channel(entity_id)
        if channel is not None:
            return {'id': str(entity_id), 'type': 'channel', 'name': channel.name}
        member = guild.get_member(entity_id)
        if member is not None:
            return {'id': str(entity_id), 'type': 'member', 'name': member.display_name}
        return {'id': str(entity_id), 'type': 'unknown', 'name': str(entity_id)}