"""Discord side of the member restrictions: voice kick/ban, nicknames, per-channel mutes.

Each function either succeeds or raises :class:`MemberActionError`; the callers
(:func:`app.cogs.moderation.member_actions.apply_member_action`, hence the commands, the dashboard and
the listeners) own permissions, hierarchy and the case. The pure rules are in
:mod:`app.services.restrictions`, the SQL in ``bot.db.restrictions``.
"""

from __future__ import annotations

import datetime
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import discord

from app.cogs.moderation.member_actions import MemberActionError, validate_mute_duration
from app.services.moderation_settings import sticky_to_restore
from app.services.restrictions import CHANNEL_MUTE_DENIALS, normalize_nickname

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from app.core import Bot

__all__ = (
    'apply_channelmute',
    'apply_nick',
    'apply_nickfreeze',
    'apply_voiceban',
    'apply_voicekick',
    'lift_channelmute',
    'lift_nickfreeze',
    'lift_voiceban',
    'reapply',
    'remove_stickyrole',
    'restore_stickyroles',
    'sticky_error',
)

VOICEBAN_ROLE_NAME = 'Voice Banned'


def _expiry(duration: int | None) -> datetime.datetime | None:
    if duration is None:
        return None
    return datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=duration)


def _nickname(nickname: str | None) -> str | None:
    try:
        return normalize_nickname(nickname)
    except ValueError as e:
        raise MemberActionError(400, str(e)) from None


async def deny_voice(role: discord.Role, channels: Iterable[discord.abc.GuildChannel], reason: str | None) -> None:
    """Denies Connect and Speak to ``role`` in each of ``channels`` (skipping ones Percy cannot edit)."""
    for channel in channels:
        if not channel.permissions_for(role.guild.me).manage_roles:
            continue
        overwrite = channel.overwrites_for(role)
        if overwrite.connect is False and overwrite.speak is False:
            continue
        overwrite.update(connect=False, speak=False)
        with suppress(discord.HTTPException):
            await channel.set_permissions(role, overwrite=overwrite, reason=reason)


async def voiceban_role(bot: Bot, guild: discord.Guild, reason: str | None, *, create: bool) -> discord.Role | None:
    """The guild's voice-ban role, created (and bound) on demand when ``create`` is set."""
    config = await bot.db.get_guild_config(guild.id)
    role = guild.get_role(config.voiceban_role_id) if config.voiceban_role_id else None
    if role is None:
        if not create:
            return None
        role = await guild.create_role(name=VOICEBAN_ROLE_NAME, reason=reason)
        await config.update(voiceban_role_id=role.id)
        await deny_voice(role, [*guild.voice_channels, *guild.stage_channels], reason)
    if guild.me.top_role <= role:
        raise MemberActionError(400, 'the voice ban role is equal to or above my highest role')
    return role


async def apply_voicekick(member: discord.Member, reason: str | None) -> None:
    if member.voice is None:
        raise MemberActionError(400, 'member is not in a voice channel')
    await member.move_to(None, reason=reason)


async def apply_voiceban(
    bot: Bot,
    guild: discord.Guild,
    member: discord.Member,
    moderator: discord.Member,
    reason: str | None,
    duration: int | None,
) -> None:
    """Gives the voice-ban role, disconnects the member and arms the expiry."""
    validate_mute_duration(duration)
    role = await voiceban_role(bot, guild, reason, create=True)
    assert role is not None
    if member.get_role(role.id) is not None:
        raise MemberActionError(400, 'member is already voice banned')
    expires = _expiry(duration)
    await bot.db.restrictions.upsert(guild.id, member.id, 'voiceban', expires_at=expires)
    # Not banned right now, so a timer left over is stale (the stored expiry would also veto it).
    await bot.timers.delete_member_timer('voiceban', guild.id, member.id)
    await member.add_roles(role, reason=reason)
    if member.voice is not None:
        with suppress(discord.HTTPException):
            await member.move_to(None, reason=reason)
    if expires is not None:
        await bot.timers.create(expires, 'voiceban', guild.id, moderator.id, member.id)


async def lift_voiceban(bot: Bot, guild: discord.Guild, member: discord.Member, reason: str | None) -> None:
    role = await voiceban_role(bot, guild, reason, create=False)
    held = role is not None and member.get_role(role.id) is not None
    if not held and await bot.db.restrictions.get(guild.id, member.id, 'voiceban') is None:
        raise MemberActionError(400, 'member is not voice banned')
    await bot.timers.delete_member_timer('voiceban', guild.id, member.id)
    if held:
        assert role is not None
        await member.remove_roles(role, reason=reason)
    # Last, so a failed role removal leaves the row for the rejoin/expiry paths to finish the job.
    await bot.db.restrictions.remove(guild.id, member.id, 'voiceban')


async def apply_nick(bot: Bot, member: discord.Member, nickname: str | None, reason: str | None) -> None:
    """Sets the nickname; blank resets it. On a frozen member the freeze follows the new name (a reset is refused)."""
    name = _nickname(nickname)
    if await bot.db.restrictions.get(member.guild.id, member.id, 'nickfreeze') is not None:
        if name is None:
            raise MemberActionError(400, "the member's nickname is frozen; unfreeze it first")
        await bot.db.restrictions.upsert(member.guild.id, member.id, 'nickfreeze', value=name)
    await member.edit(nick=name, reason=reason)


async def apply_nickfreeze(bot: Bot, member: discord.Member, nickname: str | None, reason: str | None) -> None:
    name = _nickname(nickname)
    if name is None:
        raise MemberActionError(400, 'nickfreeze requires a nickname')
    # Row first: the edit fires on_member_update, whose revert must see the new value, not the old one.
    await bot.db.restrictions.upsert(member.guild.id, member.id, 'nickfreeze', value=name)
    await member.edit(nick=name, reason=reason)


async def lift_nickfreeze(bot: Bot, member: discord.Member) -> None:
    if not await bot.db.restrictions.remove(member.guild.id, member.id, 'nickfreeze'):
        raise MemberActionError(400, "member's nickname is not frozen")


def _channel(guild: discord.Guild, channel_id: int | None, moderator: discord.Member) -> discord.abc.GuildChannel:
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel is None or isinstance(channel, discord.CategoryChannel):
        raise MemberActionError(400, 'a valid channel is required')
    if not channel.permissions_for(moderator).manage_channels:
        raise MemberActionError(403, 'you need Manage Channels in that channel')
    return channel


async def set_channel_denial(
    channel: discord.abc.GuildChannel, member: discord.abc.Snowflake, denied: bool, reason: str | None
) -> None:
    """Denies (or clears) the channel-mute permissions on the member's overwrite, dropping an emptied one."""
    overwrite = channel.overwrites_for(member)
    overwrite.update(**dict.fromkeys(CHANNEL_MUTE_DENIALS, False if denied else None))
    await channel.set_permissions(member, overwrite=None if overwrite.is_empty() else overwrite, reason=reason)


async def apply_channelmute(
    bot: Bot,
    guild: discord.Guild,
    member: discord.Member,
    moderator: discord.Member,
    channel_id: int | None,
    reason: str | None,
    duration: int | None,
) -> None:
    validate_mute_duration(duration)
    channel = _channel(guild, channel_id, moderator)
    expires = _expiry(duration)
    await set_channel_denial(channel, member, True, reason)
    # The stored expiry vetoes any older timer for this channel, so none has to be deleted.
    await bot.db.restrictions.upsert(guild.id, member.id, 'channelmute', channel_id=channel.id, expires_at=expires)
    if expires is not None:
        await bot.timers.create(expires, 'channelmute', guild.id, moderator.id, member.id, channel.id)


async def lift_channelmute(
    bot: Bot,
    guild: discord.Guild,
    member: discord.Member,
    moderator: discord.Member,
    channel_id: int | None,
    reason: str | None,
) -> None:
    channel = _channel(guild, channel_id, moderator)
    if await bot.db.restrictions.get(guild.id, member.id, 'channelmute', channel.id) is None:
        raise MemberActionError(400, 'member is not muted in that channel')
    await set_channel_denial(channel, member, False, reason)
    await bot.db.restrictions.remove(guild.id, member.id, 'channelmute', channel.id)


async def reapply(bot: Bot, member: discord.Member) -> None:
    """Puts a rejoining member's stored restrictions back. Discord errors propagate to the caller."""
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    guild = member.guild
    reason = 'Member rejoined with an active restriction.'
    rows = await bot.db.restrictions.for_member(guild.id, member.id)
    for row in rows:
        if row['expires_at'] is not None and row['expires_at'] <= now:
            continue  # the expiry timer owns the cleanup
        if row['kind'] == 'voiceban':
            if (role := await voiceban_role(bot, guild, reason, create=False)) is not None:
                await member.add_roles(role, reason=reason)
        elif row['kind'] == 'nickfreeze':
            await member.edit(nick=row['value'], reason=reason)
        elif row['kind'] == 'channelmute' and (channel := guild.get_channel(row['channel_id'])) is not None:
            await set_channel_denial(channel, member, True, reason)
    await restore_stickyroles(member, rows, 'Sticky roles restored after the member rejoined.')


def sticky_error(guild: discord.Guild, moderator: discord.Member, role: discord.Role) -> str | None:
    """Why ``role`` cannot be made sticky by ``moderator`` (Percy and the moderator must both outrank it)."""
    if role.is_default() or role.managed:
        return 'that role cannot be assigned: it is @everyone or managed by an integration'
    if guild.me.top_role <= role:
        return 'that role is equal to or above my highest role'
    if moderator.id != guild.owner_id and moderator.top_role <= role:
        return 'your highest role must be above that role'
    return None


async def add_stickyrole(
    bot: Bot, guild: discord.Guild, member: discord.Member, moderator: discord.Member, role: discord.Role
) -> None:
    """Makes ``role`` sticky for ``member`` and gives it to them now."""
    if error := sticky_error(guild, moderator, role):
        raise MemberActionError(403, error)
    await bot.db.restrictions.upsert(guild.id, member.id, 'stickyrole', channel_id=role.id)
    if member.get_role(role.id) is None:
        await member.add_roles(role, reason=f'Sticky role added by {moderator}.')


async def remove_stickyrole(
    bot: Bot, guild: discord.Guild, member: discord.Member, moderator: discord.Member, role: discord.Role
) -> None:
    """Stops re-applying ``role`` to ``member``; the role itself stays on them."""
    if error := sticky_error(guild, moderator, role):
        raise MemberActionError(403, error)
    if not await bot.db.restrictions.remove(guild.id, member.id, 'stickyrole', role.id):
        raise MemberActionError(400, 'that role is not sticky for this member')


async def restore_stickyroles(member: discord.Member, rows: Iterable[Mapping[str, Any]], reason: str) -> None:
    """Gives ``member`` back the sticky roles (among their restriction ``rows``) they lack. Discord errors propagate."""
    guild = member.guild
    stored = {r['channel_id'] for r in rows if r['kind'] == 'stickyrole'}
    if not stored:
        return
    assignable = {r.id for r in guild.roles if not r.is_default() and not r.managed and r < guild.me.top_role}
    if roles := sticky_to_restore(stored, {r.id for r in member.roles}, assignable):
        await member.add_roles(*(discord.Object(id=r) for r in roles), reason=reason)
