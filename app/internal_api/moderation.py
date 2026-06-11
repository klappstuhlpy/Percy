"""InternalAPI moderation endpoints: audit log, case management, bulk actions, activity."""
from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, cast

from aiohttp import web

from .models import InternalAPIHandlers

# The modlog cog package imports ``app.core``, which itself imports this package
# (``app.core.bot`` -> ``app.internal_api``), so modlog imports must stay lazy
# (inside the handlers) to avoid a circular import at module load.
if TYPE_CHECKING:
    import discord

    from app.cogs.modlog.cog import ModLog
    from app.cogs.modlog.models import ModerationCase


class ModerationHandlers(InternalAPIHandlers):
    """Moderation-related internal API handlers (cases browser + CRUD, bulk actions, activity)."""

    def _case_payload(self, guild: discord.Guild, case: ModerationCase) -> dict:
        """Serializes a case with moderator/target names resolved against the guild."""
        moderator_name = None
        if case.moderator_id is not None:
            mod = guild.get_member(case.moderator_id) or self.bot.get_user(case.moderator_id)
            moderator_name = mod.display_name if mod else f'Unknown ({case.moderator_id})'

        target = guild.get_member(case.target_id) or self.bot.get_user(case.target_id)
        target_name = target.display_name if target else f'Unknown ({case.target_id})'

        return {
            'case_index': case.index,
            'action': case.action,
            'target_id': str(case.target_id),
            'target_name': target_name,
            'moderator_id': str(case.moderator_id) if case.moderator_id is not None else None,
            'moderator_name': moderator_name,
            'reason': case.reason,
            'created_at': case.created_at.isoformat() if case.created_at else None,
        }

    def _modlog_cog(self) -> ModLog | None:
        """The ModLog cog, used so API mutations keep the modlog channel posts in sync."""
        return cast('ModLog | None', self.bot.get_cog('ModLog'))

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

        from app.cogs.modlog.models import ModerationCase

        cases = [self._case_payload(guild, ModerationCase.from_record(record)) for record in records]
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

        from app.cogs.modlog.models import ModerationCase

        cases = [self._case_payload(guild, ModerationCase.from_record(record)) for record in records]
        return web.json_response({'cases': cases})

    async def _create_case(self, request: web.Request) -> web.Response:
        """Manually open a moderation case (e.g. from a member's dashboard page).

        Only records the case (and announces it in the modlog channel); it does not
        perform the action itself — member actions handle that.
        """
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        from app.cogs.modlog.models import CaseType, ModerationCase

        action = body.get('action')
        if not action or CaseType.from_action(action) is None:
            valid = ', '.join(case_type.value for case_type in CaseType)
            raise web.HTTPBadRequest(text=f'action must be one of: {valid}')

        try:
            target_id = int(body.get('target_id') or 0)
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text='target_id must be a user id')
        if not target_id:
            raise web.HTTPBadRequest(text='target_id is required')

        moderator_id = body.get('moderator_id')
        moderator_id = int(moderator_id) if moderator_id else None
        reason = body.get('reason') or None

        cog = self._modlog_cog()
        if cog is not None:
            case = await cog.record_case(guild_id, action, target_id, moderator_id, reason)
        else:
            record = await self.bot.db.cases.create_case(guild_id, action, target_id, moderator_id, reason)
            case = ModerationCase.from_record(record)

        return web.json_response({'ok': True, 'case': self._case_payload(guild, case)})

    async def _patch_case(self, request: web.Request) -> web.Response:
        """Update a case's reason (and its modlog channel post)."""
        guild_id = int(request.match_info['guild_id'])
        case_index = int(request.match_info['case_index'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        reason = body.get('reason')
        if not isinstance(reason, str) or not reason.strip():
            raise web.HTTPBadRequest(text='reason is required')
        reason = reason.strip()

        from app.cogs.modlog.models import ModerationCase

        cog = self._modlog_cog()
        if cog is not None:
            case = await cog.update_case_reason(guild_id, case_index, reason)
        else:
            record = await self.bot.db.cases.update_reason(guild_id, case_index, reason)
            case = ModerationCase.from_record(record) if record is not None else None

        if case is None:
            raise web.HTTPNotFound(text='case not found')
        return web.json_response({'ok': True, 'case': self._case_payload(guild, case)})

    async def _delete_case(self, request: web.Request) -> web.Response:
        """Close (delete) a case and remove its modlog channel post."""
        guild_id = int(request.match_info['guild_id'])
        case_index = int(request.match_info['case_index'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        from app.cogs.modlog.models import ModerationCase

        cog = self._modlog_cog()
        if cog is not None:
            case = await cog.delete_case(guild_id, case_index)
        else:
            record = await self.bot.db.cases.delete_case(guild_id, case_index)
            case = ModerationCase.from_record(record) if record is not None else None

        if case is None:
            raise web.HTTPNotFound(text='case not found')
        return web.json_response({'ok': True, 'case_index': case.index})

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
        moderator_id = body.get('moderator_id')
        moderator_id = int(moderator_id) if moderator_id else None

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

                if action in ('kick', 'ban', 'unban'):
                    self.bot.dispatch('mod_action', guild_id, action, uid_int, moderator_id, reason)
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
