"""Leveling configuration, leaderboard, XP history, and role management endpoints."""
from __future__ import annotations

import discord
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..dependencies import BotDep, GuildDep, verify_token

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Leveling"], dependencies=[Depends(verify_token)])

# Milestone reward roles created by the dashboard "preset" button.
# (level threshold, role name, RGB colour) — a cool-to-warm gradient up to 100.
_PRESET_LEVEL_ROLES = (
    (5, 'Newcomer', 0x95A5A6),
    (10, 'Member', 0x3498DB),
    (15, 'Regular', 0x1ABC9C),
    (20, 'Active', 0x2ECC71),
    (30, 'Veteran', 0xF1C40F),
    (40, 'Elite', 0xE67E22),
    (50, 'Master', 0xE74C3C),
    (60, 'Champion', 0x9B59B6),
    (70, 'Legend', 0xE91E63),
    (80, 'Mythic', 0x00BCD4),
    (90, 'Ascended', 0xFF5722),
    (100, 'Immortal', 0xFFD700),
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PatchLevelingUserBody(BaseModel):
    level: int | None = None
    xp: int | None = None


class PatchLevelingConfigBody(BaseModel):
    enabled: bool | None = None
    voice_enabled: bool | None = None
    voice_xp: int | None = None
    level_up_message: str | None = None
    level_up_channel: int | None = None
    role_stack: bool | None = None
    factor: float | None = None
    delete_after_leave: bool | None = None
    base: int | None = None
    min_gain: int | None = None
    max_gain: int | None = None
    cooldown_per: int | None = None
    special_level_up_messages: dict | None = None

    model_config = {"extra": "ignore"}


class PostLevelingRolesBody(BaseModel):
    level: int
    role_id: int | None = None


class PostLevelingMultipliersBody(BaseModel):
    type: str
    id: int
    multiplier: float | None = None


class PostLevelingBlacklistBody(BaseModel):
    type: str
    id: int
    action: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/leveling/config")
async def get_leveling_config(guild: GuildDep, bot: BotDep) -> dict:
    record = await bot.db.leveling.get_guild_config_record(guild.id)
    if record is None:
        return {'enabled': False, 'configured': False}

    return {
        'enabled': record.get('enabled', False),
        'configured': True,
        # 0 = don't send, 1 = source channel, 2 = DM, else channel id
        'level_up_channel': int(record.get('level_up_channel') or 1),
        'level_up_message': record.get('level_up_message'),
        'special_level_up_messages': record.get('special_level_up_messages', {}),
        'blacklisted_roles': record.get('blacklisted_roles', []),
        'blacklisted_channels': record.get('blacklisted_channels', []),
        'blacklisted_users': record.get('blacklisted_users', []),
        'level_roles': record.get('level_roles', {}),
        'multiplier_roles': record.get('multiplier_roles', {}),
        'multiplier_channels': record.get('multiplier_channels', {}),
        'role_stack': record.get('role_stack', False),
        'voice_enabled': record.get('voice_enabled', False),
        'delete_after_leave': record.get('delete_after_leave', False),
        'factor': record.get('factor', 1.0),
        'base': record.get('base', 100),
        'min_gain': record.get('min_gain', 8),
        'max_gain': record.get('max_gain', 15),
        'cooldown_per': record.get('cooldown_per', 40),
    }


@router.get("/leveling/leaderboard")
async def get_leveling_leaderboard(
    guild: GuildDep,
    bot: BotDep,
    limit: int = Query(default=25, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    records = await bot.db.leveling.get_leaderboard(guild.id, limit=limit + offset)

    entries = []
    for record in records:
        user_id = record['user_id']
        member = guild.get_member(user_id)
        entries.append({
            'user_id': str(user_id),
            'username': member.display_name if member else f'Unknown ({user_id})',
            'avatar_url': member.display_avatar.url if member else None,
            'level': record['level'],
            'xp': record['xp'],
            'total_xp': record.get('total_xp', record['xp']),
        })

    total = len(entries)
    entries = entries[offset:offset + limit]
    return {'entries': entries, 'total': total}


@router.get("/leveling/xp-history")
async def get_leveling_xp_history(
    guild: GuildDep,
    bot: BotDep,
    days: int = Query(default=30, ge=1, le=365),
) -> dict:
    records = await bot.db.leveling.get_xp_history(guild.id, days=days)

    points = [
        {
            'day': record['day'].isoformat(),
            'total_xp': int(record['total_xp']),
            'gainers': int(record['gainers']),
        }
        for record in records
    ]
    return {'points': points, 'days': days}


@router.patch("/leveling/users/{user_id}")
async def patch_leveling_user(guild: GuildDep, bot: BotDep, user_id: int, body: PatchLevelingUserBody) -> dict:
    if body.level is None and body.xp is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='must specify level or xp')

    updates: dict[str, object] = {}
    if body.level is not None:
        updates['level'] = body.level
    if body.xp is not None:
        updates['xp'] = body.xp

    await bot.db.leveling.get_or_create_user_level(user_id, guild.id)
    await bot.db.leveling.update_user_level(user_id, guild.id, updates)
    return {'ok': True}


@router.patch("/leveling/config")
async def patch_leveling_config(guild: GuildDep, bot: BotDep, body: PatchLevelingConfigBody) -> dict:
    updates: dict[str, object] = body.model_dump(exclude_none=True)

    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='no valid fields to update')

    record = await bot.db.leveling.get_guild_config_record(guild.id)
    if record is None:
        await bot.db.leveling.create_guild_config(guild.id, updates.get('enabled', False))
        record = await bot.db.leveling.get_guild_config_record(guild.id)

    await bot.db.leveling.update_guild_config(guild.id, updates)
    return {'ok': True}


@router.post("/leveling/roles")
async def post_leveling_roles(guild: GuildDep, bot: BotDep, body: PostLevelingRolesBody) -> dict:
    record = await bot.db.leveling.get_guild_config_record(guild.id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='leveling not configured')

    level_roles = dict(record.get('level_roles') or {})
    if body.role_id:
        level_roles[str(body.role_id)] = body.level
    else:
        level_roles = {k: v for k, v in level_roles.items() if v != body.level}

    await bot.db.leveling.update_guild_config(guild.id, {'level_roles': level_roles})
    return {'ok': True}


@router.post("/leveling/roles/preset")
async def create_leveling_role_preset(guild: GuildDep, bot: BotDep) -> dict:
    """Create the preset milestone reward roles (levels 5-100) and register them.

    Idempotent by role name: an existing role with the same name is reused
    rather than duplicated, so re-running only fills in what's missing.
    """
    record = await bot.db.leveling.get_guild_config_record(guild.id)
    if record is None:
        await bot.db.leveling.create_guild_config(guild.id, False)
        record = await bot.db.leveling.get_guild_config_record(guild.id)

    level_roles = dict(record.get('level_roles') or {})
    existing = {role.name.casefold(): role for role in guild.roles}
    created = 0
    try:
        for level, name, colour in _PRESET_LEVEL_ROLES:
            role = existing.get(name.casefold())
            if role is None:
                role = await guild.create_role(
                    name=name,
                    colour=discord.Colour(colour),
                    reason='Leveling preset roles (dashboard)',
                )
                existing[name.casefold()] = role
                created += 1
            level_roles[str(role.id)] = level
    except discord.Forbidden:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='bot is missing the Manage Roles permission')
    except discord.HTTPException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'failed to create roles: {exc}')

    await bot.db.leveling.update_guild_config(guild.id, {'level_roles': level_roles})
    return {'ok': True, 'created': created}


@router.post("/leveling/multipliers")
async def post_leveling_multipliers(guild: GuildDep, bot: BotDep, body: PostLevelingMultipliersBody) -> dict:
    if body.type not in ('role', 'channel'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='type must be role or channel')

    record = await bot.db.leveling.get_guild_config_record(guild.id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='leveling not configured')

    field = 'multiplier_roles' if body.type == 'role' else 'multiplier_channels'
    current = dict(record.get(field) or {})
    if body.multiplier is not None and body.multiplier > 0:
        current[str(body.id)] = body.multiplier
    else:
        current.pop(str(body.id), None)

    await bot.db.leveling.update_guild_config(guild.id, {field: current})
    return {'ok': True}


@router.post("/leveling/blacklist")
async def post_leveling_blacklist(guild: GuildDep, bot: BotDep, body: PostLevelingBlacklistBody) -> dict:
    if body.type not in ('role', 'channel', 'user'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='type must be role, channel, or user',
        )
    if body.action not in ('add', 'remove'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='action must be add or remove',
        )

    record = await bot.db.leveling.get_guild_config_record(guild.id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='leveling not configured')

    field_map = {'role': 'blacklisted_roles', 'channel': 'blacklisted_channels', 'user': 'blacklisted_users'}
    field = field_map[body.type]
    current = set(record.get(field) or [])
    if body.action == 'add':
        current.add(body.id)
    else:
        current.discard(body.id)

    await bot.db.leveling.update_guild_config(guild.id, {field: list(current)})
    return {'ok': True}
