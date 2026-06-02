"""Reusable execution helpers for moderation infractions.

The infraction *commands* themselves live on the :class:`Moderation` cog (they must, to be
registered as commands), but the reusable mechanics they delegate to live here so that the cog
stays focused on routing and so other modules (e.g. the gatekeeper UI) can share them without
reaching back into the cog.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from app.utils import merge_perms

if TYPE_CHECKING:
    from collections.abc import Sequence


def safe_reason_append(base: str, to_append: str) -> str:
    appended = f'{base} ({to_append})'
    if len(appended) > 512:
        return base
    return appended


def default_reason(author: discord.abc.User | discord.Member) -> str:
    """Return the standard audit-log reason for a moderation action performed by ``author``."""
    return f'Action done by {author} (ID: {author.id})'


async def update_role_permissions(
        role: discord.Role,
        guild: discord.Guild,
        invoker: discord.abc.User,
        update_read_permissions: bool = False,
        channels: Sequence[discord.abc.GuildChannel] | list[discord.abc.Messageable] | None = None,
        **permissions: bool | None
) -> tuple[int, int, int]:
    r"""|coro|

    Updates the permission overwrites of a specified role on the server.

    Notes
    -----
    This method should only be used to restrict permissions for the role in the channels.

    Parameters
    ----------
    role: discord.Role
        The role to update the permission overwrites for.
    guild: discord.Guild
        The guild to update the permission overwrites in.
    invoker: discord.abc.User
        The user who invoked the action.
    update_read_permissions: bool
        Whether to update the read permissions as well.
    channels: Sequence[discord.abc.GuildChannel] | list[discord.abc.Messageable] | None
        The channels to update the permission overwrites in.
    \*\*permissions: bool | None
        The permissions to update the permission overwrites with.
        Those are extras.

    Returns
    -------
    tuple[int, int, int]
        A tuple containing the number of successful, failed, and skipped updates.
    """
    success, failure, skipped = 0, 0, 0
    reason = default_reason(invoker)
    effective_channels: list[discord.abc.GuildChannel | discord.abc.Messageable]
    if channels is None:
        effective_channels = [ch for ch in guild.channels if isinstance(ch, discord.abc.Messageable)]
    else:
        effective_channels = list(channels)

    guild_perms = guild.me.guild_permissions
    for channel in effective_channels:
        perms = channel.permissions_for(guild.me)  # type: ignore[union-attr]
        if perms.manage_roles:
            overwrite = channel.overwrites_for(role)  # type: ignore[union-attr]
            channel_perms = {
                'send_messages': False,
                'add_reactions': False,
                'use_application_commands': False,
                'create_private_threads': False,
                'create_public_threads': False,
                'send_messages_in_threads': False,
                'connect': False,
                'speak': False,
            }
            if update_read_permissions:
                channel_perms['read_messages'] = False

            if permissions:
                merge_perms(overwrite, guild_perms, **permissions)  # type: ignore[arg-type]

            merge_perms(overwrite, guild_perms, **channel_perms)
            try:
                await channel.set_permissions(role, overwrite=overwrite, reason=reason)  # type: ignore[union-attr]
            except discord.HTTPException:
                failure += 1
            else:
                success += 1
        else:
            skipped += 1
    return success, failure, skipped
