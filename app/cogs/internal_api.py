"""Internal HTTP API for the klappstuhl_me BFF dashboard.

Exposes guild configuration over a local aiohttp server authenticated with a
pre-shared token. The Rust dashboard proxies user actions through this API so
that all mutations route through Percy's repository layer and cache invalidation
happens atomically.

Disabled (silently) when ``config.internal_api_token`` is not set.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiohttp import web

import config
from app.core import Bot, Cog

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
            log.info('Internal API disabled (INTERNAL_API_TOKEN not set)')
            return

        self._app = web.Application(middlewares=[auth_middleware])
        self._app['bot'] = self.bot
        self._app.router.add_get('/api/internal/guilds/{guild_id}', self._get_guild_config)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/config', self._patch_guild_config)
        self._app.router.add_get('/api/internal/guilds/{guild_id}/roles', self._get_guild_roles)
        self._app.router.add_get('/api/internal/guilds/{guild_id}/channels', self._get_guild_channels)
        self._app.router.add_get('/api/internal/guilds/{guild_id}/members', self._get_guild_members)
        self._app.router.add_get('/api/internal/users/{discord_id}/guilds', self._get_user_guilds)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, '127.0.0.1', config.internal_api_port)
        await self._site.start()
        log.info('Internal API listening on 127.0.0.1:%d', config.internal_api_port)

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
            'music_panel_channel': self._resolve_channel(guild, guild_config.music_panel_channel_id),
            'use_music_panel': guild_config.use_music_panel,
            'prefixes': list(guild_config.prefixes),
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


async def setup(bot: Bot) -> None:
    await bot.add_cog(InternalAPI(bot))
