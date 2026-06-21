"""InternalAPI music/equalizer endpoints."""
from __future__ import annotations

from contextlib import suppress
from typing import Callable

import discord
import wavelink
from aiohttp import web
from discord.ui.view import LayoutView
from discord.utils import MISSING

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

    def _get_guild_player(self, guild_id: int) -> tuple[discord.Guild | None, wavelink.Player | None]:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return None, None
        player = guild.voice_client
        if player is None or not isinstance(player, wavelink.Player):
            return guild, None
        return guild, player

    @staticmethod
    def _effectively_stream(track) -> bool:
        # Lavalink reports radio/live sources as non-streams with an absurd length
        # (≈max int64). Treat anything over 24h as a stream for display purposes.
        return bool(track.is_stream or track.length > 86_400_000)

    def _resolve_requester(self, guild, track) -> dict | None:
        """Resolve the member who queued ``track`` to a small {id, name, avatar} dict."""
        requester_id = getattr(track.extras, 'requester_id', None)
        if not requester_id:
            return None
        member = guild.get_member(int(requester_id))
        if member is None:
            return {'id': str(requester_id), 'name': None, 'avatar': None}
        return {
            'id': str(member.id),
            'name': member.display_name,
            'avatar': member.display_avatar.url,
        }

    def _serialize_track(self, guild, track, *, full: bool = False, artwork_func: Callable | None = None) -> dict:
        """Serialise a wavelink track into the JSON the dashboard player consumes."""
        is_stream = self._effectively_stream(track)
        data = {
            'title': track.title,
            'author': track.author,
            'uri': track.uri,
            'artwork': artwork_func(track) if artwork_func else track.artwork,
            'duration': 0 if is_stream else track.length,
            'is_stream': is_stream,
            'source': track.source,
            'requester': self._resolve_requester(guild, track),
        }
        if full:
            album = None
            if track.album and track.album.name:
                album = {'name': track.album.name, 'url': track.album.url}
            playlist = None
            if track.playlist:
                playlist = {'name': track.playlist.name, 'url': track.playlist.url}
            data.update({
                'artist_url': track.artist.url if track.artist else None,
                'album': album,
                'playlist': playlist,
                'recommended': bool(track.recommended),
                'isrc': track.isrc,
            })
        return data

    def _now_playing_payload(self, guild, player) -> dict | None:
        """Full now-playing snapshot for the live dashboard player."""
        track = player.current
        if track is None:
            return None

        _loop_map = {
            wavelink.QueueMode.normal: 0,
            wavelink.QueueMode.loop: 1,
            wavelink.QueueMode.loop_all: 2,
        }
        data = self._serialize_track(guild, track, full=True, artwork_func=player._resolve_artwork)
        is_stream = data['is_stream']
        data.update({
            'position': 0 if is_stream else player.position,
            'paused': player.paused,
            'volume': player.volume,
            'loop': _loop_map.get(player.queue.mode, 0),
            'shuffle': bool(player.queue.shuffle),
            'autoplay': player.autoplay.value,
        })
        return data

    async def _get_music(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild, player = self._get_guild_player(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        config = await self.bot.db.get_guild_config(guild_id)
        # The panel state and the (optional) dedicated channel are independent:
        # ``use_panel`` may be on with no channel (a temporary panel created in the
        # channel where playback starts) and a channel can exist with the panel off.
        setup = {
            'channel_id': str(config.music_panel_channel_id) if config.music_panel_channel_id else None,
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
                'listeners': [],
            })

        eq_payload = player.filters.equalizer.payload
        gains = [eq_payload[i]['gain'] if i in eq_payload else 0.0 for i in range(15)]

        filters_state = {
            'nightcore': player.filters.timescale.payload.get('speed', 1.0) != 1.0,
            '8d': player.filters.rotation.payload.get('rotationHz', 0.0) != 0.0,
            'lowpass': player.filters.low_pass.payload.get('smoothing', None),
        }

        now_playing = self._now_playing_payload(guild, player)

        # Upcoming tracks (capped) so the dashboard can render a live queue. The
        # bot itself is excluded; history is intentionally omitted to keep the
        # payload small — the panel mirrors the in-Discord "Up Next" view.
        queue = [self._serialize_track(guild, t, artwork_func=player._resolve_artwork) for t in list(player.queue)[:50]]

        # IDs of the (non-bot) members sharing the bot's voice channel. The
        # dashboard's public overview uses this to decide whether a viewer may
        # control playback; the control endpoint re-verifies server-side.
        listeners = []
        if player.channel:
            listeners = [str(m.id) for m in player.channel.members if not m.bot]

        return web.json_response({
            'active': True,
            'equalizer': gains,
            'filters': filters_state,
            'presets': list(PRESETS.keys()),
            'now_playing': now_playing,
            'queue': queue,
            'channel': str(player.channel.id) if player.channel else None,
            'channel_name': player.channel.name if player.channel else None,
            'setup': setup,
            'always_on': always_on,
            'listeners': listeners,
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
        """Remove the dedicated panel channel (delete it and clear the channel config).

        Deliberately leaves ``use_music_panel`` untouched: removing the dedicated
        channel just demotes the panel to a temporary one (created where playback
        starts) — it does not turn the panel off. That's a separate toggle.
        """
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        config = await self.bot.db.get_guild_config(guild_id)
        if not config.music_panel_channel_id:
            raise web.HTTPBadRequest(text='no dedicated channel to remove')

        channel = config.music_panel_channel
        await config.update(music_panel_channel_id=None, music_panel_message_id=None)

        if channel:
            try:
                await channel.delete(reason="Music panel channel removed via dashboard")
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

    async def _post_music_control(self, request: web.Request) -> web.Response:
        """Control the live player from the dashboard's public overview.

        A viewer may only control playback while sharing the bot's voice channel
        (or holding DJ access), and the guild's DJ mode governs which actions are
        allowed — mirroring the in-Discord control panel exactly.
        """
        from app.cogs.music.models import DJMode, is_dj

        guild_id = int(request.match_info['guild_id'])
        guild, player = self._get_guild_player(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')
        if player is None:
            raise web.HTTPBadRequest(text='no active player in this guild')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        # Basic actions are available to any listener; destructive ones are
        # DJ-gated in hybrid mode (mirrors the in-Discord panel exactly).
        BASIC = {'pause', 'resume', 'volume', 'seek'}
        DESTRUCTIVE = {'skip', 'stop', 'back', 'shuffle', 'loop', 'jump', 'move'}

        action = body.get('action')
        user_id = body.get('user_id')
        if action not in BASIC | DESTRUCTIVE:
            raise web.HTTPBadRequest(text='unknown action')
        if not user_id:
            raise web.HTTPBadRequest(text="'user_id' is required")

        member = guild.get_member(int(user_id))
        if member is None:
            raise web.HTTPForbidden(text='you are not a member of this server')

        has_dj = is_dj(member) or member.guild_permissions.manage_guild
        bot_vc = guild.me.voice and guild.me.voice.channel
        author_vc = member.voice and member.voice.channel
        in_voice = bool(bot_vc and author_vc and author_vc == bot_vc)
        dj_mode = DJMode(getattr(await self.bot.db.get_guild_config(guild_id), 'music_dj_mode', 0))

        # Base gate: DJs can always act; otherwise the viewer must share the VC.
        if not has_dj:
            if dj_mode == DJMode.dj_only:
                raise web.HTTPForbidden(text='only members with the DJ role can control the player')
            if not in_voice:
                raise web.HTTPForbidden(text='join the bot\'s voice channel to control playback')
            # Hybrid mode restricts destructive actions to DJs.
            if dj_mode == DJMode.hybrid and action in DESTRUCTIVE:
                raise web.HTTPForbidden(text='this action requires the DJ role')

        from wavelink import QueueMode

        from app.cogs.music.models import ShuffleMode

        # Track-changing actions (skip/back/jump/stop) re-render the panel through
        # wavelink's track-start event; the rest need an explicit panel refresh so
        # the in-Discord embed stays in lock-step with the dashboard.
        refresh_panel = False

        if action == 'pause':
            await player.pause(True)
            refresh_panel = True
        elif action == 'resume':
            await player.pause(False)
            refresh_panel = True
        elif action == 'skip':
            await player.skip()
        elif action == 'back':
            await player.back()
        elif action == 'stop':
            player.queue.reset()
            await player.disconnect()
        elif action == 'volume':
            try:
                value = int(body.get('value'))
            except (TypeError, ValueError):
                raise web.HTTPBadRequest(text="'value' (0-100) is required")
            await player.set_volume(max(0, min(value, 100)))
            refresh_panel = True
        elif action == 'seek':
            if player.current is None or self._effectively_stream(player.current):
                raise web.HTTPBadRequest(text='this track cannot be seeked')
            try:
                position = int(body.get('position'))
            except (TypeError, ValueError):
                raise web.HTTPBadRequest(text="'position' (milliseconds) is required")
            position = max(0, min(position, player.current.length))
            await player.seek(position)
            refresh_panel = True
        elif action == 'loop':
            mode = body.get('mode')
            if mode in (0, 1, 2):
                player.queue.mode = {0: QueueMode.normal, 1: QueueMode.loop, 2: QueueMode.loop_all}[mode]
            else:
                cycle = {
                    QueueMode.normal: QueueMode.loop,
                    QueueMode.loop: QueueMode.loop_all,
                    QueueMode.loop_all: QueueMode.normal,
                }
                player.queue.mode = cycle.get(player.queue.mode, QueueMode.normal)
            refresh_panel = True
        elif action == 'shuffle':
            value = body.get('value')
            if value is None:
                player.queue.shuffle = ShuffleMode.off if player.queue.shuffle else ShuffleMode.on
            else:
                player.queue.shuffle = ShuffleMode.on if value else ShuffleMode.off
            refresh_panel = True
        elif action == 'jump':
            try:
                index = int(body.get('index'))
            except (TypeError, ValueError):
                raise web.HTTPBadRequest(text="'index' is required")
            if not await player.jump_to(index):
                raise web.HTTPBadRequest(text='invalid queue index')
            await player.stop()
        elif action == 'move':
            # Reorder an upcoming track. Indices match the dashboard queue, which
            # serialises ``list(player.queue)`` — i.e. the queue's ``_items`` order.
            try:
                from_idx = int(body.get('from'))
                to_idx = int(body.get('to'))
            except (TypeError, ValueError):
                raise web.HTTPBadRequest(text="'from' and 'to' indices are required")
            items = player.queue._items  # wavelink stores the upcoming queue here
            if not (0 <= from_idx < len(items)) or not (0 <= to_idx < len(items)):
                raise web.HTTPBadRequest(text='index out of range')
            items.insert(to_idx, items.pop(from_idx))
            refresh_panel = True

        if refresh_panel and player.connected and getattr(player, 'panel', MISSING) is not MISSING:
            with suppress(Exception):
                await player.panel.update()

        return web.json_response({'ok': True, 'action': action, 'paused': player.paused if player.connected else False})

    async def _get_music_lyrics(self, request: web.Request) -> web.Response:
        """Resolve time-synced lyrics for the current track.

        The dashboard player drives the karaoke highlight entirely client-side from
        the live playback position, so this is fetched once per track (not polled).
        """
        guild_id = int(request.match_info['guild_id'])
        guild, player = self._get_guild_player(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        empty = {'ok': True, 'has_synced': False, 'title': None, 'source': None, 'lines': [], 'plain': None}
        if player is None or player.current is None:
            return web.json_response(empty)

        cog = self.bot.get_cog('Music')
        if cog is None:
            return web.json_response(empty)

        try:
            result = await cog.fetch_lyrics_for_player(player)
        except Exception:
            result = None
        if result is None:
            return web.json_response(empty)

        lines = []
        if result.has_synced and result.synced is not None:
            lines = [{'time': line.timestamp, 'text': line.text} for line in result.synced.lines]

        return web.json_response({
            'ok': True,
            'has_synced': result.has_synced,
            'title': result.title,
            'source': result.source,
            'lines': lines,
            'plain': result.plain,
        })
