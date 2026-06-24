"""Shared helpers for resolving Discord entities into API-friendly dicts."""
from __future__ import annotations

import discord


def resolve_channel(guild: discord.Guild, channel_id: int | None) -> dict | None:
    if channel_id is None:
        return None
    ch = guild.get_channel(channel_id)
    return {
        'id': str(channel_id),
        'name': ch.name if ch else 'deleted-channel',
        'type': str(ch.type) if ch else 'unknown',
    }


def resolve_role(guild: discord.Guild, role_id: int | None) -> dict | None:
    if role_id is None:
        return None
    role = guild.get_role(role_id)
    return {
        'id': str(role_id),
        'name': role.name if role else 'deleted-role',
        'color': role.color.value if role else 0,
    }


def resolve_entity(guild: discord.Guild, entity_id: int) -> dict:
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
