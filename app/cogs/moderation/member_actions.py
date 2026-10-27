"""The member-action executor shared by the internal API and the Notifications cog.

One code path applies a moderator's action to a member (validation, hierarchy, execution,
``mod_action`` dispatch), so the dashboard and an approved request behave identically. It
raises :class:`MemberActionError`; the API router maps that to an ``HTTPException``.
"""
from __future__ import annotations

import asyncio
import datetime
from contextlib import suppress

import discord

from app.utils.checks import hierarchy_error

# Percy keeps no message archive (the gateway cache holds 10 messages), so both the message
# viewer and the purge action read live channel history. The scan is bounded on both axes:
# only the most recently active channels are visited, and only a slice of each is read.
MAX_SCAN_CHANNELS = 20
MAX_SCAN_CONCURRENCY = 5

# Discord refuses to bulk-delete messages older than two weeks.
BULK_DELETE_MAX_AGE = datetime.timedelta(days=14)

# Discord caps a timeout at 28 days; anything under a minute is not worth a round trip.
MIN_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 28 * 24 * 60 * 60
# A tempmute is lifted by a timer, so it has no Discord cap -- but past a year it is a permanent
# mute in all but name, and an unbounded value overflows the expiry datetime.
MAX_MUTE_SECONDS = 365 * 24 * 60 * 60

# Actions Discord only lets Percy perform on a member it outranks. Every action also requires the
# moderator to outrank the member; a mute only needs the mute role below Percy, not the member.
HIERARCHY_ACTIONS = frozenset({'kick', 'ban', 'softban', 'timeout', 'untimeout'})


class MemberActionError(Exception):
    """A refused or failed member action; ``status`` is the HTTP status the API maps it to."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def validate_timeout_duration(duration: int | None) -> datetime.timedelta:
    """Validates a timeout duration in seconds, returning it as a timedelta."""
    if duration is None:
        raise MemberActionError(400, 'timeout requires a duration')
    if not MIN_TIMEOUT_SECONDS <= duration <= MAX_TIMEOUT_SECONDS:
        raise MemberActionError(400, f'duration must be between {MIN_TIMEOUT_SECONDS} seconds and 28 days')
    return datetime.timedelta(seconds=duration)


def validate_mute_duration(duration: int | None) -> None:
    """Refuses a tempmute duration outside one minute to one year (``None`` is a permanent mute)."""
    if duration is not None and not MIN_TIMEOUT_SECONDS <= duration <= MAX_MUTE_SECONDS:
        raise MemberActionError(400, f'duration must be between {MIN_TIMEOUT_SECONDS} seconds and 365 days')


async def scan_member_messages(
    guild: discord.Guild,
    user_id: int,
    *,
    limit: int,
    per_channel: int = 100,
) -> tuple[list[discord.Message], int]:
    """Best-effort scan of recent channel history for one member's messages.

    Returns the newest ``limit`` messages (across channels) plus the number of channels
    actually read. Channels are visited newest-activity-first and capped at
    :data:`MAX_SCAN_CHANNELS`, so a member who last spoke in a quiet channel long ago may
    not show up — this is a recency view, not a complete history.
    """
    me = guild.me
    readable = [c for c in guild.text_channels if c.permissions_for(me).read_message_history]
    readable.sort(key=lambda c: c.last_message_id or 0, reverse=True)
    targets = readable[:MAX_SCAN_CHANNELS]

    semaphore = asyncio.Semaphore(MAX_SCAN_CONCURRENCY)

    async def scan(channel: discord.TextChannel) -> list[discord.Message]:
        async with semaphore:
            try:
                return [m async for m in channel.history(limit=per_channel) if m.author.id == user_id]
            except discord.HTTPException:
                return []

    found = await asyncio.gather(*(scan(channel) for channel in targets))
    messages = [message for chunk in found for message in chunk]
    messages.sort(key=lambda m: m.created_at, reverse=True)
    return messages[:limit], len(targets)


async def purge_member_messages(guild: discord.Guild, user_id: int, limit: int, reason: str | None) -> int:
    """Deletes up to ``limit`` of a member's recent messages. Returns how many were removed."""
    messages, _ = await scan_member_messages(guild, user_id, limit=limit)
    cutoff = datetime.datetime.now(datetime.UTC) - BULK_DELETE_MAX_AGE

    by_channel: dict[int, list[discord.Message]] = {}
    for message in messages:
        if message.created_at > cutoff:
            by_channel.setdefault(message.channel.id, []).append(message)

    deleted = 0
    for channel_id, batch in by_channel.items():
        channel = guild.get_channel(channel_id)
        if channel is None or not channel.permissions_for(guild.me).manage_messages:
            continue
        # delete_messages chunks internally but caps at 100 per call.
        for start in range(0, len(batch), 100):
            chunk = batch[start:start + 100]
            with suppress(discord.HTTPException):
                await channel.delete_messages(chunk, reason=reason)
                deleted += len(chunk)
    return deleted


async def apply_mute(
    bot, guild: discord.Guild, member: discord.Member, moderator: discord.Member, reason: str | None, duration: int | None
) -> str:
    """Adds the configured mute role, arming an expiry timer when a duration is given.

    Returns the action recorded on the case (``mute`` or ``tempmute``).
    """
    config = await bot.db.get_guild_config(guild.id)
    role_id = config.mute_role_id if config else None
    if role_id is None:
        raise MemberActionError(400, 'no mute role is configured for this server — set one in the Moderation tab')
    role = guild.get_role(role_id)
    if role is None:
        raise MemberActionError(400, 'the configured mute role no longer exists')
    if guild.me.top_role <= role:
        raise MemberActionError(400, 'the mute role is equal to or above my highest role')
    if moderator.id != guild.owner_id and moderator.top_role <= role:
        # As the mute command's check: a moderator may not hand out a role they could not give.
        raise MemberActionError(403, 'your highest role must be above the mute role')
    if member.get_role(role_id) is not None:
        raise MemberActionError(400, 'member is already muted')

    # Not muted right now, so a tempmute timer left over (role removed by hand) is stale and
    # would end this mute early.
    await bot.timers.delete_member_timer('tempmute', guild.id, member.id)
    await member.add_roles(role, reason=reason)

    if duration is None:
        return 'mute'

    expires = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=duration)
    await bot.timers.create(expires, 'tempmute', guild.id, moderator.id, member.id, role_id)
    return 'tempmute'


async def lift_mute(bot, guild: discord.Guild, member: discord.Member, reason: str | None) -> None:
    """Removes the mute role and cancels any pending tempmute expiry."""
    config = await bot.db.get_guild_config(guild.id)
    role_id = config.mute_role_id if config else None
    if role_id is None:
        raise MemberActionError(400, 'no mute role is configured for this server')
    if member.get_role(role_id) is None:
        raise MemberActionError(400, 'member is not muted')

    await member.remove_roles(discord.Object(id=role_id), reason=reason)
    await bot.timers.delete_member_timer('tempmute', guild.id, member.id)


async def apply_member_action(
    bot,
    guild: discord.Guild,
    moderator: discord.Member,
    user_id: int,
    *,
    action: str,
    reason: str | None = None,
    duration: int | None = None,
    delete_message_days: int = 0,
    limit: int = 100,
) -> dict:
    """Apply one moderation action to a member on behalf of an already-resolved ``moderator``.

    Every action that changes a member's standing is recorded as a modlog case through the
    same ``mod_action`` dispatch the in-Discord commands use. Raises :class:`MemberActionError`.
    """
    if action == 'mute':
        # Before the role goes on, so a bad duration never leaves a permanent mute behind.
        validate_mute_duration(duration)

    # Unban and purge work on an id (the user may have left); the rest need the member.
    member = guild.get_member(user_id)
    if member is None and action not in ('unban', 'purge'):
        raise MemberActionError(404, 'member not found')
    target = member if member is not None else discord.Object(id=user_id)
    # Purging your own messages is harmless, so self is the one target the rank rule lets through.
    own_purge = action == 'purge' and user_id == moderator.id
    if not own_purge and (
        error := hierarchy_error(moderator, target, guild.me, action=action, check_bot=action in HIERARCHY_ACTIONS)
    ):
        raise MemberActionError(403, error)
    if action == 'timeout' and member is not None and member.guild_permissions.administrator:
        raise MemberActionError(400, 'Discord does not allow timing out administrators')

    delete_days = max(0, min(7, delete_message_days))
    extra: dict = {}
    # The recorded case can differ from the requested action (a mute with a duration is a
    # tempmute), so actions set this rather than assuming action.
    case_action = action
    case_expires: datetime.datetime | None = None

    try:
        if action == 'unban':
            await guild.unban(discord.Object(id=user_id), reason=reason)
            # As the unban command does: an early unban ends the tempban, so its timer must not lift a later ban.
            await bot.timers.delete_member_timer('tempban', guild.id, user_id)
        elif action == 'purge':
            extra['deleted'] = await purge_member_messages(guild, user_id, max(1, min(500, limit)), reason)
        else:
            assert member is not None
            if action == 'kick':
                await member.kick(reason=reason)
            elif action == 'ban':
                await member.ban(reason=reason, delete_message_seconds=delete_days * 86400)
                # A permanent ban replaces any tempban, so its expiry must not lift this one later.
                await bot.timers.delete_member_timer('tempban', guild.id, user_id)
            elif action == 'softban':
                from app.cogs.moderation.infractions import softban

                wipe = (delete_days or 1) * 86400
                if not await softban(bot, guild, member, reason=reason, delete_message_seconds=wipe):
                    # The ban stands: record what happened, not a softban, and say so instead of failing.
                    case_action = 'ban'
                    extra['warning'] = 'banned, but lifting the ban again failed, so the member is still banned'
            elif action == 'timeout':
                delta = validate_timeout_duration(duration)
                await member.timeout(delta, reason=reason)
                case_expires = datetime.datetime.now(datetime.UTC) + delta
                extra['until'] = case_expires.isoformat()
            elif action == 'untimeout':
                if member.timed_out_until is None:
                    raise MemberActionError(400, 'member is not timed out')
                await member.timeout(None, reason=reason)
            elif action == 'mute':
                case_action = await apply_mute(bot, guild, member, moderator, reason, duration)
                if duration is not None:
                    case_expires = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=duration)
            elif action == 'unmute':
                await lift_mute(bot, guild, member, reason)
            elif action == 'warn':
                if member.bot:
                    raise MemberActionError(400, 'cannot warn a bot')
                from app.cogs.modlog.ui import send_warn_dm

                # A warning is only a case plus a courtesy DM — closed DMs are not an error.
                # The case is recorded by the dispatch below, so this is the next warn on record.
                from app.cogs.modlog.warns import active_warn_count

                rows = await bot.db.cases.get_warn_rows(guild.id, user_id)
                warns = active_warn_count(row['expires_at'] for row in rows)
                await send_warn_dm(member, reason, warn_count=warns + 1)
    except discord.Forbidden:
        raise MemberActionError(403, f'I am missing the permissions to {action} this member') from None
    except discord.HTTPException as e:
        raise MemberActionError(400, str(e)) from None

    # Purging messages is a cleanup, not a standing change — it gets no case.
    if action != 'purge':
        args = (guild.id, case_action, user_id, moderator.id, reason)
        bot.dispatch('mod_action', *args, *((case_expires,) if case_expires else ()))

    return {'ok': True, 'action': case_action, **extra}
