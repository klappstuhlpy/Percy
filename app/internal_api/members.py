"""InternalAPI members endpoints."""
from __future__ import annotations

import base64
import json

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

        search = (request.query.get('search') or '').lower()

        all_members = sorted(
            (m for m in guild.members if m.id > after),
            key=lambda m: m.id,
        )

        if search:
            all_members = [
                m for m in all_members
                if search in m.name.lower() or search in m.display_name.lower()
            ]

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

        return web.json_response({'members': members, 'total': len(all_members)})

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

        # Tags this user owns in the guild (most-used first), for the profile.
        owned_tag_records = await self.bot.db.tags.get_owned_tags(guild_id, user_id)
        owned_tags = [
            {'id': r['id'], 'name': r['name'], 'uses': r.get('uses', 0)}
            for r in sorted(owned_tag_records, key=lambda r: r.get('uses', 0), reverse=True)[:50]
        ]

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
            'owned_tags': owned_tags,
        })

    async def _get_member_self(self, request: web.Request) -> web.Response:
        """Personal profile: leveling, economy, command stats — no moderation cases.

        Designed for non-admin members viewing their own profile. Same data as
        member_detail minus the sensitive moderation history, plus economy balance.
        """
        guild_id = int(request.match_info['guild_id'])
        user_id = int(request.match_info['user_id'])

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        member = guild.get_member(user_id)
        if member is None:
            raise web.HTTPNotFound(text='member not found')

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
            'top_role': member.top_role.name if member.top_role != guild.default_role else None,
            'top_role_color': member.top_role.color.value if member.top_role != guild.default_role else 0,
            'join_position': join_position,
            'member_count': guild.member_count or len(guild.members),
            'boosting_since': member.premium_since.isoformat() if member.premium_since else None,
        }

        # Leveling
        leveling = None
        level_record = await self.bot.db.leveling.get_user_level(user_id, guild_id)
        if level_record is not None:
            rank = await self.bot.db.leveling.get_rank(user_id, guild_id)
            leveling = {
                'level': level_record['level'],
                'xp': level_record['xp'],
                'total_xp': level_record.get('total_xp', level_record['xp']),
                'messages': level_record['messages'],
                'rank': rank,
            }

        # Economy balance
        balance = await self.bot.db.get_user_balance(user_id, guild_id)
        economy = {
            'cash': balance.cash if balance else 0,
            'bank': balance.bank if balance else 0,
            'total': (balance.cash + balance.bank) if balance else 0,
        }

        # Command usage stats
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

        # Tags this member owns in the guild (most-used first).
        owned_tag_records = await self.bot.db.tags.get_owned_tags(guild_id, user_id)
        owned_tags = [
            {'id': r['id'], 'name': r['name'], 'uses': r.get('uses', 0)}
            for r in sorted(owned_tag_records, key=lambda r: r.get('uses', 0), reverse=True)[:50]
        ]

        return web.json_response({
            **identity,
            'leveling': leveling,
            'economy': economy,
            'command_stats': command_stats,
            'owned_tags': owned_tags,
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

    async def _get_user_settings(self, request: web.Request) -> web.Response:
        """GET /api/v1/users/{discord_id}/settings — user's personal bot settings."""
        user_id = int(request.match_info['discord_id'])
        config = await self.bot.db.get_user_config(user_id)

        return web.json_response({
            'timezone': config.timezone,
            'track_presence': config.track_presence,
            'track_history': config.track_history,
        })

    async def _patch_user_settings(self, request: web.Request) -> web.Response:
        """PATCH /api/v1/users/{discord_id}/settings — update personal bot settings."""
        user_id = int(request.match_info['discord_id'])

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        config = await self.bot.db.get_user_config(user_id)

        if 'timezone' in body:
            tz = body['timezone']
            if tz is None or tz == '':
                await self.bot.db.users.clear_timezone(user_id)
            else:
                import zoneinfo
                if tz not in zoneinfo.available_timezones():
                    raise web.HTTPBadRequest(text=f'invalid timezone: {tz}')
                await self.bot.db.users.set_timezone(user_id, tz)

        if 'track_presence' in body:
            await config.update(track_presence=bool(body['track_presence']))

        if 'track_history' in body:
            await config.update(track_history=bool(body['track_history']))

        return web.json_response({'ok': True})

    async def _get_user_history(self, request: web.Request) -> web.Response:
        """GET /api/v1/users/{discord_id}/history — consent-tracked history.

        Returns the user's username/nickname change log, avatar snapshots
        (base64-encoded, newest-capped) and presence transitions over the last
        30 days — the data Percy keeps when ``track_history``/``track_presence``
        are on. Feeds the member's personal-dashboard history view.
        """
        user_id = int(request.match_info['discord_id'])
        avatar_limit = min(int(request.query.get('avatar_limit', '24')), 50)

        usernames = await self.bot.db.stats.get_item_history(user_id, 'name')
        nicknames = await self.bot.db.stats.get_item_history(user_id, 'nickname')
        avatars = await self.bot.db.stats.get_avatar_history(user_id, limit=avatar_limit)
        presence = await self.bot.db.stats.get_presence_history(user_id, days=30)

        def _ts(record: object) -> str | None:
            value = record['changed_at']  # type: ignore[index]
            return value.isoformat() if value else None

        return web.json_response({
            'usernames': [{'name': r['item_value'], 'changed_at': _ts(r)} for r in usernames],
            'nicknames': [{'name': r['item_value'], 'changed_at': _ts(r)} for r in nicknames],
            'avatars': [
                {'image': base64.b64encode(r['avatar']).decode(), 'changed_at': _ts(r)}
                for r in avatars
            ],
            'presence': [
                {'status': r['status'], 'status_before': r['status_before'], 'changed_at': _ts(r)}
                for r in presence
            ],
        })

    async def _export_user_data(self, request: web.Request) -> web.Response:
        """GET /api/v1/users/{discord_id}/data-export — full GDPR-style export.

        Mirrors the ``settings request-data`` command: every user-keyed table
        Percy stores, aggregated by :meth:`UsersRepository.export_all_user_data`.
        ``default=str`` serialises the datetimes/Decimals the payload contains.
        """
        user_id = int(request.match_info['discord_id'])
        data = await self.bot.db.users.export_all_user_data(user_id)
        return web.json_response(data, dumps=lambda obj: json.dumps(obj, default=str, ensure_ascii=False))

    async def _delete_user_data(self, request: web.Request) -> web.Response:
        """DELETE /api/v1/users/{discord_id}/personal-data — erase tracked history.

        Permanently removes the user's stored presence, avatar and name/nickname
        history (the same data the ``settings remove-personal-data`` command wipes).
        """
        user_id = int(request.match_info['discord_id'])
        await self.bot.db.users.delete_personal_data(user_id)
        return web.json_response({'ok': True})

