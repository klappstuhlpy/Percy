"""Reusable execution helpers for moderation infractions.

The infraction *commands* themselves live on the :class:`Moderation` cog (they must, to be
registered as commands), but the reusable mechanics they delegate to live here so that the cog
stays focused on routing and so other modules (e.g. the sentinel UI) can share them without
reaching back into the cog.
"""

from __future__ import annotations

import datetime
import logging
import time
from contextlib import suppress
from typing import TYPE_CHECKING

import discord

from app.utils import merge_perms
from app.utils.checks import hierarchy_error
from config import Emojis

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from app.core import Bot, Context
    from app.core.timer import Timer

    ProgressCallback = Callable[[int, int], Awaitable[None]]

log = logging.getLogger(__name__)


def check_member_hierarchy(
    ctx: Context,
    member: discord.abc.Snowflake | discord.Member,
    *,
    action: str,
    check_bot: bool = True,
) -> str | None:
    """Return an error message if ``ctx.author`` cannot ``action`` ``member``, else ``None``.

    A thin command-side wrapper over :func:`app.utils.checks.hierarchy_error`, the one
    hierarchy rule every moderation path shares.
    """
    assert ctx.guild is not None
    return hierarchy_error(ctx.author, member, ctx.guild.me, action=action, check_bot=check_bot)  # type: ignore[arg-type]


def safe_reason_append(base: str, to_append: str) -> str:
    appended = f"{base} ({to_append})"
    if len(appended) > 512:
        return base
    return appended


def default_reason(author: discord.abc.User | discord.Member) -> str:
    """Return the standard audit-log reason for a moderation action performed by ``author``."""
    return f"Action done by {author} (ID: {author.id})"


def mute_role_error(guild: discord.Guild, role_id: int) -> str | None:
    """Why Percy cannot hand out or take away the mute role ``role_id`` right now, else ``None``."""
    role = guild.get_role(role_id)
    if role is None:
        return "The mute role no longer exists. Set up a new one with `muterole`."
    if guild.me.top_role <= role:
        return "My highest role must be above the mute role before I can mute or unmute anyone."
    return None


async def softban(
    bot: Bot,
    guild: discord.Guild,
    member: discord.abc.Snowflake,
    *,
    reason: str | None,
    delete_message_seconds: int = 86400,
) -> bool:
    """Ban ``member`` and lift the ban again: a kick that also wipes their recent messages.

    A failed ban raises as usual. Returns ``False`` when the ban went through but lifting it
    did not, so the member is still banned: the caller must record and report a ban then, not
    a failure, or a ban stands that nobody knows about. Any tempban timer is cancelled in that
    case, since its expiry would otherwise lift this ban.
    """
    await guild.ban(member, reason=reason, delete_message_seconds=delete_message_seconds)
    try:
        await guild.unban(member, reason=reason)
    except discord.HTTPException:
        log.warning("Softban of %s in guild %s: banned, but the unban failed", member.id, guild.id)
        await bot.timers.delete_member_timer("tempban", guild.id, member.id)
        return False
    return True


#: How many times an expiry timer (tempban/tempmute) is retried before it is given up.
TIMER_MAX_RETRIES = 10
TIMER_RETRY_BASE = datetime.timedelta(minutes=5)
TIMER_RETRY_CAP = datetime.timedelta(hours=6)


def timer_retry_delay(attempt: int) -> datetime.timedelta | None:
    """The wait before retry number ``attempt + 1`` of a failed expiry, or ``None`` to give up.

    Doubles from 5 minutes up to 6 hours, so ten retries cover a Discord outage of about a day.
    """
    if attempt >= TIMER_MAX_RETRIES:
        return None
    return min(TIMER_RETRY_BASE * 2**attempt, TIMER_RETRY_CAP)


def timer_origin(timer: Timer) -> str:
    """When the moderator originally set ``timer``, surviving any retries."""
    return timer.kwargs.get("origin") or str(timer.created)


async def retry_expiry_timer(bot: Bot, timer: Timer, why: str) -> None:
    """Re-arm a tempban/tempmute expiry that could not run, so the punishment still ends.

    :meth:`TimerManager.call` deletes the row before dispatching, so an expiry that fails
    without this is lost for good and a temporary ban or mute silently becomes permanent.
    """
    attempt = int(timer.kwargs.get("attempt", 0))
    delay = timer_retry_delay(attempt)
    if delay is None:
        log.warning("Giving up on %s timer %s with args %s after %d retries: %s", timer.event, timer.id, timer.args, attempt, why)
        return

    log.info("Retrying %s timer %s in %s: %s", timer.event, timer.id, delay, why)
    await bot.timers.create(
        discord.utils.utcnow() + delay,
        timer.event,
        *timer.args,
        attempt=attempt + 1,
        origin=timer_origin(timer),
        timezone=timer.timezone,
    )


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
            if overwrite == channel.overwrites_for(role):
                # Already correct: skip the API call so a re-sync of a large server stays cheap.
                success += 1
                if progress is not None:
                    await progress(index, total)
                continue
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

    Shared by the mute-role and sentinel setup views so heavy multi-channel syncs
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
