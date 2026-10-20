"""Lockdown domain logic for the moderation package.

The lockdown *commands* live on the :class:`Moderation` cog (they must, to be registered), but the
channel-overwrite mechanics and lockdown bookkeeping live here as parameterised helpers so the cog
merely routes to them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from app.utils import helpers

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.core import Bot, Context

    ProgressCallback = Callable[[int, int], Awaitable[None]]


def build_lockdown_error_embed() -> discord.Embed:
    return discord.Embed(
        title="Failed to perform Lockdown",
        description="For some reason, I could not find an appropriate channel to edit overwrites for."
        "Note that this lockdown will potentially lock the bot from sending messages. "
        "Please explicitly give the bot permissions to **send messages** in threads and channels.",
        color=helpers.Colour.light_red(),
    )


async def get_lockdown_information(
    bot: Bot, guild_id: int, channel_ids: list[int] | None = None
) -> dict[int, discord.PermissionOverwrite]:
    """Gets the lockdown information for the given guild."""
    rows: list[tuple[int, int, int]] = await bot.db.moderation.get_lockdowns(guild_id, channel_ids=channel_ids)  # type: ignore

    return {
        channel_id: discord.PermissionOverwrite.from_pair(discord.Permissions(allow), discord.Permissions(deny))
        for channel_id, allow, deny in rows
    }


async def lock_channels(
    bot: Bot,
    guild: discord.Guild,
    channels: list[discord.TextChannel | discord.VoiceChannel],
    *,
    reason: str,
    progress: ProgressCallback | None = None,
) -> tuple[list[discord.TextChannel | discord.VoiceChannel], list[discord.TextChannel | discord.VoiceChannel]]:
    """Applies lockdown overwrites to the given channels and records them.

    Shared by the ``lockdown`` command and the dashboard internal API so the
    permission mechanics and bookkeeping live in one place. ``progress`` is an
    optional ``async (done, total)`` callback invoked after each channel.
    """
    records = []
    success, failures = [], []
    total = len(channels)
    for index, channel in enumerate(channels, start=1):
        overwrites = channel.overwrites_for(guild.default_role)
        allow, deny = overwrites.pair()
        overwrites.update(
            send_messages=False,
            add_reactions=False,
            use_application_commands=False,
            create_public_threads=False,
            create_private_threads=False,
            send_messages_in_threads=False,
        )
        if isinstance(channel, discord.VoiceChannel):
            overwrites.update(connect=False, speak=False)

        try:
            await channel.set_permissions(guild.default_role, overwrite=overwrites, reason=reason)
        except discord.HTTPException:
            failures.append(channel)
        else:
            success.append(channel)
            records.append(
                {
                    "guild_id": guild.id,
                    "channel_id": channel.id,
                    "allow": allow.value,
                    "deny": deny.value,
                }
            )

        if progress is not None:
            await progress(index, total)

    await bot.db.moderation.add_lockdowns(records)
    return success, failures


async def start_lockdown(
    ctx: Context, channels: list[discord.TextChannel | discord.VoiceChannel]
) -> tuple[list[discord.TextChannel | discord.VoiceChannel], list[discord.TextChannel | discord.VoiceChannel]]:
    """Starts a lockdown in the given channels."""
    assert ctx.guild is not None
    reason = f"Lockdown request by {ctx.author} (ID: {ctx.author.id})"
    async with ctx.progress(f"Locking down {len(channels)} channels...") as progress:

        async def on_progress(done: int, total: int) -> None:
            if done == total or done % 5 == 0:
                await progress.tick(done, total, "Locking down")

        return await lock_channels(ctx.bot, ctx.guild, channels, reason=reason, progress=on_progress)


async def end_lockdown(
    bot: Bot,
    guild: discord.Guild,
    *,
    channel_ids: list[int] | None = None,
    reason: str | None = None,
    progress: ProgressCallback | None = None,
) -> list[discord.abc.GuildChannel]:
    """Ends a lockdown in the given guild and returns the channels that could not be restored.

    A channel's saved overwrites are the only copy of its pre-lockdown permissions, so its
    row is deleted only once the restore succeeded (or the channel no longer exists). A
    failed channel keeps its row and stays listed as locked, so ending again retries it.
    Restored channels are also taken out of the guild's pending lockdown timers.

    ``progress`` is an optional ``async (done, total)`` callback invoked after each
    channel's overwrites are restored, used to surface live progress to the caller.
    """
    channel_fallback: dict[int, discord.abc.GuildChannel] | None = None
    default_role = guild.default_role
    failures: list[discord.abc.GuildChannel] = []
    done: list[int] = []

    lockdowns = await get_lockdown_information(bot, guild.id, channel_ids=channel_ids)
    total = len(lockdowns)
    try:
        for index, (channel_id, permissions) in enumerate(lockdowns.items(), start=1):
            channel = guild.get_channel(channel_id)
            if channel is None:
                if channel_fallback is None:
                    channel_fallback = {c.id: c for c in await guild.fetch_channels()}
                channel = channel_fallback.get(channel_id)

            if channel is None:
                # The channel was deleted: there is nothing left to restore.
                done.append(channel_id)
            else:
                try:
                    await channel.set_permissions(default_role, overwrite=permissions, reason=reason)
                except discord.HTTPException:
                    failures.append(channel)
                else:
                    done.append(channel_id)

            if progress is not None:
                await progress(index, total)
    finally:
        if done:
            await bot.db.moderation.remove_lockdowns(guild.id, done)
            # Those channels are no longer locked, so a pending `lockdown for` timer must not end
            # a later re-lock of them; channels that failed stay on their timer for a retry.
            await bot.timers.drop_lockdown_channels(guild.id, done)

    return failures


def is_potential_lockout(me: discord.Member, channel: discord.Thread | discord.VoiceChannel | discord.TextChannel) -> bool:
    """Checks if the bot is potentially locked out from sending messages in the given channel."""
    if isinstance(channel, discord.Thread):
        parent = channel.parent
        if parent is None:
            return True

        overwrites = parent.overwrites
        for role in me.roles:
            ow = overwrites.get(role)
            if ow is None:
                continue
            if ow.send_messages_in_threads:
                return False
        return True

    overwrites = channel.overwrites
    for role in me.roles:
        ow = overwrites.get(role)
        if ow is None:
            continue
        if ow.send_messages:
            return False
    return True
