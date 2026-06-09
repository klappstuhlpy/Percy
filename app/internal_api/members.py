"""InternalAPI members endpoints."""
from __future__ import annotations

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

