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
    from app.core import Bot, Context


def build_lockdown_error_embed() -> discord.Embed:
    return discord.Embed(
        title='Failed to perform Lockdown',
        description='For some reason, I could not find an appropriate channel to edit overwrites for.'
                    'Note that this lockdown will potentially lock the bot from sending messages. '
                    'Please explicitly give the bot permissions to **send messages** in threads and channels.',
        color=helpers.Colour.light_red(),
    )


async def get_lockdown_information(
        bot: Bot, guild_id: int, channel_ids: list[int] | None = None
) -> dict[int, discord.PermissionOverwrite]:
    """Gets the lockdown information for the given guild."""
    rows: list[tuple[int, int, int]] = await bot.db.moderation.get_lockdowns(
        guild_id, channel_ids=channel_ids)

    return {
        channel_id: discord.PermissionOverwrite.from_pair(discord.Permissions(allow), discord.Permissions(deny))
        for channel_id, allow, deny in rows
    }


async def start_lockdown(
        ctx: Context, channels: list[discord.TextChannel | discord.VoiceChannel]
) -> tuple[list[discord.TextChannel | discord.VoiceChannel], list[discord.TextChannel | discord.VoiceChannel]]:
    """Starts a lockdown in the given channels."""
    assert ctx.guild is not None
    guild_id = ctx.guild.id

    records = []
    success, failures = [], []
    reason = f'Lockdown request by {ctx.author} (ID: {ctx.author.id})'
    async with ctx.typing():
        for channel in channels:
            overwrites = channel.overwrites_for(channel.guild.default_role)
            allow, deny = overwrites.pair()
            overwrites.update(
                send_messages=False,
                add_reactions=False,
                use_application_commands=False,
                create_public_threads=False,
                create_private_threads=False,
                send_messages_in_threads=False
            )

            try:
                await channel.set_permissions(ctx.guild.default_role, overwrite=overwrites, reason=reason)
            except discord.HTTPException:
                failures.append(channel)
            else:
                success.append(channel)
                records.append(
                    {
                        'guild_id': guild_id,
                        'channel_id': channel.id,
                        'allow': allow.value,
                        'deny': deny.value,
                    }
                )

    await ctx.bot.db.moderation.add_lockdowns(records)
    return success, failures


async def end_lockdown(
        bot: Bot,
        guild: discord.Guild,
        *,
        channel_ids: list[int] | None = None,
        reason: str | None = None,
) -> list[discord.abc.GuildChannel]:
    """Ends a lockdown in the given guild."""
    channel_fallback: dict[int, discord.abc.GuildChannel] | None = None
    default_role = guild.default_role
    failures = []

    lockdowns = await get_lockdown_information(bot, guild.id, channel_ids=channel_ids)
    for channel_id, permissions in lockdowns.items():
        channel = guild.get_channel(channel_id)
        if channel is None:
            if channel_fallback is None:
                channel_fallback = {c.id: c for c in await guild.fetch_channels()}
                channel = channel_fallback.get(channel_id)
                if channel is None:
                    continue
            continue

        try:
            await channel.set_permissions(default_role, overwrite=permissions, reason=reason)
        except discord.HTTPException:
            failures.append(channel)

    return failures


async def is_cooldown_active(bot: Bot, guild: discord.Guild, channel: discord.abc.GuildChannel) -> bool:
    """Checks if the given channel is currently in a lockdown."""
    record = await bot.db.moderation.get_lockdown(guild.id, channel.id)
    return bool(record)


def is_potential_lockout(
        me: discord.Member, channel: discord.Thread | discord.VoiceChannel | discord.TextChannel
) -> bool:
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
