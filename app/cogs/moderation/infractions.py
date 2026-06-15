"""Reusable execution helpers for moderation infractions.

The infraction *commands* themselves live on the :class:`Moderation` cog (they must, to be
registered as commands), but the reusable mechanics they delegate to live here so that the cog
stays focused on routing and so other modules (e.g. the gatekeeper UI) can share them without
reaching back into the cog.
"""

from __future__ import annotations

import time
from contextlib import suppress
from typing import TYPE_CHECKING

import discord

from app.utils import merge_perms
from config import Emojis

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from app.core import Context

    ProgressCallback = Callable[[int, int], Awaitable[None]]


def check_member_hierarchy(
    ctx: Context,
    member: discord.abc.Snowflake | discord.Member,
    *,
    action: str,
    check_owner: bool = True,
) -> str | None:
    """Return an error message if a moderation ``action`` cannot target ``member``, else ``None``.

    Centralises the self / server-owner / role-hierarchy guards shared by the ban, kick and mute
    commands. ``check_owner`` is disabled for mutes, which historically skipped the owner guard.
    The ``isinstance`` guard mirrors the ban family, where ``member`` may be a bare snowflake
    (banning by ID); for the always-resolved mute case it is simply always true.
    """
    assert ctx.guild is not None
    if ctx.author.id == member.id:
        return f"You cannot {action} yourself."

    if check_owner and member.id == ctx.guild.owner_id:
        return f"You cannot {action} the server owner."

    if isinstance(member, discord.Member):
        if ctx.author.top_role < member.top_role:
            return f"You cannot {action} a member with a role equal to or higher than yours."

        if ctx.guild.me.top_role < member.top_role:
            return f"I cannot {action} a member with a role equal to or higher than mine."

    return None


def safe_reason_append(base: str, to_append: str) -> str:
    appended = f"{base} ({to_append})"
    if len(appended) > 512:
        return base
    return appended


def default_reason(author: discord.abc.User | discord.Member) -> str:
    """Return the standard audit-log reason for a moderation action performed by ``author``."""
    return f"Action done by {author} (ID: {author.id})"


async def update_role_permissions(
    role: discord.Role,
    guild: discord.Guild,
    invoker: discord.abc.User,
    update_read_permissions: bool = False,
    channels: Sequence[discord.abc.GuildChannel] | list[discord.abc.Messageable] | None = None,
    progress: ProgressCallback | None = None,
    **permissions: bool | None,
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
    progress: ProgressCallback | None
        An optional ``async (done, total)`` callback invoked after each channel is
        processed, used to surface live progress to the caller.
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
        effective_channels = list(channels)  # type: ignore

    guild_perms = guild.me.guild_permissions
    total = len(effective_channels)
    for index, channel in enumerate(effective_channels, start=1):
        perms = channel.permissions_for(guild.me)
        if perms.manage_roles:
            overwrite = channel.overwrites_for(role)
            channel_perms = {
                "send_messages": False,
                "add_reactions": False,
                "use_application_commands": False,
                "create_private_threads": False,
                "create_public_threads": False,
                "send_messages_in_threads": False,
                "connect": False,
                "speak": False,
            }
            if update_read_permissions:
                channel_perms["read_messages"] = False

            if permissions:
                merge_perms(overwrite, guild_perms, **permissions)

            merge_perms(overwrite, guild_perms, **channel_perms)
            try:
                await channel.set_permissions(role, overwrite=overwrite, reason=reason)
            except discord.HTTPException:
                failure += 1
            else:
                success += 1
        else:
            skipped += 1

        if progress is not None:
            await progress(index, total)
    return success, failure, skipped


async def sync_permissions_with_progress(
    interaction: discord.Interaction,
    role: discord.Role,
    guild: discord.Guild,
    *,
    channels: Sequence[discord.abc.GuildChannel] | list[discord.abc.Messageable] | None = None,
    update_read_permissions: bool = False,
    label: str = "Syncing channel permissions",
) -> tuple[int, int, int]:
    """Run :func:`update_role_permissions` with a live ephemeral progress message.

    Shared by the mute-role and gatekeeper setup views so heavy multi-channel syncs
    surface a ``done/total`` counter instead of a frozen typing indicator. The
    interaction must already be deferred or responded to. Status edits are throttled
    to roughly one per 1.5s (plus a final tick) to stay within Discord's rate limits.
    """
    status = await interaction.followup.send(f"{Emojis.loading} {label}...", ephemeral=True, wait=True)

    last_edit = 0.0

    async def on_progress(done: int, total: int) -> None:
        nonlocal last_edit
        now = time.monotonic()
        if done != total and now - last_edit < 1.5:
            return
        last_edit = now
        with suppress(discord.HTTPException):
            await status.edit(content=f"{Emojis.loading} {label}... `{done}/{total}`")

    result = await update_role_permissions(
        role,
        guild,
        interaction.user,
        update_read_permissions=update_read_permissions,
        channels=channels,
        progress=on_progress,
    )
    with suppress(discord.HTTPException):
        await status.delete()
    return result
