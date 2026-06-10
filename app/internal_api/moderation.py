"""InternalAPI moderation endpoints: audit log, bulk actions, activity."""
from __future__ import annotations

import datetime

from aiohttp import web

from .models import InternalAPIHandlers


class ModerationHandlers(InternalAPIHandlers):
    """Moderation-related internal API handlers (cases browser, bulk actions, activity)."""

    async def _get_cases(self, request: web.Request) -> web.Response:
        """Paginated, filterable list of moderation cases for a guild."""
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        limit = min(int(request.query.get('limit', '50')), 100)
        offset = int(request.query.get('offset', '0'))
        action = request.query.get('action') or None
        moderator_id = request.query.get('moderator_id')
        target_id = request.query.get('target_id')
        after = request.query.get('after')
        before = request.query.get('before')

        kwargs: dict = {
            'action': action,
            'moderator_id': int(moderator_id) if moderator_id else None,
            'target_id': int(target_id) if target_id else None,
            'after': datetime.datetime.fromisoformat(after) if after else None,
            'before': datetime.datetime.fromisoformat(before) if before else None,
            'limit': limit,
            'offset': offset,
        }

        records = await self.bot.db.cases.get_cases(guild_id, **kwargs)
        total = await self.bot.db.cases.count_cases(
            guild_id,
            action=kwargs['action'],
            moderator_id=kwargs['moderator_id'],
            target_id=kwargs['target_id'],
            after=kwargs['after'],
            before=kwargs['before'],
        )

        cases = []
        for record in records:
            mod_id = record['moderator_id']
            moderator_name = None
            if mod_id is not None:
                mod = guild.get_member(mod_id) or self.bot.get_user(mod_id)
                moderator_name = mod.display_name if mod else f'Unknown ({mod_id})'

            tgt_id = record['target_id']
            target = guild.get_member(tgt_id) or self.bot.get_user(tgt_id)
            target_name = target.display_name if target else f'Unknown ({tgt_id})'

            cases.append({
                'case_index': record['case_index'],
                'action': record['action'],
                'target_id': str(tgt_id),
                'target_name': target_name,
                'moderator_id': str(mod_id) if mod_id is not None else None,
                'moderator_name': moderator_name,
                'reason': record['reason'],
                'created_at': record['created_at'].isoformat() if record['created_at'] else None,
            })

        return web.json_response({'cases': cases, 'total': total})

    async def _get_recent_cases(self, request: web.Request) -> web.Response:
        """Cases created since a timestamp (for event polling/streaming)."""
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        since = request.query.get('since')
        if not since:
            raise web.HTTPBadRequest(text='since parameter is required (ISO timestamp)')

        since_dt = datetime.datetime.fromisoformat(since)
        records = await self.bot.db.cases.get_recent_cases(guild_id, since=since_dt)

        cases = []
        for record in records:
            mod_id = record['moderator_id']
            moderator_name = None
            if mod_id is not None:
                mod = guild.get_member(mod_id) or self.bot.get_user(mod_id)
                moderator_name = mod.display_name if mod else f'Unknown ({mod_id})'

            tgt_id = record['target_id']
            target = guild.get_member(tgt_id) or self.bot.get_user(tgt_id)
            target_name = target.display_name if target else f'Unknown ({tgt_id})'

            cases.append({
                'case_index': record['case_index'],
                'action': record['action'],
                'target_id': str(tgt_id),
                'target_name': target_name,
                'moderator_id': str(mod_id) if mod_id is not None else None,
                'moderator_name': moderator_name,
                'reason': record['reason'],
                'created_at': record['created_at'].isoformat() if record['created_at'] else None,
            })

        return web.json_response({'cases': cases})

    async def _bulk_member_action(self, request: web.Request) -> web.Response:
        """Apply a moderation action to multiple members at once."""
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        user_ids = body.get('user_ids', [])
        action = body.get('action')
        reason = body.get('reason') or None
        role_ids = body.get('role_ids', [])

        if not user_ids:
            raise web.HTTPBadRequest(text='user_ids is required')
        if action not in ('kick', 'ban', 'unban', 'add_roles', 'remove_roles'):
            raise web.HTTPBadRequest(text='action must be kick, ban, unban, add_roles, or remove_roles')
        if action in ('add_roles', 'remove_roles') and not role_ids:
            raise web.HTTPBadRequest(text='role_ids is required for role actions')

        bot_member = guild.me
        successes = []
        failures = []

        roles = [r for r in guild.roles if str(r.id) in role_ids] if role_ids else []

        for uid in user_ids:
            uid_int = int(uid)
            try:
                if action == 'unban':
                    user = await self.bot.fetch_user(uid_int)
                    await guild.unban(user, reason=reason)
                else:
                    member = guild.get_member(uid_int)
                    if member is None:
                        failures.append({'user_id': uid, 'error': 'member not found'})
                        continue
                    if action in ('kick', 'ban') and member.top_role >= bot_member.top_role:
                        failures.append({'user_id': uid, 'error': 'higher role hierarchy'})
                        continue

                    if action == 'kick':
                        await member.kick(reason=reason)
                    elif action == 'ban':
                        await member.ban(reason=reason)
                    elif action == 'add_roles':
                        await member.add_roles(*roles, reason=reason or 'Dashboard bulk role update')
                    elif action == 'remove_roles':
                        await member.remove_roles(*roles, reason=reason or 'Dashboard bulk role update')

                successes.append(uid)
            except Exception as e:
                failures.append({'user_id': uid, 'error': str(e)})

        return web.json_response({
            'ok': True,
            'successes': len(successes),
            'failures': failures,
        })

    async def _get_member_activity(self, request: web.Request) -> web.Response:
        """Daily command counts for a member (activity heatmap data)."""
        guild_id = int(request.match_info['guild_id'])
        user_id = int(request.match_info['user_id'])

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        days = min(int(request.query.get('days', '365')), 365)
        records = await self.bot.db.stats.get_member_daily_activity(guild_id, user_id, days=days)

        activity = [
            {'day': record['day'].isoformat(), 'count': record['count']}
            for record in records
        ]
        return web.json_response({'activity': activity, 'days': days})
