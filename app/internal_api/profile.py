"""InternalAPI custom bot profile endpoints — live from Discord, no DB."""
from __future__ import annotations

import base64

from aiohttp import web

from .models import InternalAPIHandlers


class ProfileHandlers(InternalAPIHandlers):
    """Per-guild bot profile: read live state, edit via Discord API."""

    async def _get_custom_bot(self, request: web.Request) -> web.Response:
        """GET /api/internal/guilds/{guild_id}/custom-bot"""
        await self.bot.wait_until_ready()

        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        me = guild.me
        bot_user = await self.bot.fetch_user(self.bot.user.id)
        return web.json_response({
            'name': me.nick or (me.display_name if me else None),
            'avatar_url': str(me.display_avatar.url) if me else bot_user.display_avatar.url,
            'banner_url': str(me.banner.url) if me and me.banner else bot_user.banner.url if bot_user.banner else None,
            'accent_color': str(me.accent_color) if me else None,
            'about_me': None,  # not supported by Discord API
        })

    async def _patch_custom_bot(self, request: web.Request) -> web.Response:
        """PATCH /api/internal/guilds/{guild_id}/custom-bot — apply via Discord API."""
        await self.bot.wait_until_ready()

        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        body = await request.json()
        me = guild.me

        # Guild nickname
        if 'name' in body:
            nick = body['name']
            if nick is not None:
                nick = str(nick).strip()[:32] or None
            await me.edit(nick=nick)

        # Global avatar (base64-encoded image bytes)
        if 'avatar' in body and body['avatar']:
            avatar_bytes = base64.b64decode(body['avatar'])
            await me.edit(avatar=avatar_bytes)

        # Global banner (base64-encoded image bytes)
        if 'banner' in body and body['banner']:
            banner_bytes = base64.b64decode(body['banner'])
            await me.edit(banner=banner_bytes)

        # About me / bio
        if 'about_me' in body:
            bio = body['about_me']
            if bio is not None:
                bio = str(bio).strip()[:190] or None
            await me.edit(bio=bio)

        return web.json_response({'ok': True})

    async def _reset_custom_bot(self, request: web.Request) -> web.Response:
        """POST /api/internal/guilds/{guild_id}/custom-bot/reset — clear nickname."""
        await self.bot.wait_until_ready()

        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        me = guild.me
        await me.edit(nick=None, avatar=None, banner=None, bio=None)
        return web.json_response({'ok': True})
