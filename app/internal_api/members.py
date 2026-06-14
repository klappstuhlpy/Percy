"""InternalAPI members endpoints."""
from __future__ import annotations

import base64

from aiohttp import web

from .models import InternalAPIHandlers


class MemberHandlers(InternalAPIHandlers):
    """Members-related internal API handlers."""

    async def _get_guild_members(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        # Paginated: ?limit=100&after=0
        limit = min(int(request.query.get('limit', '100')), 1000)
        after = int(request.query.get('after', '0'))

        members_iter = (m for m in guild.members if m.id > after)
        members = []
        for i, member in enumerate(sorted(members_iter, key=lambda m: m.id)):
            if i >= limit:
                break
            members.append({
                'id': str(member.id),
                'name': member.name,
                'display_name': member.display_name,
                'avatar_url': member.display_avatar.url,
                'joined_at': member.joined_at.isoformat() if member.joined_at else None,
                'roles': [str(r.id) for r in member.roles if r != guild.default_role],
                'bot': member.bot,
            })

        return web.json_response(members)

    async def _get_member_detail(self, request: web.Request) -> web.Response:
        """Aggregated profile for one user: identity, leveling, moderation history, stats."""
        guild_id = int(request.match_info['guild_id'])
        user_id = int(request.match_info['user_id'])

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        member = guild.get_member(user_id)

        # Identity — fall back to a bare user fetch when they have left the guild.
        if member is not None:
            # Join position
            sorted_members = sorted(
                (m for m in guild.members if m.joined_at),
                key=lambda m: m.joined_at,
            )
            join_position = next(
                (i for i, m in enumerate(sorted_members, 1) if m.id == member.id), None
            )

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
                user = await self.bot.fetch_user(user_id)
            except Exception:
                raise web.HTTPNotFound(text='user not found')
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

        # Leveling — read-only; absent row means the member has no XP yet.
        leveling = None
        level_record = await self.bot.db.leveling.get_user_level(user_id, guild_id)
        if level_record is not None:
            rank = await self.bot.db.leveling.get_rank(user_id, guild_id)
            leveling = {
                'level': level_record['level'],
                'xp': level_record['xp'],
                'messages': level_record['messages'],
                'rank': rank,
            }

        # Moderation history (newest first) with resolved moderator names.
        case_records = await self.bot.db.cases.get_user_cases(guild_id, user_id, limit=50)
        cases = []
        for record in case_records:
            moderator_id = record['moderator_id']
            moderator_name = None
            if moderator_id is not None:
                mod = guild.get_member(moderator_id) or self.bot.get_user(moderator_id)
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

        # Command usage stats for this user in this guild.
        command_summary = await self.bot.db.stats.get_command_summary(guild_id, user_id)
        top_commands = await self.bot.db.stats.get_command_usage(
            guild_id=guild_id, author_id=user_id, group_by='command', limit=5
        )
        command_stats = {
            'total_commands': command_summary[0] if command_summary else 0,
            'first_command_at': command_summary[1].isoformat() if command_summary and command_summary[1] else None,
            'top_commands': [
                {'command': r['command'], 'uses': r['uses']} for r in top_commands
            ] if top_commands else [],
        }

        # Last seen (most recent presence transition).
        presence_records = await self.bot.db.stats.get_presence_history(user_id, days=30)
        last_seen = presence_records[0]['changed_at'].isoformat() if presence_records else None

        # Name history (recent usernames and nicknames).
        name_history = await self.bot.db.stats.get_item_history(user_id, 'name')
        nickname_history = await self.bot.db.stats.get_item_history(user_id, 'nickname')
        names = {
            'usernames': [
                {'name': r[0], 'changed_at': r[1].isoformat()} for r in name_history[:10]
            ],
            'nicknames': [
                {'name': r[0], 'changed_at': r[1].isoformat()} for r in nickname_history[:10]
            ],
        }

        # Avatar history count (images fetched separately via /avatars endpoint).
        avatar_records = await self.bot.db.stats.get_avatar_history(user_id, limit=100)
        avatar_count = len(avatar_records)

        return web.json_response({
            **identity,
            'leveling': leveling,
            'cases': cases,
            'case_count': len(cases),
            'warning_count': warning_count,
            'command_stats': command_stats,
            'last_seen': last_seen,
            'names': names,
            'avatar_count': avatar_count,
        })

    async def _member_action(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        user_id = int(request.match_info['user_id'])

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        action = body.get('action')
        reason = body.get('reason') or None
        moderator_id = body.get('moderator_id')
        moderator_id = int(moderator_id) if moderator_id else None

        if action not in ('kick', 'ban', 'unban'):
            raise web.HTTPBadRequest(text='action must be kick, ban, or unban')

        if action == 'unban':
            try:
                user = await self.bot.fetch_user(user_id)
                await guild.unban(user, reason=reason)
            except Exception as e:
                raise web.HTTPBadRequest(text=str(e))
        else:
            member = guild.get_member(user_id)
            if member is None:
                raise web.HTTPNotFound(text='member not found')

            bot_member = guild.me
            if member.top_role >= bot_member.top_role:
                raise web.HTTPBadRequest(text='cannot moderate a member with equal or higher role')

            if action == 'kick':
                await member.kick(reason=reason)
            elif action == 'ban':
                await member.ban(reason=reason)

        self.bot.dispatch('mod_action', guild_id, action, user_id, moderator_id, reason)
        return web.json_response({'ok': True})

    async def _member_roles(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        user_id = int(request.match_info['user_id'])

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        member = guild.get_member(user_id)
        if member is None:
            raise web.HTTPNotFound(text='member not found')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        add_ids = body.get('add', [])
        remove_ids = body.get('remove', [])

        if not add_ids and not remove_ids:
            raise web.HTTPBadRequest(text='must specify add or remove')

        reason = 'Dashboard role update'

        if add_ids:
            roles_to_add = [r for r in guild.roles if str(r.id) in add_ids]
            if roles_to_add:
                await member.add_roles(*roles_to_add, reason=reason)

        if remove_ids:
            roles_to_remove = [r for r in guild.roles if str(r.id) in remove_ids]
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason=reason)

        return web.json_response({'ok': True})

    async def _get_member_avatars(self, request: web.Request) -> web.Response:
        """Returns avatar history as base64-encoded images with timestamps."""
        user_id = int(request.match_info['user_id'])
        limit = min(int(request.query.get('limit', '20')), 50)

        records = await self.bot.db.stats.get_avatar_history(user_id, limit=limit)
        avatars = [
            {
                'image': base64.b64encode(record['avatar']).decode(),
                'changed_at': record['changed_at'].isoformat() if record['changed_at'] else None,
            }
            for record in records
        ]

        return web.json_response({'avatars': avatars, 'total': len(records)})

