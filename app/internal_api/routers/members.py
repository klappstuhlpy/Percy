"""Internal API members endpoints (FastAPI router)."""
from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..dependencies import BotDep, GuildDep, verify_token

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Members"], dependencies=[Depends(verify_token)])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class MemberActionBody(BaseModel):
    action: str
    reason: str | None = None
    moderator_id: str | int | None = None


class MemberRolesBody(BaseModel):
    add: list[str] = []
    remove: list[str] = []


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

    members = []
    for member in all_members[:limit]:
        members.append({
            'id': str(member.id),
            'name': member.name,
            'display_name': member.display_name,
            'avatar_url': member.display_avatar.url,
            'joined_at': member.joined_at.isoformat() if member.joined_at else None,
            'roles': [str(r.id) for r in member.roles if r != guild.default_role],
            'bot': member.bot,
        })

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
    moderator_id = int(body.moderator_id) if body.moderator_id else None

    if body.action not in ('kick', 'ban', 'unban'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='action must be kick, ban, or unban')

    if body.action == 'unban':
        try:
            user = await bot.fetch_user(user_id)
            await guild.unban(user, reason=body.reason)
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    else:
        member = guild.get_member(user_id)
        if member is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='member not found')

        bot_member = guild.me
        if member.top_role >= bot_member.top_role:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='cannot moderate a member with equal or higher role',
            )

        if body.action == 'kick':
            await member.kick(reason=body.reason)
        elif body.action == 'ban':
            await member.ban(reason=body.reason)

    bot.dispatch('mod_action', guild.id, body.action, user_id, moderator_id, body.reason)
    return {'ok': True}


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
