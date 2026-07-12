"""Internal API members endpoints (FastAPI router)."""
from __future__ import annotations

import asyncio
import base64
import datetime
from contextlib import suppress

import discord
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..dependencies import BotDep, GuildDep, verify_token
from ..helpers import validate_timeout_duration

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Members"], dependencies=[Depends(verify_token)])

# Percy keeps no message archive (the gateway cache holds 10 messages), so both the message
# viewer and the purge action read live channel history. The scan is bounded on both axes:
# only the most recently active channels are visited, and only a slice of each is read.
MAX_SCAN_CHANNELS = 20
MAX_SCAN_CONCURRENCY = 5

# Discord refuses to bulk-delete messages older than two weeks.
BULK_DELETE_MAX_AGE = datetime.timedelta(days=14)

ACTIONS = frozenset(
    {'kick', 'ban', 'unban', 'softban', 'warn', 'mute', 'unmute', 'timeout', 'untimeout', 'purge'}
)
# Actions that move a member down the hierarchy — refused when the target outranks Percy.
HIERARCHY_ACTIONS = frozenset({'kick', 'ban', 'softban', 'mute', 'timeout'})


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class MemberActionBody(BaseModel):
    action: str
    reason: str | None = None
    moderator_id: str | int | None = None
    #: Seconds, for ``timeout`` (required) and ``mute`` (optional — a mute with a
    #: duration becomes a ``tempmute`` and is lifted by a timer).
    duration: int | None = None
    #: Days of message history to wipe, for ``ban`` and ``softban`` (Discord allows 0-7).
    delete_message_days: int = 0
    #: How many of the member's recent messages ``purge`` should delete.
    limit: int = 100


class MemberRolesBody(BaseModel):
    add: list[str] = []
    remove: list[str] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _scan_member_messages(
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


def _message_payload(message: discord.Message) -> dict:
    return {
        'id': str(message.id),
        'channel_id': str(message.channel.id),
        'channel_name': getattr(message.channel, 'name', 'unknown'),
        'content': message.content,
        'created_at': message.created_at.isoformat(),
        'edited_at': message.edited_at.isoformat() if message.edited_at else None,
        'jump_url': message.jump_url,
        'attachments': [a.url for a in message.attachments],
        'embed_count': len(message.embeds),
    }


async def _purge_member_messages(guild: discord.Guild, user_id: int, limit: int, reason: str | None) -> int:
    """Deletes up to ``limit`` of a member's recent messages. Returns how many were removed."""
    messages, _ = await _scan_member_messages(guild, user_id, limit=limit)
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


async def _apply_mute(bot, guild: discord.Guild, member: discord.Member, body: MemberActionBody) -> str:
    """Adds the configured mute role, arming an expiry timer when a duration is given.

    Returns the action recorded on the case (``mute`` or ``tempmute``).
    """
    config = await bot.db.get_guild_config(guild.id)
    role_id = config.mute_role_id if config else None
    if role_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='no mute role is configured for this server — set one in the Moderation tab',
        )
    role = guild.get_role(role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='the configured mute role no longer exists')
    if guild.me.top_role <= role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='the mute role is equal to or above my highest role',
        )
    if member.get_role(role_id) is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='member is already muted')

    await member.add_roles(role, reason=body.reason)

    if body.duration is None:
        return 'mute'

    moderator_id = int(body.moderator_id) if body.moderator_id else member.id
    expires = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=body.duration)
    await bot.timers.create(expires, 'tempmute', guild.id, moderator_id, member.id, role_id)
    return 'tempmute'


async def _lift_mute(bot, guild: discord.Guild, member: discord.Member, reason: str | None) -> None:
    """Removes the mute role and cancels any pending tempmute expiry."""
    config = await bot.db.get_guild_config(guild.id)
    role_id = config.mute_role_id if config else None
    if role_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='no mute role is configured for this server')
    if member.get_role(role_id) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='member is not muted')

    await member.remove_roles(discord.Object(id=role_id), reason=reason)
    with suppress(Exception):
        await bot.timers.delete_member_timer('tempmute', guild.id, member.id)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/members")
async def get_guild_members(
    guild: GuildDep,
    limit: int = Query(default=100, le=1000),
    after: int = Query(default=0),
    search: str = Query(default=""),
) -> dict:
    search_lower = search.lower()
    all_members = sorted((m for m in guild.members if m.id > after), key=lambda m: m.id)

    if search_lower:
        all_members = [m for m in all_members if search_lower in m.name.lower() or search_lower in m.display_name.lower()]

    members = [
        {
            'id': str(member.id),
            'name': member.name,
            'display_name': member.display_name,
            'avatar_url': member.display_avatar.url,
            'joined_at': member.joined_at.isoformat() if member.joined_at else None,
            'roles': [str(r.id) for r in member.roles if r != guild.default_role],
            'bot': member.bot,
        }
        for member in all_members[:limit]
    ]

    return {'members': members, 'total': len(all_members)}


@router.get("/members/{user_id}/detail")
async def get_member_detail(
    user_id: int,
    bot: BotDep,
    guild: GuildDep,
) -> dict:
    """Aggregated profile for one user: identity, leveling, moderation history, stats."""
    member = guild.get_member(user_id)

    if member is not None:
        sorted_members = sorted((m for m in guild.members if m.joined_at), key=lambda m: m.joined_at)
        join_position = next((i for i, m in enumerate(sorted_members, 1) if m.id == member.id), None)
        # Mute state drives the dashboard's action menu (offer mute vs. unmute), so it is
        # resolved against the guild's configured mute role rather than guessed from roles.
        guild_config = await bot.db.get_guild_config(guild.id)
        mute_role_id = guild_config.mute_role_id if guild_config else None
        identity = {
            'id': str(member.id),
            'name': member.name,
            'display_name': member.display_name,
            'avatar_url': member.display_avatar.url,
            'joined_at': member.joined_at.isoformat() if member.joined_at else None,
            'created_at': member.created_at.isoformat(),
            'roles': [
                {'id': str(r.id), 'name': r.name, 'color': r.color.value}
                for r in member.roles if r != guild.default_role
            ],
            'bot': member.bot,
            'in_guild': True,
            'top_role': member.top_role.name if member.top_role != guild.default_role else None,
            'top_role_color': member.top_role.color.value if member.top_role != guild.default_role else 0,
            'join_position': join_position,
            'member_count': guild.member_count or len(guild.members),
            'mutual_guilds': len(member.mutual_guilds),
            'permissions': member.guild_permissions.value,
            'boosting_since': member.premium_since.isoformat() if member.premium_since else None,
            'public_flags': [flag.replace('_', ' ') for flag, value in member.public_flags if value],
            'status': member.status.name if hasattr(member, 'status') else None,
            'muted': mute_role_id is not None and member.get_role(mute_role_id) is not None,
            'mute_role_configured': mute_role_id is not None,
            'timed_out_until': member.timed_out_until.isoformat() if member.timed_out_until else None,
        }
    else:
        try:
            user = await bot.fetch_user(user_id)
        except Exception:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='user not found')
        identity = {
            'id': str(user.id),
            'name': user.name,
            'display_name': user.display_name,
            'avatar_url': user.display_avatar.url,
            'joined_at': None,
            'created_at': user.created_at.isoformat(),
            'roles': [],
            'bot': user.bot,
            'in_guild': False,
            'top_role': None,
            'top_role_color': 0,
            'join_position': None,
            'member_count': guild.member_count or len(guild.members),
            'mutual_guilds': 0,
            'permissions': 0,
            'boosting_since': None,
            'public_flags': [flag.replace('_', ' ') for flag, value in user.public_flags if value],
            'status': None,
            'muted': False,
            'mute_role_configured': False,
            'timed_out_until': None,
        }

    # Leveling
    leveling = None
    level_record = await bot.db.leveling.get_user_level(user_id, guild.id)
    if level_record is not None:
        rank = await bot.db.leveling.get_rank(user_id, guild.id)
        leveling = {
            'level': level_record['level'], 'xp': level_record['xp'],
            'messages': level_record['messages'], 'rank': rank,
        }

    # Moderation cases
    case_records = await bot.db.cases.get_user_cases(guild.id, user_id, limit=50)
    cases = []
    for record in case_records:
        moderator_id = record['moderator_id']
        moderator_name = None
        if moderator_id is not None:
            mod = guild.get_member(moderator_id) or bot.get_user(moderator_id)
            moderator_name = mod.display_name if mod else f'Unknown ({moderator_id})'
        cases.append({
            'case_index': record['case_index'],
            'action': record['action'],
            'reason': record['reason'],
            'moderator_id': str(moderator_id) if moderator_id is not None else None,
            'moderator_name': moderator_name,
            'created_at': record['created_at'].isoformat() if record['created_at'] else None,
        })

    warning_count = sum(1 for c in cases if c['action'] == 'warn')

    # Command stats
    command_summary = await bot.db.stats.get_command_summary(guild.id, user_id)
    top_commands = await bot.db.stats.get_command_usage(guild_id=guild.id, author_id=user_id, group_by='command', limit=5)
    command_stats = {
        'total_commands': command_summary[0] if command_summary else 0,
        'first_command_at': command_summary[1].isoformat() if command_summary and command_summary[1] else None,
        'top_commands': [{'command': r['command'], 'uses': r['uses']} for r in top_commands] if top_commands else [],
    }

    # Presence / last seen
    presence_records = await bot.db.stats.get_presence_history(user_id, days=30)
    last_seen = presence_records[0]['changed_at'].isoformat() if presence_records else None

    # Name history
    name_history = await bot.db.stats.get_item_history(user_id, 'name')
    nickname_history = await bot.db.stats.get_item_history(user_id, 'nickname')
    names = {
        'usernames': [{'name': r[0], 'changed_at': r[1].isoformat()} for r in name_history[:10]],
        'nicknames': [{'name': r[0], 'changed_at': r[1].isoformat()} for r in nickname_history[:10]],
    }

    # Avatar count
    avatar_records = await bot.db.stats.get_avatar_history(user_id, limit=100)
    avatar_count = len(avatar_records)

    # Owned tags
    owned_tag_records = await bot.db.tags.get_owned_tags(guild.id, user_id)
    owned_tags = [
        {'id': r['id'], 'name': r['name'], 'uses': r.get('uses', 0)}
        for r in sorted(owned_tag_records, key=lambda r: r.get('uses', 0), reverse=True)[:50]
    ]

    return {
        **identity,
        'leveling': leveling,
        'cases': cases,
        'case_count': len(cases),
        'warning_count': warning_count,
        'command_stats': command_stats,
        'last_seen': last_seen,
        'names': names,
        'avatar_count': avatar_count,
        'owned_tags': owned_tags,
    }


@router.get("/members/{user_id}/self")
async def get_member_self(
    user_id: int,
    bot: BotDep,
    guild: GuildDep,
) -> dict:
    """Personal profile: leveling, economy, command stats -- no moderation cases."""
    member = guild.get_member(user_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='member not found')

    sorted_members = sorted((m for m in guild.members if m.joined_at), key=lambda m: m.joined_at)
    join_position = next((i for i, m in enumerate(sorted_members, 1) if m.id == member.id), None)

    identity = {
        'id': str(member.id),
        'name': member.name,
        'display_name': member.display_name,
        'avatar_url': member.display_avatar.url,
        'joined_at': member.joined_at.isoformat() if member.joined_at else None,
        'created_at': member.created_at.isoformat(),
        'roles': [
            {'id': str(r.id), 'name': r.name, 'color': r.color.value}
            for r in member.roles if r != guild.default_role
        ],
        'top_role': member.top_role.name if member.top_role != guild.default_role else None,
        'top_role_color': member.top_role.color.value if member.top_role != guild.default_role else 0,
        'join_position': join_position,
        'member_count': guild.member_count or len(guild.members),
        'boosting_since': member.premium_since.isoformat() if member.premium_since else None,
    }

    # Leveling
    leveling = None
    level_record = await bot.db.leveling.get_user_level(user_id, guild.id)
    if level_record is not None:
        rank = await bot.db.leveling.get_rank(user_id, guild.id)
        leveling = {
            'level': level_record['level'],
            'xp': level_record['xp'],
            'total_xp': level_record.get('total_xp', level_record['xp']),
            'messages': level_record['messages'],
            'rank': rank,
        }

    # Economy
    balance = await bot.db.get_user_balance(user_id, guild.id)
    economy = {
        'cash': balance.cash if balance else 0,
        'bank': balance.bank if balance else 0,
        'total': (balance.cash + balance.bank) if balance else 0,
    }

    # Command stats
    command_summary = await bot.db.stats.get_command_summary(guild.id, user_id)
    top_commands = await bot.db.stats.get_command_usage(guild_id=guild.id, author_id=user_id, group_by='command', limit=5)
    command_stats = {
        'total_commands': command_summary[0] if command_summary else 0,
        'first_command_at': command_summary[1].isoformat() if command_summary and command_summary[1] else None,
        'top_commands': [{'command': r['command'], 'uses': r['uses']} for r in top_commands] if top_commands else [],
    }

    # Owned tags
    owned_tag_records = await bot.db.tags.get_owned_tags(guild.id, user_id)
    owned_tags = [
        {'id': r['id'], 'name': r['name'], 'uses': r.get('uses', 0)}
        for r in sorted(owned_tag_records, key=lambda r: r.get('uses', 0), reverse=True)[:50]
    ]

    return {**identity, 'leveling': leveling, 'economy': economy, 'command_stats': command_stats, 'owned_tags': owned_tags}


@router.post("/members/{user_id}/action")
async def member_action(
    user_id: int,
    body: MemberActionBody,
    bot: BotDep,
    guild: GuildDep,
) -> dict:
    """Apply one moderation action to a member.

    Every action that changes a member's standing is recorded as a modlog case through the
    same ``mod_action`` dispatch the in-Discord commands use, so dashboard and bot actions
    land in one history.
    """
    moderator_id = int(body.moderator_id) if body.moderator_id else None

    if body.action not in ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'action must be one of: {", ".join(sorted(ACTIONS))}',
        )

    delete_days = max(0, min(7, body.delete_message_days))
    extra: dict = {}
    # The recorded case can differ from the requested action (a mute with a duration is a
    # tempmute), so actions set this rather than assuming body.action.
    case_action = body.action

    if body.action == 'unban':
        try:
            user = await bot.fetch_user(user_id)
            await guild.unban(user, reason=body.reason)
        except discord.HTTPException as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    else:
        member = guild.get_member(user_id)
        if member is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='member not found')

        if body.action in HIERARCHY_ACTIONS and member.top_role >= guild.me.top_role:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='cannot moderate a member with equal or higher role',
            )

        try:
            if body.action == 'kick':
                await member.kick(reason=body.reason)
            elif body.action == 'ban':
                await member.ban(reason=body.reason, delete_message_seconds=delete_days * 86400)
            elif body.action == 'softban':
                # Ban-then-unban: kicks the member and wipes their recent messages.
                await member.ban(reason=body.reason, delete_message_seconds=(delete_days or 1) * 86400)
                await guild.unban(member, reason=body.reason)
            elif body.action == 'timeout':
                delta = validate_timeout_duration(body.duration)
                await member.timeout(delta, reason=body.reason)
                extra['until'] = (datetime.datetime.now(datetime.UTC) + delta).isoformat()
            elif body.action == 'untimeout':
                if member.timed_out_until is None:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='member is not timed out')
                await member.timeout(None, reason=body.reason)
            elif body.action == 'mute':
                case_action = await _apply_mute(bot, guild, member, body)
            elif body.action == 'unmute':
                await _lift_mute(bot, guild, member, body.reason)
            elif body.action == 'warn':
                if member.bot:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='cannot warn a bot')
                # A warning is only a case plus a courtesy DM — closed DMs are not an error.
                with suppress(discord.HTTPException):
                    note = f': {body.reason}' if body.reason else '.'
                    await member.send(f'\N{WARNING SIGN} You were warned in **{guild.name}**{note}')
            elif body.action == 'purge':
                extra['deleted'] = await _purge_member_messages(
                    guild, user_id, max(1, min(500, body.limit)), body.reason
                )
        except discord.Forbidden:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'I am missing the permissions to {body.action} this member',
            )
        except discord.HTTPException as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Purging messages is a cleanup, not a standing change — it gets no case.
    if body.action != 'purge':
        bot.dispatch('mod_action', guild.id, case_action, user_id, moderator_id, body.reason)

    return {'ok': True, 'action': case_action, **extra}


@router.get("/members/{user_id}/messages")
async def get_member_messages(
    user_id: int,
    guild: GuildDep,
    limit: int = Query(default=25, le=100),
    per_channel: int = Query(default=100, le=200),
) -> dict:
    """A member's most recent messages, read live from channel history.

    Percy stores no message archive, so this is a bounded scan of the guild's most recently
    active channels (see :data:`MAX_SCAN_CHANNELS`) rather than a complete history.
    """
    messages, scanned = await _scan_member_messages(guild, user_id, limit=limit, per_channel=per_channel)
    return {
        'messages': [_message_payload(m) for m in messages],
        'scanned_channels': scanned,
        'partial': len(guild.text_channels) > scanned,
    }


@router.patch("/members/{user_id}/roles")
async def member_roles(
    user_id: int,
    body: MemberRolesBody,
    guild: GuildDep,
) -> dict:
    member = guild.get_member(user_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='member not found')

    if not body.add and not body.remove:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='must specify add or remove')

    reason = 'Dashboard role update'

    if body.add:
        roles_to_add = [r for r in guild.roles if str(r.id) in body.add]
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason=reason)

    if body.remove:
        roles_to_remove = [r for r in guild.roles if str(r.id) in body.remove]
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason=reason)

    return {'ok': True}


@router.get("/members/{user_id}/avatars")
async def get_member_avatars(
    user_id: int,
    bot: BotDep,
    limit: int = Query(default=20, le=50),
) -> dict:
    records = await bot.db.stats.get_avatar_history(user_id, limit=limit)
    avatars = [
        {
            'image': base64.b64encode(record['avatar']).decode(),
            'changed_at': record['changed_at'].isoformat() if record['changed_at'] else None,
        }
        for record in records
    ]
    return {'avatars': avatars, 'total': len(records)}
