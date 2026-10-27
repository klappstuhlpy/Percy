"""Leveling configuration, leaderboard, XP history, and role management endpoints."""
from __future__ import annotations

import discord
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.rendering.card_style import clean_style, style_options
from app.services.level_message import LEVEL_UP_VARIABLES, level_up_values, render_level_up

from ..dependencies import BotDep, GuildDep, verify_token
from ..helpers import invalidate_cog_cache

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
    card_style: dict | None = None
    card_locked: bool | None = None

    model_config = {"extra": "ignore"}


class LevelUpTestBody(BaseModel):
    user_id: int


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
        'card_style': record.get('card_style') or {},
        'card_locked': record.get('card_locked', False),
        'card_options': style_options(),
        'level_up_variables': [{'name': k, 'description': v} for k, v in LEVEL_UP_VARIABLES.items()],
    }


@router.get("/leveling/leaderboard")
async def get_leveling_leaderboard(
    guild: GuildDep,
    bot: BotDep,
    limit: int = Query(default=25, ge=1, le=100),
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


@router.get("/leveling/users/{user_id}")
async def get_leveling_user(guild: GuildDep, bot: BotDep, user_id: int) -> dict:
    """One member's level row (read-only; a member with no XP is level 0, no row is created)."""
    record = await bot.db.leveling.get_user_level(user_id, guild.id)
    if record is None:
        return {'user_id': str(user_id), 'level': 0, 'xp': 0, 'messages': 0, 'rank': 0}
    return {
        'user_id': str(user_id),
        'level': record['level'],
        'xp': record['xp'],
        'messages': record['messages'],
        'rank': await bot.db.leveling.get_rank(user_id, guild.id),
    }


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
    # levels.level is INTEGER and levels.xp BIGINT: out of range is a 500 from asyncpg, negative is nonsense.
    if body.level is not None and not 0 <= body.level <= 2**31 - 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='level must not be negative')
    if body.xp is not None and not 0 <= body.xp <= 2**63 - 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='xp must not be negative')

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
    if 'card_style' in updates:
        try:
            updates['card_style'] = clean_style(updates['card_style'])  # type: ignore[arg-type]
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    record = await bot.db.leveling.get_guild_config_record(guild.id)
    if record is None:
        await bot.db.leveling.create_guild_config(guild.id, updates.get('enabled', False))
        record = await bot.db.leveling.get_guild_config_record(guild.id)

    await bot.db.leveling.update_guild_config(guild.id, updates)
    invalidate_cog_cache(bot, 'Leveling', 'get_guild_level_config', guild.id)
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
    invalidate_cog_cache(bot, 'Leveling', 'get_guild_level_config', guild.id)
    return {'ok': True}


@router.post("/leveling/roles/preset")
async def create_leveling_role_preset(guild: GuildDep, bot: BotDep) -> dict:
    """Create the preset milestone reward roles (levels 5-100) and register them."""
    return {'ok': True, 'created': await _create_preset_roles(guild, bot)}


class SetupLevelingBody(BaseModel):
    #: 0 = no messages, 1 = where they talked, 2 = DM, otherwise a text channel id.
    level_up_channel: int
    preset_roles: bool = False


@router.post("/setup/leveling")
async def setup_leveling(guild: GuildDep, bot: BotDep, body: SetupLevelingBody) -> dict:
    """Turn leveling on in one step: announcement channel, optionally the milestone roles."""
    target = body.level_up_channel
    if target < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Pick a level-up destination.')
    if target > 2:
        channel = guild.get_channel(target)
        if not isinstance(channel, discord.TextChannel):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Pick a text channel.')
        perms = channel.permissions_for(guild.me)
        missing = [p for p in ('view_channel', 'send_messages', 'attach_files') if not getattr(perms, p)]
        if missing:
            names = ', '.join(p.replace('_', ' ').title() for p in missing)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f'Percy is missing {names} in #{channel.name}.'
            )
        text = f'Level-ups are announced in #{channel.name}'
    else:
        text = ('No level-up messages', 'Level-ups are announced where members talked', 'Level-ups are sent by DM')[target]
    if body.preset_roles and not guild.me.guild_permissions.manage_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Percy needs Manage Roles to create the milestone roles. Turn that option off or grant it.',
        )

    if await bot.db.leveling.get_guild_config_record(guild.id) is None:
        await bot.db.leveling.create_guild_config(guild.id, True)
    await bot.db.leveling.update_guild_config(guild.id, {'enabled': True, 'level_up_channel': target})
    created = await _create_preset_roles(guild, bot) if body.preset_roles else 0

    invalidate_cog_cache(bot, 'Leveling', 'get_guild_level_config', guild.id)
    message = f'Leveling is on. {text}.'
    if body.preset_roles:
        message += f' {created} milestone role{"s" if created != 1 else ""} created, the rest already existed.'
    return {'ok': True, 'message': message}


async def _create_preset_roles(guild: discord.Guild, bot: BotDep) -> int:
    """Create the preset milestone reward roles (levels 5-100) and register them; returns how many were new.

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
    invalidate_cog_cache(bot, 'Leveling', 'get_guild_level_config', guild.id)
    return created


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
    invalidate_cog_cache(bot, 'Leveling', 'get_guild_level_config', guild.id)
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
    invalidate_cog_cache(bot, 'Leveling', 'get_guild_level_config', guild.id)
    return {'ok': True}


@router.post("/leveling/test-message")
async def send_level_up_test(guild: GuildDep, bot: BotDep, body: LevelUpTestBody) -> dict:
    """Post the level-up message once, as it would look for ``user_id`` reaching level 5.

    A configured channel gets it there; "where they talked" and "DM" modes send it to the
    requesting member's DMs, since there is no message to reply to. Marked as a test.
    """
    record = await bot.db.leveling.get_guild_config_record(guild.id)
    template = record.get('level_up_message') if record else None
    if not template:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Set a level-up message first.')
    mode = int(record.get('level_up_channel') or 1)
    if mode == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Level-up messages are turned off.')

    member = guild.get_member(body.user_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='You are not a member of this server.')
    preview = render_level_up(template, level_up_values(member, 5))
    content = f'-# Test from the dashboard: this is how level-up messages look.\n{preview}'

    channel = guild.get_channel(mode) if mode > 2 else None
    try:
        if isinstance(channel, discord.abc.Messageable):
            await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
            return {'ok': True, 'message': f'Test message sent to #{channel.name}.'}  # type: ignore[union-attr]
        if mode > 2:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='The level-up channel was deleted. Pick a new one.')
        await member.send(content)
        return {'ok': True, 'message': 'Test message sent to your DMs.'}
    except discord.Forbidden:
        where = f'#{channel.name}' if channel is not None else 'your DMs'  # type: ignore[union-attr]
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Percy can't send messages to {where}.") from None
