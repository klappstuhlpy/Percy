"""InternalAPI leveling endpoints."""
from __future__ import annotations

from aiohttp import web

from .models import InternalAPIHandlers

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


class LevelingHandlers(InternalAPIHandlers):
    """Leveling-related internal API handlers."""

    async def _get_leveling_config(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        record = await self.bot.db.leveling.get_guild_config_record(guild_id)
        if record is None:
            return web.json_response({'enabled': False, 'configured': False})

        return web.json_response({
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
        })

    async def _get_leveling_leaderboard(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        limit = min(int(request.query.get('limit', '25')), 100)
        records = await self.bot.db.leveling.get_leaderboard(guild_id, limit=limit)

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

        return web.json_response({'entries': entries, 'total': len(entries)})

    async def _get_leveling_xp_history(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        days = max(1, min(int(request.query.get('days', '30')), 365))
        records = await self.bot.db.leveling.get_xp_history(guild_id, days=days)

        points = [
            {
                'day': record['day'].isoformat(),
                'total_xp': int(record['total_xp']),
                'gainers': int(record['gainers']),
            }
            for record in records
        ]
        return web.json_response({'points': points, 'days': days})

    async def _patch_leveling_user(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        user_id = int(request.match_info['user_id'])

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        level = body.get('level')
        xp = body.get('xp')

        if level is None and xp is None:
            raise web.HTTPBadRequest(text='must specify level or xp')

        updates: dict[str, object] = {}
        if level is not None:
            updates['level'] = int(level)
        if xp is not None:
            updates['xp'] = int(xp)

        await self.bot.db.leveling.get_or_create_user_level(user_id, guild_id)
        await self.bot.db.leveling.update_user_level(user_id, guild_id, updates)
        return web.json_response({'ok': True})

    async def _patch_leveling_config(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        allowed = {'enabled', 'voice_enabled', 'voice_xp', 'level_up_message', 'level_up_channel',
                   'role_stack', 'factor', 'delete_after_leave', 'base', 'min_gain', 'max_gain',
                   'cooldown_per', 'special_level_up_messages'}
        updates: dict[str, object] = {}
        for key, value in body.items():
            if key in allowed:
                updates[key] = value

        if not updates:
            raise web.HTTPBadRequest(text='no valid fields to update')

        record = await self.bot.db.leveling.get_guild_config_record(guild_id)
        if record is None:
            await self.bot.db.leveling.create_guild_config(guild_id, updates.get('enabled', False))
            record = await self.bot.db.leveling.get_guild_config_record(guild_id)

        await self.bot.db.leveling.update_guild_config(guild_id, updates)
        return web.json_response({'ok': True})

    async def _post_leveling_roles(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        level = body.get('level')
        role_id = body.get('role_id')
        if level is None:
            raise web.HTTPBadRequest(text='level is required')

        record = await self.bot.db.leveling.get_guild_config_record(guild_id)
        if record is None:
            raise web.HTTPBadRequest(text='leveling not configured')

        level_roles = dict(record.get('level_roles') or {})
        if role_id:
            level_roles[str(role_id)] = int(level)
        else:
            level_roles = {k: v for k, v in level_roles.items() if v != int(level)}

        await self.bot.db.leveling.update_guild_config(guild_id, {'level_roles': level_roles})
        return web.json_response({'ok': True})

    async def _create_leveling_role_preset(self, request: web.Request) -> web.Response:
        """Create the preset milestone reward roles (levels 5-100) and register them.

        Idempotent by role name: an existing role with the same name is reused
        rather than duplicated, so re-running only fills in what's missing.
        """
        import discord

        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        record = await self.bot.db.leveling.get_guild_config_record(guild_id)
        if record is None:
            await self.bot.db.leveling.create_guild_config(guild_id, False)
            record = await self.bot.db.leveling.get_guild_config_record(guild_id)

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
            raise web.HTTPForbidden(text='bot is missing the Manage Roles permission')
        except discord.HTTPException as exc:
            raise web.HTTPBadRequest(text=f'failed to create roles: {exc}')

        await self.bot.db.leveling.update_guild_config(guild_id, {'level_roles': level_roles})
        return web.json_response({'ok': True, 'created': created})

    async def _post_leveling_multipliers(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        mult_type = body.get('type')
        entity_id = body.get('id')
        multiplier = body.get('multiplier')

        if mult_type not in ('role', 'channel') or entity_id is None:
            raise web.HTTPBadRequest(text='type (role/channel) and id are required')

        record = await self.bot.db.leveling.get_guild_config_record(guild_id)
        if record is None:
            raise web.HTTPBadRequest(text='leveling not configured')

        field = 'multiplier_roles' if mult_type == 'role' else 'multiplier_channels'
        current = dict(record.get(field) or {})
        if multiplier is not None and float(multiplier) > 0:
            current[str(entity_id)] = float(multiplier)
        else:
            current.pop(str(entity_id), None)

        await self.bot.db.leveling.update_guild_config(guild_id, {field: current})
        return web.json_response({'ok': True})

    async def _post_leveling_blacklist(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        bl_type = body.get('type')
        entity_id = body.get('id')
        action = body.get('action')

        if bl_type not in ('role', 'channel', 'user') or entity_id is None or action not in ('add', 'remove'):
            raise web.HTTPBadRequest(text='type (role/channel/user), id, and action (add/remove) are required')

        record = await self.bot.db.leveling.get_guild_config_record(guild_id)
        if record is None:
            raise web.HTTPBadRequest(text='leveling not configured')

        field_map = {'role': 'blacklisted_roles', 'channel': 'blacklisted_channels', 'user': 'blacklisted_users'}
        field = field_map[bl_type]
        current = set(record.get(field) or [])
        eid = int(entity_id)
        if action == 'add':
            current.add(eid)
        else:
            current.discard(eid)

        await self.bot.db.leveling.update_guild_config(guild_id, {field: list(current)})
        return web.json_response({'ok': True})

