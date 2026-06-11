"""InternalAPI music/equalizer endpoints."""
from __future__ import annotations

import wavelink
from aiohttp import web

from .models import InternalAPIHandlers

PRESETS = {
    'flat': [0.0] * 15,
    'bassboost': [0.2, 0.15, 0.1, 0.05, 0.0, -0.05, -0.1, -0.1, -0.1, -0.1, -0.1, -0.1, -0.1, -0.1, -0.1],
    'treble': [-0.1, -0.1, -0.1, -0.05, 0.0, 0.05, 0.1, 0.12, 0.15, 0.18, 0.2, 0.22, 0.24, 0.25, 0.25],
    'vocal': [-0.1, -0.05, 0.0, 0.1, 0.2, 0.25, 0.25, 0.2, 0.15, 0.1, 0.0, -0.05, -0.1, -0.1, -0.1],
}


class MusicHandlers(InternalAPIHandlers):
    """Music/equalizer internal API handlers."""

    def _get_guild_player(self, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return None, None
        player = guild.voice_client
        if player is None or not isinstance(player, wavelink.Player):
            return guild, None
        return guild, player

    async def _get_music(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild, player = self._get_guild_player(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        if player is None:
            return web.json_response({
                'active': False,
                'equalizer': [0.0] * 15,
                'filters': {'nightcore': False, '8d': False, 'lowpass': None},
                'presets': list(PRESETS.keys()),
            })

        eq_payload = player.filters.equalizer.payload
        gains = [eq_payload[i]['gain'] if i in eq_payload else 0.0 for i in range(15)]

        filters_state = {
            'nightcore': player.filters.timescale.payload.get('speed', 1.0) != 1.0,
            '8d': player.filters.rotation.payload.get('rotationHz', 0.0) != 0.0,
            'lowpass': player.filters.low_pass.payload.get('smoothing', None),
        }

        now_playing = None
        if player.current:
            now_playing = {
                'title': player.current.title,
                'author': player.current.author,
                'duration': player.current.length,
                'position': player.position,
            }

        return web.json_response({
            'active': True,
            'equalizer': gains,
            'filters': filters_state,
            'presets': list(PRESETS.keys()),
            'now_playing': now_playing,
            'channel': str(player.channel.id) if player.channel else None,
        })

    async def _post_music_equalizer(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        _, player = self._get_guild_player(guild_id)
        if player is None:
            raise web.HTTPBadRequest(text='no active player in this guild')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        preset = body.get('preset')
        bands = body.get('bands')

        if preset:
            if preset not in PRESETS:
                raise web.HTTPBadRequest(text=f'unknown preset: {preset}')
            gains = PRESETS[preset]
        elif bands is not None:
            if not isinstance(bands, list) or len(bands) != 15:
                raise web.HTTPBadRequest(text='bands must be an array of 15 gain values')
            for g in bands:
                if not isinstance(g, (int, float)) or not -0.25 <= g <= 1.0:
                    raise web.HTTPBadRequest(text='each gain must be between -0.25 and 1.0')
            gains = bands
        else:
            raise web.HTTPBadRequest(text='provide either preset or bands')

        filters: wavelink.Filters = player.filters
        filters.equalizer.set(bands=[{'band': i, 'gain': g} for i, g in enumerate(gains)])
        await player.set_filters(filters)

        return web.json_response({'ok': True, 'equalizer': gains})

    async def _post_music_filters(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        _, player = self._get_guild_player(guild_id)
        if player is None:
            raise web.HTTPBadRequest(text='no active player in this guild')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        filters: wavelink.Filters = player.filters
        action = body.get('action')

        if action == 'nightcore':
            if filters.timescale.payload.get('speed', 1.0) != 1.0:
                filters.timescale.reset()
            else:
                filters.timescale.set(speed=1.25, pitch=1.3, rate=1.3)
        elif action == '8d':
            if filters.rotation.payload.get('rotationHz', 0.0) != 0.0:
                filters.rotation.reset()
            else:
                filters.rotation.set(rotation_hz=0.15)
        elif action == 'lowpass':
            smoothing = body.get('smoothing')
            if smoothing is None:
                filters.low_pass.reset()
            else:
                filters.low_pass.set(smoothing=float(smoothing))
        elif action == 'reset':
            filters.reset()
        else:
            raise web.HTTPBadRequest(text='unknown action')

        await player.set_filters(filters)
        return web.json_response({'ok': True})
