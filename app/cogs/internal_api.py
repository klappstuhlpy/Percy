"""Internal HTTP API for the klappstuhl_me BFF dashboard.

Exposes guild configuration over a local aiohttp server authenticated with a
pre-shared token. The Rust dashboard proxies user actions through this API so
that all mutations route through Percy's repository layer and cache invalidation
happens atomically.

Disabled (silently) when ``config.internal_api_token`` is not set.
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING

from aiohttp import web

import config
from app.core import Bot, Cog
from app.database import Gatekeeper

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

__all__ = ('InternalAPI',)


def _check_auth(request: web.Request) -> bool:
    token = request.headers.get('Authorization')
    return token == f'Bearer {config.internal_api_token}'


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if not _check_auth(request):
        raise web.HTTPUnauthorized(text='invalid or missing token')
    return await handler(request)


class InternalAPI(Cog):
    """Manages the internal HTTP API server lifecycle."""

    __hidden__ = True

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def cog_load(self) -> None:
        if not config.internal_api_token:
            log.warning('Internal API disabled (INTERNAL_API_TOKEN not set)')
            return

        self._app = web.Application(middlewares=[auth_middleware])
        assert self._app is not None
        
        self._app['bot'] = self.bot
        # Guild config
        self._app.router.add_get('/api/internal/guilds/{guild_id}', self._get_guild_config)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/config', self._patch_guild_config)
        self._app.router.add_get('/api/internal/guilds/{guild_id}/roles', self._get_guild_roles)
        self._app.router.add_get('/api/internal/guilds/{guild_id}/channels', self._get_guild_channels)
        # Members
        self._app.router.add_get('/api/internal/guilds/{guild_id}/members', self._get_guild_members)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/members/{user_id}/action', self._member_action)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/members/{user_id}/roles', self._member_roles)
        # Gatekeeper
        self._app.router.add_get('/api/internal/guilds/{guild_id}/gatekeeper', self._get_gatekeeper)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/gatekeeper', self._patch_gatekeeper)
        # User
        self._app.router.add_get('/api/internal/users/{discord_id}/guilds', self._get_user_guilds)
        # Leveling
        self._app.router.add_get('/api/internal/guilds/{guild_id}/leveling/config', self._get_leveling_config)
        self._app.router.add_get('/api/internal/guilds/{guild_id}/leveling/leaderboard', self._get_leveling_leaderboard)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/leveling/users/{user_id}', self._patch_leveling_user)
        # Polls
        self._app.router.add_get('/api/internal/guilds/{guild_id}/polls', self._get_polls)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/polls/{poll_id}', self._patch_poll)
        # Giveaways
        self._app.router.add_get('/api/internal/guilds/{guild_id}/giveaways', self._get_giveaways)
        # Tags
        self._app.router.add_get('/api/internal/guilds/{guild_id}/tags', self._get_tags)
        # Commands
        self._app.router.add_get('/api/internal/guilds/{guild_id}/commands', self._get_commands)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/commands/toggle', self._toggle_command)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/plonks', self._manage_plonk)
        # Stats
        self._app.router.add_get('/api/internal/guilds/{guild_id}/stats', self._get_guild_stats)
        self._app.router.add_get('/api/internal/bot/stats', self._get_bot_stats)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, config.internal_api_host, config.internal_api_port)
        await self._site.start()
        log.info('Internal API listening on %s:%d', config.internal_api_host, config.internal_api_port)

    async def cog_unload(self) -> None:
        if self._site is not None:
            await self._site.stop()
        if self._runner is not None:
            await self._runner.cleanup()

    # -- Endpoints ------------------------------------------------------------

    @staticmethod
    def _resolve_channel(guild, channel_id: int | None) -> dict | None:
        if channel_id is None:
            return None
        ch = guild.get_channel(channel_id)
        return {
            'id': str(channel_id),
            'name': ch.name if ch else 'deleted-channel',
            'type': str(ch.type) if ch else 'unknown',
        }

    @staticmethod
    def _resolve_role(guild, role_id: int | None) -> dict | None:
        if role_id is None:
            return None
        role = guild.get_role(role_id)
        return {
            'id': str(role_id),
            'name': role.name if role else 'deleted-role',
            'color': role.color.value if role else 0,
        }

    async def _get_guild_config(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        guild_config = await self.bot.db.get_guild_config(guild_id)

        payload = {
            'id': guild_config.id,
            'name': guild.name,
            'icon_url': guild.icon.url if guild.icon else None,
            'member_count': guild.member_count,
            'flags': {
                'audit_log': guild_config.flags.audit_log,
                'raid': guild_config.flags.raid,
                'alerts': guild_config.flags.alerts,
                'gatekeeper': guild_config.flags.gatekeeper,
            },
            'audit_log_channel': self._resolve_channel(guild, guild_config.audit_log_channel_id),
            'poll_channel': self._resolve_channel(guild, guild_config.poll_channel_id),
            'poll_ping_role': self._resolve_role(guild, guild_config.poll_ping_role_id),
            'poll_reason_channel': self._resolve_channel(guild, guild_config.poll_reason_channel_id),
            'mention_count': guild_config.mention_count,
            'mute_role': self._resolve_role(guild, guild_config.mute_role_id),
            'alert_channel': self._resolve_channel(guild, guild_config.alert_channel_id),
            'mod_log_channel': self._resolve_channel(guild, getattr(guild_config, 'mod_log_channel_id', None)),
            'message_log_channel': self._resolve_channel(guild, getattr(guild_config, 'message_log_channel_id', None)),
            'voice_log_channel': self._resolve_channel(guild, getattr(guild_config, 'voice_log_channel_id', None)),
            'music_panel_channel': self._resolve_channel(guild, guild_config.music_panel_channel_id),
            'use_music_panel': guild_config.use_music_panel,
            'prefixes': list(guild_config.prefixes),
            'is_new_config': guild_config.flags.value == 0 and guild_config.audit_log_channel_id is None,
        }
        return web.json_response(payload)

    async def _patch_guild_config(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        if not isinstance(body, dict) or not body:
            raise web.HTTPBadRequest(text='body must be a non-empty object')

        guild_config = await self.bot.db.get_guild_config(guild_id)

        # Build SET clauses from allowed fields.
        allowed_fields = {
            'audit_log_channel_id', 'poll_channel_id', 'poll_ping_role_id',
            'poll_reason_channel_id', 'mention_count', 'mute_role_id',
            'alert_channel_id', 'music_panel_channel_id', 'use_music_panel',
            'mod_log_channel_id', 'message_log_channel_id', 'voice_log_channel_id',
        }
        updates: dict[str, object] = {}
        for key, value in body.items():
            if key in allowed_fields:
                updates[key] = value
            elif key == 'flags' and isinstance(value, dict):
                # Flags are a bitmask — compute the new value.
                new_flags = guild_config.flags.value
                flag_map = {'audit_log': 1, 'raid': 2, 'alerts': 4, 'gatekeeper': 8}
                for flag_name, bit in flag_map.items():
                    if flag_name in value:
                        if value[flag_name]:
                            new_flags |= bit
                        else:
                            new_flags &= ~bit
                updates['flags'] = new_flags
            elif key == 'prefixes' and isinstance(value, list):
                updates['prefixes'] = value

        if not updates:
            raise web.HTTPBadRequest(text='no valid fields to update')

        # Apply each update through the record's _update mechanism.
        # We use raw SQL here because the BaseRecord._update pattern is
        # designed for single-field changes from within the bot.
        set_clauses = []
        params: list[object] = []
        for i, (col, val) in enumerate(updates.items(), start=1):
            if col == 'prefixes':
                set_clauses.append(f'{col} = ${i}')
                params.append(val)
            else:
                set_clauses.append(f'{col} = ${i}')
                params.append(val)

        params.append(guild_id)
        query = f"UPDATE guild_config SET {', '.join(set_clauses)} WHERE id = ${len(params)}"

        await self.bot.db.execute(query, *params)
        self.bot.db.get_guild_config.invalidate(guild_id)

        return web.json_response({'ok': True})

    async def _get_guild_roles(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        roles = [
            {
                'id': str(role.id),
                'name': role.name,
                'color': role.color.value,
                'position': role.position,
                'permissions': role.permissions.value,
                'mentionable': role.mentionable,
                'managed': role.managed,
                'hoist': role.hoist,
                'icon_url': role.icon.url if role.icon else None,
            }
            for role in sorted(guild.roles, key=lambda r: r.position, reverse=True)
        ]
        return web.json_response(roles)

    async def _get_guild_channels(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        channels = [
            {
                'id': str(ch.id),
                'name': ch.name,
                'type': str(ch.type),
                'position': ch.position,
                'category_id': str(ch.category_id) if ch.category_id else None,
            }
            for ch in sorted(guild.channels, key=lambda c: (c.position, c.name))
        ]
        return web.json_response(channels)

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

    async def _get_gatekeeper(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        gatekeeper = await self.bot.db.get_guild_gatekeeper(guild_id)

        if gatekeeper is None:
            return web.json_response(None)

        payload = {
            'channel': self._resolve_channel(guild, gatekeeper.channel_id),
            'role': self._resolve_role(guild, gatekeeper.role_id),
            'message': gatekeeper.message_id,
            'starter_role': self._resolve_role(guild, gatekeeper.starter_role_id),
            'bypass_action': gatekeeper.bypass_action,
            'rate': gatekeeper.rate if isinstance(gatekeeper.rate, str) else (f"{gatekeeper.rate[0]}/{gatekeeper.rate[1]}" if gatekeeper.rate else None),
            'started_at': gatekeeper.started_at.isoformat() if gatekeeper.started_at else None,
            'member_count': len(gatekeeper.members),
            'needs_setup': gatekeeper.requires_setup
        }
        return web.json_response(payload)

    async def _patch_gatekeeper(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        if not isinstance(body, dict) or not body:
            raise web.HTTPBadRequest(text='body must be a non-empty object')

        allowed = {'channel_id', 'role_id', 'starter_role_id', 'bypass_action', 'rate'}
        updates: dict[str, object] = {}
        for key, value in body.items():
            if key not in allowed:
                continue
            if key == 'bypass_action' and value not in ('ban', 'kick'):
                raise web.HTTPBadRequest(text='bypass_action must be ban or kick')
            updates[key] = value

        if not updates:
            raise web.HTTPBadRequest(text='no valid fields to update')

        set_clauses = []
        params: list[object] = [guild_id]
        for i, (col, val) in enumerate(updates.items(), start=2):
            set_clauses.append(f'{col} = ${i}')
            params.append(val)

        await self.bot.db.execute(
            "INSERT INTO guild_gatekeeper (id) VALUES ($1) ON CONFLICT (id) DO NOTHING",
            guild_id,
        )

        query = f"UPDATE guild_gatekeeper SET {', '.join(set_clauses)} WHERE id = $1"
        await self.bot.db.execute(query, *params)
        self.bot.db.get_guild_gatekeeper.invalidate(guild_id)

        return web.json_response({'ok': True})

    async def _get_user_guilds(self, request: web.Request) -> web.Response:
        discord_id = int(request.match_info['discord_id'])

        manageable = []
        for guild in self.bot.guilds:
            member = guild.get_member(discord_id)
            if member is None:
                continue
            perms = member.guild_permissions
            if perms.administrator or perms.manage_guild:
                manageable.append({
                    'id': str(guild.id),
                    'name': guild.name,
                    'icon_url': guild.icon.url if guild.icon else None,
                    'member_count': guild.member_count,
                    'owner': guild.owner_id == discord_id,
                })

        return web.json_response(manageable)

    # -- Leveling endpoints -----------------------------------------------------

    async def _get_leveling_config(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        record = await self.bot.db.leveling.get_guild_config_record(guild_id)
        if record is None:
            return web.json_response({'enabled': False, 'configured': False})

        level_up_ch = record.get('level_up_channel_id')
        return web.json_response({
            'enabled': record.get('enabled', False),
            'configured': True,
            'level_up_channel_id': str(level_up_ch) if level_up_ch else None,
            'level_up_message': record.get('level_up_message'),
            'stack_roles': record.get('stack_roles', False),
            'voice_enabled': record.get('voice_enabled', False),
            'xp_rate': record.get('xp_rate', 1.0),
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
        await self.bot.db.leveling.update_user_level(
            user_id, guild_id,
            key=lambda: None,
            values=updates,
        )
        return web.json_response({'ok': True})

    # -- Polls endpoints --------------------------------------------------------

    async def _get_polls(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        records = await self.bot.db.polls.get_for_guild(guild_id)

        polls = []
        for record in records:
            metadata = record.get('metadata') or {}
            _kwargs = metadata.get('kwargs', {})
            polls.append({
                'id': record['id'],
                'channel_id': str(record['channel_id']),
                'message_id': str(record['message_id']),
                'question': _kwargs.get('content', _kwargs.get('question', 'Untitled Poll')),
                'description': _kwargs.get('description') or '',
                'options': [opt['content'] for opt in _kwargs.get('options', [])],
                'image_url': _kwargs.get('image_url') or '',
                'color': _kwargs.get('color') or '',
                'published': record['published'].isoformat() if record.get('published') else None,
                'expires': record['expires'].isoformat() if record.get('expires') else None,
                'ended': _kwargs.get('running', False) is False,
                'total_votes': _kwargs.get('votes', 0),
            })

        return web.json_response({'polls': polls})

    async def _patch_poll(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        poll_id = int(request.match_info['poll_id'])

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        record = await self.bot.db.polls.get(poll_id, guild_id)
        if record is None:
            raise web.HTTPNotFound(text='poll not found')

        metadata = record.get('metadata') or {}
        kwargs = metadata.get('kwargs', {})

        if not kwargs.get('running', False):
            raise web.HTTPBadRequest(text='cannot edit a poll that has ended')

        # Apply edits to kwargs
        if 'question' in body:
            val = body['question']
            if val:
                kwargs['content'] = val

        if 'description' in body:
            val = body['description']
            kwargs['description'] = val if val else None

        if 'image_url' in body:
            val = body['image_url']
            kwargs['image_url'] = val if val else None

        if 'color' in body:
            val = body['color']
            kwargs['color'] = val if val else None

        if 'options' in body:
            new_options = body['options']
            if isinstance(new_options, list) and len(new_options) >= 2:
                existing = kwargs.get('options', [])
                updated = []
                for i, opt_text in enumerate(new_options):
                    if not opt_text:
                        continue
                    if i < len(existing):
                        existing[i]['content'] = opt_text
                        updated.append(existing[i])
                    else:
                        updated.append({'content': opt_text, 'index': i, 'votes': 0})
                if len(updated) >= 2:
                    for idx, opt in enumerate(updated):
                        opt['index'] = idx
                    kwargs['options'] = updated

        metadata['kwargs'] = kwargs
        await self.bot.db.polls.update(
            poll_id,
            key=lambda x: f'{x[1]} = ${x[0]}',
            values={'metadata': metadata},
        )

        return web.json_response({'ok': True})

    # -- Giveaways endpoints ----------------------------------------------------

    async def _get_giveaways(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        records = await self.bot.db.giveaways.get_guild_giveaways(guild_id)

        giveaways = []
        for record in records:
            metadata = record.get('metadata') or {}
            _kwargs = metadata.get('kwargs', {})
            giveaways.append({
                'id': record['id'],
                'channel_id': str(record['channel_id']),
                'message_id': str(record['message_id']),
                'author_id': str(record['author_id']),
                'title': _kwargs.get('prize', 'Giveaway'),
                'description': _kwargs.get('description', 'N/A'),
                'winners_count': _kwargs.get('winner_count', 1),
                'entries': len(record.get('entries', [])),
                'ended': datetime.datetime.fromisoformat(_kwargs.get('expires')).astimezone(datetime.UTC) < datetime.datetime.now(datetime.UTC),
                'ends_at': _kwargs.get('expires'),
            })

        return web.json_response({'giveaways': giveaways})

    # -- Tags endpoints ---------------------------------------------------------

    async def _get_tags(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        total = await self.bot.db.tags.count_tags(guild_id)
        most_used = await self.bot.db.tags.get_most_used_tags(guild_id, limit=25)
        top_creators = await self.bot.db.tags.get_top_tag_creators(guild_id, limit=10)
        total_uses = await self.bot.db.tags.count_tag_command_uses(guild_id)

        tags = []
        for record in most_used:
            owner_id = record.get('owner_id')
            member = guild.get_member(owner_id) if owner_id else None
            if not member:
                member = await self.bot.fetch_user(owner_id) if owner_id else None

            tags.append({
                'id': record['id'],
                'name': record['name'],
                'owner_id': str(owner_id) if owner_id else None,
                'owner_name': member.display_name if member else None,
                'uses': record.get('uses', 0),
                'created_at': record['created_at'].isoformat() if record.get('created_at') else None,
            })

        creators = []
        for record in top_creators:
            user_id = record.get('owner_id')
            member = guild.get_member(user_id) if user_id else None

            creators.append({
                'user_id': str(user_id) if user_id else None,
                'username': member.display_name if member else f'Unknown ({user_id})',
                'count': record.get('count', 0),
            })

        return web.json_response({
            'total': total,
            'total_uses': total_uses,
            'tags': tags,
            'top_creators': creators,
        })

    # -- Command management endpoints -------------------------------------------

    async def _get_commands(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        command_config = await self.bot.db.guilds.get_command_config(guild_id)
        plonks = await self.bot.db.guilds.get_plonks(guild_id)

        disabled_commands: dict[str, list[str]] = {}
        for record in command_config:
            name = record['name']
            channel_id = record.get('channel_id')
            whitelist = record.get('whitelist', False)
            if not whitelist and channel_id:
                if name not in disabled_commands:
                    disabled_commands[name] = []
                disabled_commands[name].append(str(channel_id))

        text_channel_count = len(guild.text_channels)

        all_commands = []
        for cmd in self.bot.walk_commands():
            qualified = cmd.qualified_name
            cog_name = cmd.cog.qualified_name if cmd.cog else 'Uncategorized'
            disabled_in = disabled_commands.get(qualified, [])
            all_commands.append({
                'name': qualified,
                'category': cog_name,
                'description': cmd.short_doc or '',
                'disabled_in': disabled_in,
                'globally_disabled': len(disabled_in) >= text_channel_count and text_channel_count > 0,
            })

        plonk_list = []
        for record in plonks:
            entity_id = record['entity_id']
            member = guild.get_member(entity_id)
            channel = guild.get_channel(entity_id)
            plonk_list.append({
                'entity_id': str(entity_id),
                'type': 'member' if member else ('channel' if channel else 'unknown'),
                'name': member.display_name if member else (channel.name if channel else str(entity_id)),
            })

        return web.json_response({
            'commands': sorted(all_commands, key=lambda c: (c['category'], c['name'])),
            'plonks': plonk_list,
        })

    async def _toggle_command(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        name = body.get('name')
        enabled = body.get('enabled')
        channel_id = body.get('channel_id')

        if name is None or enabled is None:
            raise web.HTTPBadRequest(text='must specify name and enabled')

        if enabled:
            # Re-enable: remove all disable entries for this command in this guild
            if channel_id:
                await self.bot.db.execute(
                    "DELETE FROM command_config WHERE guild_id=$1 AND name=$2 AND channel_id=$3;",
                    guild_id, name, int(channel_id),
                )
            else:
                await self.bot.db.execute(
                    "DELETE FROM command_config WHERE guild_id=$1 AND name=$2;",
                    guild_id, name,
                )
        else:
            # Disable: need a channel_id for per-channel, or disable in all text channels
            if channel_id:
                await self.bot.db.guilds.set_command_config(guild_id, int(channel_id), name, whitelist=False)
            else:
                # Global disable: insert a row for every text channel
                for ch in guild.text_channels:
                    try:
                        await self.bot.db.guilds.set_command_config(guild_id, ch.id, name, whitelist=False)
                    except Exception:
                        pass

        return web.json_response({'ok': True})

    async def _manage_plonk(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        action = body.get('action')
        entity_id = body.get('entity_id')

        if action not in ('add', 'remove'):
            raise web.HTTPBadRequest(text='action must be add or remove')

        if not entity_id:
            raise web.HTTPBadRequest(text='must specify entity_id')

        entity_id = int(entity_id)

        if action == 'add':
            await self.bot.db.guilds.add_plonk(guild_id, entity_id)
        else:
            await self.bot.db.guilds.remove_plonks(guild_id, [entity_id])

        return web.json_response({'ok': True})

    # -- Stats endpoints --------------------------------------------------------

    async def _get_guild_stats(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        command_summary = await self.bot.db.stats.get_command_summary(guild_id)
        command_usage = await self.bot.db.stats.get_command_usage(guild_id=guild_id, group_by='command', limit=10)

        online = sum(1 for m in guild.members if m.status.name != 'offline')
        bots = sum(1 for m in guild.members if m.bot)
        humans = guild.member_count - bots if guild.member_count else 0

        top_commands = [
            {'command': r['command'], 'uses': r['uses']}
            for r in command_usage
        ]

        return web.json_response({
            'member_count': guild.member_count,
            'online_count': online,
            'bot_count': bots,
            'human_count': humans,
            'channel_count': len(guild.channels),
            'role_count': len(guild.roles),
            'emoji_count': len(guild.emojis),
            'boost_count': guild.premium_subscription_count or 0,
            'boost_tier': guild.premium_tier,
            'total_commands': command_summary[0] if command_summary else 0,
            'top_commands': top_commands,
            'created_at': guild.created_at.isoformat(),
            'owner_id': str(guild.owner_id),
            'owner_name': guild.owner.display_name if guild.owner else None,
        })

    # -- Bot stats endpoint -----------------------------------------------------

    async def _get_bot_stats(self, request: web.Request) -> web.Response:
        total_commands = await self.bot.db.stats.count_all_commands()

        return web.json_response({
            'guild_count': len(self.bot.guilds),
            'user_count': sum(g.member_count or 0 for g in self.bot.guilds),
            'channel_count': sum(len(g.channels) for g in self.bot.guilds),
            'total_commands_used': total_commands,
            'cog_count': len(self.bot.cogs),
            'command_count': sum(1 for _ in self.bot.walk_commands()),
            'latency_ms': round(self.bot.latency * 1000, 1),
            'uptime_seconds': (self.bot.uptime.total_seconds() if hasattr(self.bot, 'uptime') else 0),
        })


async def setup(bot: Bot) -> None:
    await bot.add_cog(InternalAPI(bot))
