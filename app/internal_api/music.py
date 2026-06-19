"""InternalAPI music/equalizer endpoints."""
from __future__ import annotations

import discord
from discord.ui.view import LayoutView
import wavelink
from aiohttp import web

from .models import InternalAPIHandlers

PRESETS = {
    'flat': [0.0] * 15,
    'bassboost': [0.2, 0.15, 0.1, 0.05, 0.0, -0.05, -0.1, -0.1, -0.1, -0.1, -0.1, -0.1, -0.1, -0.1, -0.1],
    'treble': [-0.1, -0.1, -0.1, -0.05, 0.0, 0.05, 0.1, 0.12, 0.15, 0.18, 0.2, 0.22, 0.24, 0.25, 0.25],
    'vocal': [-0.1, -0.05, 0.0, 0.1, 0.2, 0.25, 0.25, 0.2, 0.15, 0.1, 0.0, -0.05, -0.1, -0.1, -0.1],
}

DEFAULT_CHANNEL_DESCRIPTION = """
This is the Channel where you can see {bot}'s current playing songs.
You can interact with the **control panel** and manage the current songs.
"""


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

        config = await self.bot.db.get_guild_config(guild_id)
        setup = None
        if config.music_panel_channel_id:
            setup = {
                'channel_id': str(config.music_panel_channel_id),
                'message_id': str(config.music_panel_message_id) if config.music_panel_message_id else None,
                'use_panel': config.use_music_panel,
                'dj_mode': getattr(config, 'music_dj_mode', 0),
            }

        # 24/7 ("always-on") state: prefer the live player, fall back to the persisted row.
        always_on = {'enabled': False, 'mode': None, 'source': None}
        if player is not None and getattr(player, 'always_on', False):
            always_on = {
                'enabled': True,
                'mode': player.always_on_mode,
                'source': player.always_on_source,
            }
        else:
            session = await self.bot.db.music_sessions.get_session(guild_id)
            if session and session['always_on']:
                always_on = {
                    'enabled': True,
                    'mode': session['always_on_mode'],
                    'source': session['always_on_source'],
                }

        if player is None:
            return web.json_response({
                'active': False,
                'equalizer': [0.0] * 15,
                'filters': {'nightcore': False, '8d': False, 'lowpass': None},
                'presets': list(PRESETS.keys()),
                'setup': setup,
                'always_on': always_on,
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
            'setup': setup,
            'always_on': always_on,
        })

    async def _post_music_247(self, request: web.Request) -> web.Response:
        """Enable or disable the 24/7 always-on player for a guild."""
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        cog = self.bot.get_cog('Music')
        if cog is None:
            raise web.HTTPBadRequest(text='music feature is unavailable')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        if not body.get('enabled', True):
            await cog.disable_always_on(guild)  # type: ignore[attr-defined]
            return web.json_response({'ok': True, 'always_on': {'enabled': False, 'mode': None, 'source': None}})

        mode = body.get('mode')
        source = body.get('source')
        if mode not in ('radio', 'playlist', 'autoplay') or not source:
            raise web.HTTPBadRequest(text="'mode' (radio|playlist|autoplay) and 'source' are required")

        # Radio mode accepts a friendly preset name (e.g. "lofi") as a shortcut.
        if mode == 'radio':
            from app.cogs.music.cog import RADIO_PRESETS
            preset = RADIO_PRESETS.get(source.strip().lower())
            if preset:
                source = preset[1]

        channel: discord.VoiceChannel | discord.StageChannel | None = None
        vc_id = body.get('voice_channel_id')
        if vc_id:
            resolved = guild.get_channel(int(vc_id))
            if isinstance(resolved, discord.VoiceChannel | discord.StageChannel):
                channel = resolved
        elif isinstance(player := guild.voice_client, wavelink.Player):
            channel = player.channel  # type: ignore[assignment]

        if channel is None:
            raise web.HTTPBadRequest(text='a valid voice_channel_id is required to start 24/7')

        from app.cogs.music.models import SearchReturn
        from app.cogs.music.player import Player

        probe = await Player.search(source, return_first=True)
        if isinstance(probe, SearchReturn) or not probe:
            raise web.HTTPBadRequest(text='could not resolve that source')

        await cog.enable_always_on(guild, channel, None, mode, source)  # type: ignore[attr-defined]
        return web.json_response(
            {'ok': True, 'always_on': {'enabled': True, 'mode': mode, 'source': source}}
        )

    async def _post_music_setup(self, request: web.Request) -> web.Response:
        """Set up the music panel: create or use a channel, send the panel message."""
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        config = await self.bot.db.get_guild_config(guild_id)
        if config.music_panel_channel_id:
            raise web.HTTPBadRequest(text='music configuration already exists')

        try:
            body = await request.json()
        except Exception:
            body = {}

        channel_id = body.get('channel_id') if body else None
        channel: discord.TextChannel | None = None

        if channel_id:
            channel = guild.get_channel(int(channel_id))  # type: ignore[assignment]
            if channel is None or not isinstance(channel, discord.TextChannel):
                raise web.HTTPBadRequest(text='invalid channel')
        else:
            category = guild.text_channels[0].category if guild.text_channels else None
            parent = category or guild
            channel = await parent.create_text_channel(name="\U0001f3b6percy-music")

        assert self.bot.user is not None
        await channel.edit(
            slowmode_delay=3,
            topic=DEFAULT_CHANNEL_DESCRIPTION.format(bot=self.bot.user.mention),
        )

        from app.cogs.music.player import Player

        view = LayoutView()
        view.add_item(Player.preview_container(guild))
        message = await channel.send(view=view)

        await message.pin()
        await channel.purge(limit=5, check=lambda msg: not msg.pinned)

        await config.update(
            music_panel_channel_id=channel.id,
            music_panel_message_id=message.id,
            use_music_panel=True,
        )

        return web.json_response({
            'ok': True,
            'channel_id': str(channel.id),
            'channel_name': channel.name,
        })

    async def _post_music_reset(self, request: web.Request) -> web.Response:
        """Reset the music configuration: delete the channel and clear config."""
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        config = await self.bot.db.get_guild_config(guild_id)
        if not config.music_panel_channel_id:
            raise web.HTTPBadRequest(text='no music configuration to reset')

        channel = config.music_panel_channel
        await config.update(music_panel_channel_id=None, music_panel_message_id=None, use_music_panel=False)

        if channel:
            try:
                await channel.delete(reason="Music configuration reset via dashboard")
            except discord.HTTPException:
                pass

        return web.json_response({'ok': True})

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

    async def _patch_music_dj_mode(self, request: web.Request) -> web.Response:
        """Update the DJ mode setting for a guild's music player."""
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        mode = body.get('dj_mode')
        if mode not in (0, 1, 2):
            raise web.HTTPBadRequest(text='dj_mode must be 0, 1, or 2')

        config = await self.bot.db.get_guild_config(guild_id)
        await config.update(music_dj_mode=mode)

        return web.json_response({'ok': True, 'dj_mode': mode})
