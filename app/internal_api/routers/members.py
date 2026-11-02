"""Internal API members endpoints (FastAPI router)."""
from __future__ import annotations

import asyncio
import base64
import datetime
from contextlib import suppress

import discord
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.cogs.moderation.member_actions import MemberActionError, apply_member_action, scan_member_messages
from app.services.notifications import ACTION_PERMISSIONS
from app.utils.checks import hierarchy_error

from ..dependencies import BotDep, GuildDep, verify_token
from ..helpers import parse_id, resolve_moderator, role_error

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Members"], dependencies=[Depends(verify_token)])

ACTIONS = frozenset(ACTION_PERMISSIONS)


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
    #: The channel, for ``channelmute`` / ``channelunmute``.
    channel_id: str | int | None = None
    #: The new nickname, for ``nick`` (blank resets) and ``nickfreeze``.
    nickname: str | None = None


class MemberRolesBody(BaseModel):
    add: list[str] = []
    remove: list[str] = []
    #: The dashboard user making the change. Optional until every dashboard sends it; when
    #: present they need Manage Roles and may only touch roles below their own top role.
    moderator_id: str | int | None = None


# ---------------------------------------------------------------------------
# Response models - the source of truth for percy-client's generated types
# (dashboard/crates/percy-client/GENERATION.md). Class names are the Rust type names.
# ---------------------------------------------------------------------------


class Member(BaseModel):
    id: str
    name: str
    display_name: str
    avatar_url: str
    joined_at: str | None
    roles: list[str]
    bot: bool


class MembersResponse(BaseModel):
    members: list[Member]
    total: int


class MemberRoleBadge(BaseModel):
    id: str
    name: str
    color: int


class MemberLeveling(BaseModel):
    level: int
    xp: int
    messages: int
    rank: int


class MemberSelfLeveling(MemberLeveling):
    total_xp: int


class MemberCase(BaseModel):
    case_index: int
    action: str
    reason: str | None
    moderator_id: str | None
    moderator_name: str | None
    created_at: str | None


class MemberTopCommand(BaseModel):
    command: str
    uses: int


class MemberCommandStats(BaseModel):
    total_commands: int
    first_command_at: str | None
    top_commands: list[MemberTopCommand]


class NameHistoryEntry(BaseModel):
    name: str
    changed_at: str


class MemberNameHistory(BaseModel):
    usernames: list[NameHistoryEntry]
    nicknames: list[NameHistoryEntry]


class MemberOwnedTag(BaseModel):
    id: int
    name: str
    uses: int


class MemberEconomy(BaseModel):
    cash: int
    bank: int
    total: int


class MemberDetail(BaseModel):
    """Aggregated profile for one user (the lookup page). ``in_guild`` is false for a user who left."""

    id: str
    name: str
    display_name: str
    avatar_url: str
    joined_at: str | None
    created_at: str
    roles: list[MemberRoleBadge]
    bot: bool
    in_guild: bool
    top_role: str | None
    top_role_color: int
    join_position: int | None
    member_count: int
    mutual_guilds: int
    permissions: int
    boosting_since: str | None
    public_flags: list[str]
    status: str | None
    muted: bool
    mute_role_configured: bool
    timed_out_until: str | None
    leveling: MemberLeveling | None
    cases: list[MemberCase]
    case_count: int
    warning_count: int
    command_stats: MemberCommandStats | None
    last_seen: str | None
    names: MemberNameHistory | None
    avatar_count: int
    owned_tags: list[MemberOwnedTag]


class MemberSelf(BaseModel):
    """Personal profile for a non-admin member: leveling, economy, command stats; no cases."""

    id: str
    name: str
    display_name: str
    avatar_url: str
    joined_at: str | None
    created_at: str
    roles: list[MemberRoleBadge]
    top_role: str | None
    top_role_color: int
    join_position: int | None
    member_count: int
    boosting_since: str | None
    leveling: MemberSelfLeveling | None
    economy: MemberEconomy | None
    command_stats: MemberCommandStats | None
    owned_tags: list[MemberOwnedTag]


class MemberActionResult(BaseModel):
    """Success body of a member action; failures are HTTP errors with their own status."""

    ok: bool
    #: The case action recorded (a mute with a duration is a ``tempmute``).
    action: str
    #: ``purge``: how many messages were deleted.
    deleted: int | None = None
    #: ``softban`` whose unban failed: the member is still banned.
    warning: str | None = None
    #: ``timeout``: ISO-8601 expiry.
    until: str | None = None


class MemberMessage(BaseModel):
    id: str
    channel_id: str
    channel_name: str
    content: str
    created_at: str
    edited_at: str | None
    jump_url: str
    attachments: list[str]
    embed_count: int


class MemberMessages(BaseModel):
    """A bounded live scan: ``partial`` means some channels were skipped."""

    messages: list[MemberMessage]
    scanned_channels: int
    partial: bool


class MemberRolesResult(BaseModel):
    ok: bool


class AvatarEntry(BaseModel):
    #: Base64-encoded image bytes.
    image: str
    changed_at: str | None


class AvatarHistoryResponse(BaseModel):
    avatars: list[AvatarEntry]
    total: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _channel_id(value: str | int | None) -> int | None:
    """The body's channel snowflake (JS sends ids as strings), 400 on anything else."""
    if value is None or value == '':
        return None
    try:
        return int(value)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='channel_id must be a snowflake') from None


def _message_payload(message: discord.Message) -> MemberMessage:
    return MemberMessage.model_validate({
        'id': str(message.id),
        'channel_id': str(message.channel.id),
        'channel_name': getattr(message.channel, 'name', 'unknown'),
        'content': message.content,
        'created_at': message.created_at.isoformat(),
        'edited_at': message.edited_at.isoformat() if message.edited_at else None,
        'jump_url': message.jump_url,
        'attachments': [a.url for a in message.attachments],
        'embed_count': len(message.embeds),
    })


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/members")
async def get_guild_members(
    guild: GuildDep,
    limit: int = Query(default=100, ge=1, le=1000),
    after: int = Query(default=0),
    search: str = Query(default=""),
) -> MembersResponse:
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

    return MembersResponse.model_validate({'members': members, 'total': len(all_members)})


@router.get("/members/{user_id}/detail")
async def get_member_detail(
    user_id: int,
    bot: BotDep,
    guild: GuildDep,
) -> MemberDetail:
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

    return MemberDetail.model_validate({
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
    })


@router.get("/members/{user_id}/self")
async def get_member_self(
    user_id: int,
    bot: BotDep,
    guild: GuildDep,
) -> MemberSelf:
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

    return MemberSelf.model_validate(
        {**identity, 'leveling': leveling, 'economy': economy, 'command_stats': command_stats, 'owned_tags': owned_tags}
    )


@router.post("/members/{user_id}/action", response_model_exclude_none=True)
async def member_action(
    user_id: int,
    body: MemberActionBody,
    bot: BotDep,
    guild: GuildDep,
) -> MemberActionResult:
    """Apply one moderation action to a member.

    Every action that changes a member's standing is recorded as a modlog case through the
    same ``mod_action`` dispatch the in-Discord commands use, so dashboard and bot actions
    land in one history (see :func:`apply_member_action`).

    ``moderator_id`` is required: the acting moderator must hold the permission the matching
    command requires and outrank the member, by the same rule the commands use.
    """
    if body.action not in ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'action must be one of: {", ".join(sorted(ACTIONS))}',
        )
    moderator = await resolve_moderator(guild, body.moderator_id, ACTION_PERMISSIONS[body.action])
    try:
        return MemberActionResult.model_validate(await apply_member_action(
            bot,
            guild,
            moderator,
            user_id,
            action=body.action,
            reason=body.reason,
            duration=body.duration,
            delete_message_days=body.delete_message_days,
            limit=body.limit,
            channel_id=_channel_id(body.channel_id),
            nickname=body.nickname,
        ))
    except MemberActionError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None


@router.get("/members/{user_id}/messages")
async def get_member_messages(
    user_id: int,
    guild: GuildDep,
    limit: int = Query(default=25, ge=1, le=100),
    per_channel: int = Query(default=100, ge=1, le=200),
) -> MemberMessages:
    """A member's most recent messages, read live from channel history.

    Percy stores no message archive, so this is a bounded scan of the guild's most recently
    active channels (see :data:`MAX_SCAN_CHANNELS`) rather than a complete history.
    """
    messages, scanned = await scan_member_messages(guild, user_id, limit=limit, per_channel=per_channel)
    return MemberMessages(
        messages=[_message_payload(m) for m in messages],
        scanned_channels=scanned,
        partial=len(guild.text_channels) > scanned,
    )


@router.patch("/members/{user_id}/roles")
async def member_roles(
    user_id: int,
    body: MemberRolesBody,
    guild: GuildDep,
) -> MemberRolesResult:
    member = guild.get_member(user_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='member not found')

    if not body.add and not body.remove:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='must specify add or remove')

    # ponytail: optional until the deployed dashboard sends it; make it required after that deploy.
    moderator = None
    if body.moderator_id not in (None, ''):
        moderator = await resolve_moderator(guild, body.moderator_id, 'manage_roles')
        # Discord's own rule: you cannot edit the roles of a member who outranks you (self excepted).
        if moderator.id != member.id and (
            error := hierarchy_error(moderator, member, guild.me, action='change the roles of', check_bot=False)
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error)

    add_ids = {parse_id(rid, 'add') for rid in body.add}
    remove_ids = {parse_id(rid, 'remove') for rid in body.remove}
    unknown = (add_ids | remove_ids) - {r.id for r in guild.roles}
    if unknown:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'unknown role id: {min(unknown)}')
    roles_to_add = [r for r in guild.roles if r.id in add_ids]
    roles_to_remove = [r for r in guild.roles if r.id in remove_ids]
    # Every role must pass, or nothing changes.
    for role in (*roles_to_add, *roles_to_remove):
        if error := role_error(role, moderator):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error)

    reason = f'Dashboard role update by {moderator} ({moderator.id})' if moderator else 'Dashboard role update'
    try:
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason=reason)
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason=reason)
    except discord.Forbidden:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="I am missing the permissions to change this member's roles"
        ) from None
    except discord.HTTPException as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from None

    return MemberRolesResult(ok=True)


@router.get("/members/{user_id}/avatars")
async def get_member_avatars(
    user_id: int,
    bot: BotDep,
    limit: int = Query(default=20, ge=1, le=50),
) -> AvatarHistoryResponse:
    records = await bot.db.stats.get_avatar_history(user_id, limit=limit)
    avatars = [
        {
            'image': base64.b64encode(record['avatar']).decode(),
            'changed_at': record['changed_at'].isoformat() if record['changed_at'] else None,
        }
        for record in records
    ]
    return AvatarHistoryResponse.model_validate({'avatars': avatars, 'total': len(records)})
