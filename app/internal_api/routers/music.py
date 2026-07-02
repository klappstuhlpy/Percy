"""Music, equalizer, and live player control endpoints."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord
import wavelink
from discord.ui.view import LayoutView
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..dependencies import BotDep, GuildDep, verify_token

if TYPE_CHECKING:
    from app.core import Bot

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Music"], dependencies=[Depends(verify_token)])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class AlwaysOnBody(BaseModel):
    enabled: bool = True
    mode: str | None = None
    source: str | None = None
    voice_channel_id: int | str | None = None


class SetupBody(BaseModel):
    channel_id: int | str | None = None


class EqualizerBody(BaseModel):
    preset: str | None = None
    bands: list[float] | None = None


class FiltersBody(BaseModel):
    action: str
    smoothing: float | None = None


class DJModeBody(BaseModel):
    dj_mode: int


class ControlBody(BaseModel):
    action: str
    user_id: int | str
    value: int | float | None = None
    position: int | None = None
    mode: int | None = None
    index: int | None = None
    # 'from' is a reserved keyword in Python; use model alias
    from_idx: int | None = None
    to: int | None = None

    model_config = {"populate_by_name": True}

    def model_post_init(self, _context: Any) -> None:
        """Pydantic v2 doesn't accept 'from' as a field name directly."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_guild_player(bot: Bot, guild_id: int) -> tuple[discord.Guild | None, wavelink.Player | None]:
    guild = bot.get_guild(guild_id)
    if guild is None:
        return None, None
    player = guild.voice_client
    if player is None or not isinstance(player, wavelink.Player):
        return guild, None
    return guild, player


def _effectively_stream(track: wavelink.Playable) -> bool:
    """Treat anything over 24h or marked as a stream as a live source."""
    return bool(track.is_stream or track.length > 86_400_000)


def _resolve_requester(guild: discord.Guild, track: wavelink.Playable) -> dict | None:
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


def _serialize_track(guild: discord.Guild, track: wavelink.Playable, *, full: bool = False) -> dict:
    """Serialise a wavelink track into the JSON the dashboard player consumes."""
    is_stream = _effectively_stream(track)
    data: dict[str, Any] = {
        'title': track.title,
        'author': track.author,
        'uri': track.uri,
        'artwork': track.artwork,
        'duration': 0 if is_stream else track.length,
        'is_stream': is_stream,
        'source': track.source,
        'requester': _resolve_requester(guild, track),
        'autoplay': False,
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


def _now_playing_payload(guild: discord.Guild, player: wavelink.Player) -> dict | None:
    """Full now-playing snapshot for the live dashboard player."""
    track = player.current
    if track is None:
        return None

    _loop_map = {
        wavelink.QueueMode.normal: 0,
        wavelink.QueueMode.loop: 1,
        wavelink.QueueMode.loop_all: 2,
    }
    data = _serialize_track(guild, track, full=True)
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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/music")
async def get_music(guild: GuildDep, bot: BotDep) -> dict:
    """Live player snapshot: now playing, queue, history, equalizer, filters, 24/7 state."""
    _, player = _get_guild_player(bot, guild.id)

    config = await bot.db.get_guild_config(guild.id)
    setup = {
        'channel_id': str(config.music_panel_channel_id) if config.music_panel_channel_id else None,
        'message_id': str(config.music_panel_message_id) if config.music_panel_message_id else None,
        'use_panel': config.use_music_panel,
        'dj_mode': getattr(config, 'music_dj_mode', 0),
    }

    # 24/7 ("always-on") state: prefer the live player, fall back to the persisted row.
    always_on: dict[str, Any] = {'enabled': False, 'mode': None, 'source': None}
    if player is not None and getattr(player, 'always_on', False):
        always_on = {
            'enabled': True,
            'mode': player.always_on_mode,
            'source': player.always_on_source,
        }
    else:
        session = await bot.db.music_sessions.get_session(guild.id)
        if session and session['always_on']:
            always_on = {
                'enabled': True,
                'mode': session['always_on_mode'],
                'source': session['always_on_source'],
            }

    if player is None:
        return {
            'active': False,
            'equalizer': [0.0] * 15,
            'filters': {'nightcore': False, '8d': False, 'lowpass': None},
            'presets': list(PRESETS.keys()),
            'setup': setup,
            'always_on': always_on,
            'listeners': [],
        }

    eq_payload = player.filters.equalizer.payload
    gains = [eq_payload[i]['gain'] if i in eq_payload else 0.0 for i in range(15)]

    filters_state = {
        'nightcore': player.filters.timescale.payload.get('speed', 1.0) != 1.0,
        '8d': player.filters.rotation.payload.get('rotationHz', 0.0) != 0.0,
        'lowpass': player.filters.low_pass.payload.get('smoothing', None),
    }

    now_playing = _now_playing_payload(guild, player)

    queue = [_serialize_track(guild, t) for t in list(player.queue)[:50]]

    # Append autoplay recommendations when the manual queue is short.
    if len(queue) < 50 and player.autoplay is wavelink.AutoPlayMode.enabled:
        for t in list(player.auto_queue)[: 50 - len(queue)]:
            player._normalise_artwork(t)
            data = _serialize_track(guild, t)
            data['autoplay'] = True
            queue.append(data)

    # Recently played history — most-recent-first, excludes current track.
    played = [t for t in player.played_history if t is not player.current][::-1][:50]
    history = []
    for t in played:
        player._normalise_artwork(t)
        history.append(_serialize_track(guild, t))

    # Non-bot members sharing the bot's voice channel.
    listeners: list[str] = []
    if player.channel:
        listeners = [str(m.id) for m in player.channel.members if not m.bot]

    return {
        'active': True,
        'equalizer': gains,
        'filters': filters_state,
        'presets': list(PRESETS.keys()),
        'now_playing': now_playing,
        'queue': queue,
        'history': history,
        'channel': str(player.channel.id) if player.channel else None,
        'channel_name': player.channel.name if player.channel else None,
        'setup': setup,
        'always_on': always_on,
        'listeners': listeners,
    }


@router.post("/music/setup")
async def post_music_setup(guild: GuildDep, bot: BotDep, body: SetupBody) -> dict:
    """Create or use a channel and send the music panel message."""
    config = await bot.db.get_guild_config(guild.id)
    if config.music_panel_channel_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='music configuration already exists')

    channel: discord.TextChannel | None = None

    if body.channel_id:
        channel = guild.get_channel(int(body.channel_id))  # type: ignore[assignment]
        if channel is None or not isinstance(channel, discord.TextChannel):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='invalid channel')
    else:
        category = guild.text_channels[0].category if guild.text_channels else None
        parent = category or guild
        channel = await parent.create_text_channel(name="\U0001f3b6percy-music")

    assert bot.user is not None
    await channel.edit(
        slowmode_delay=3,
        topic=DEFAULT_CHANNEL_DESCRIPTION.format(bot=bot.user.mention),
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

    return {
        'ok': True,
        'channel_id': str(channel.id),
        'channel_name': channel.name,
    }


@router.post("/music/reset")
async def post_music_reset(guild: GuildDep, bot: BotDep) -> dict:
    """Delete the dedicated panel channel and clear the config references."""
    config = await bot.db.get_guild_config(guild.id)
    if not config.music_panel_channel_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='no dedicated channel to remove')

    channel = config.music_panel_channel
    await config.update(music_panel_channel_id=None, music_panel_message_id=None)

    if channel:
        try:
            await channel.delete(reason="Music panel channel removed via dashboard")
        except discord.HTTPException:
            pass

    return {'ok': True}


@router.post("/music/equalizer")
async def post_music_equalizer(guild: GuildDep, bot: BotDep, body: EqualizerBody) -> dict:
    """Apply an EQ preset or custom 15-band gains to the live player."""
    _, player = _get_guild_player(bot, guild.id)
    if player is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='no active player in this guild')

    if body.preset:
        if body.preset not in PRESETS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'unknown preset: {body.preset}')
        gains = PRESETS[body.preset]
    elif body.bands is not None:
        if len(body.bands) != 15:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='bands must be an array of 15 gain values')
        for g in body.bands:
            if not -0.25 <= g <= 1.0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail='each gain must be between -0.25 and 1.0',
                )
        gains = body.bands
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='provide either preset or bands')

    filters: wavelink.Filters = player.filters
    filters.equalizer.set(bands=[{'band': i, 'gain': g} for i, g in enumerate(gains)])
    await player.set_filters(filters)

    return {'ok': True, 'equalizer': gains}


@router.post("/music/filters")
async def post_music_filters(guild: GuildDep, bot: BotDep, body: FiltersBody) -> dict:
    """Toggle nightcore/8d/lowpass or reset all filters."""
    _, player = _get_guild_player(bot, guild.id)
    if player is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='no active player in this guild')

    filters: wavelink.Filters = player.filters
    action = body.action

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
        if body.smoothing is None:
            filters.low_pass.reset()
        else:
            filters.low_pass.set(smoothing=body.smoothing)
    elif action == 'reset':
        filters.reset()
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='unknown action')

    await player.set_filters(filters)
    return {'ok': True}


@router.post("/music/247")
async def post_music_247(guild: GuildDep, bot: BotDep, body: AlwaysOnBody) -> dict:
    """Enable or disable the 24/7 always-on player."""
    cog = bot.get_cog('Music')
    if cog is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='music feature is unavailable')

    if not body.enabled:
        await cog.disable_always_on(guild)  # type: ignore[attr-defined]
        return {'ok': True, 'always_on': {'enabled': False, 'mode': None, 'source': None}}

    if body.mode not in ('radio', 'playlist', 'autoplay') or not body.source:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'mode' (radio|playlist|autoplay) and 'source' are required",
        )

    source = body.source

    # Radio mode accepts a friendly preset name (e.g. "lofi") as a shortcut.
    if body.mode == 'radio':
        from app.cogs.music.cog import RADIO_PRESETS

        preset = RADIO_PRESETS.get(source.strip().lower())
        if preset:
            source = preset[1]

    channel: discord.VoiceChannel | discord.StageChannel | None = None
    if body.voice_channel_id:
        resolved = guild.get_channel(int(body.voice_channel_id))
        if isinstance(resolved, discord.VoiceChannel | discord.StageChannel):
            channel = resolved
    elif isinstance(player := guild.voice_client, wavelink.Player):
        channel = player.channel  # type: ignore[assignment]

    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='a valid voice_channel_id is required to start 24/7',
        )

    from app.cogs.music.models import SearchReturn
    from app.cogs.music.player import Player

    probe = await Player.search(source, return_first=True)
    if isinstance(probe, SearchReturn) or not probe:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='could not resolve that source')

    await cog.enable_always_on(guild, channel, None, body.mode, source)  # type: ignore[attr-defined]
    return {'ok': True, 'always_on': {'enabled': True, 'mode': body.mode, 'source': source}}


@router.patch("/music/dj-mode")
async def patch_music_dj_mode(guild: GuildDep, bot: BotDep, body: DJModeBody) -> dict:
    """Set the DJ mode (0=off, 1=hybrid, 2=dj_only)."""
    if body.dj_mode not in (0, 1, 2):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='dj_mode must be 0, 1, or 2')

    config = await bot.db.get_guild_config(guild.id)
    await config.update(music_dj_mode=body.dj_mode)

    return {'ok': True, 'dj_mode': body.dj_mode}


@router.post("/music/control")
async def post_music_control(guild: GuildDep, bot: BotDep, body: ControlBody) -> dict:
    """Control playback from the dashboard (pause/resume/skip/back/volume/seek/loop/shuffle/jump/move/stop).

    Enforces voice-channel presence and DJ-mode permissions, mirroring the
    in-Discord control panel exactly.
    """
    from app.cogs.music.models import DJMode, is_dj

    _, player = _get_guild_player(bot, guild.id)
    if player is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='no active player in this guild')

    BASIC = {'pause', 'resume', 'volume', 'seek'}
    DESTRUCTIVE = {'skip', 'stop', 'back', 'shuffle', 'loop', 'jump', 'move'}

    action = body.action
    user_id = body.user_id

    if action not in BASIC | DESTRUCTIVE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='unknown action')
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="'user_id' is required")

    member = guild.get_member(int(user_id))
    if member is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='you are not a member of this server')

    has_dj = is_dj(member) or member.guild_permissions.manage_guild
    bot_vc = guild.me.voice and guild.me.voice.channel
    author_vc = member.voice and member.voice.channel
    in_voice = bool(bot_vc and author_vc and author_vc == bot_vc)
    dj_mode = DJMode(getattr(await bot.db.get_guild_config(guild.id), 'music_dj_mode', 0))

    # Base gate: DJs can always act; otherwise the viewer must share the VC.
    if not has_dj:
        if dj_mode == DJMode.dj_only:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail='only members with the DJ role can control the player',
            )
        if not in_voice:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="join the bot's voice channel to control playback",
            )
        # Hybrid mode restricts destructive actions to DJs.
        if dj_mode == DJMode.hybrid and action in DESTRUCTIVE:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='this action requires the DJ role')

    from wavelink import QueueMode

    from app.cogs.music.models import ShuffleMode

    # Track-changing actions re-render the panel through wavelink's track-start
    # event; the rest need an explicit panel refresh.
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
            value = int(body.value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="'value' (0-100) is required")
        await player.set_volume(max(0, min(value, 100)))
        refresh_panel = True
    elif action == 'seek':
        if player.current is None or _effectively_stream(player.current):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='this track cannot be seeked')
        try:
            position = int(body.position)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="'position' (milliseconds) is required")
        position = max(0, min(position, player.current.length))
        await player.seek(position)
        refresh_panel = True
    elif action == 'loop':
        mode = body.mode
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
        value = body.value
        if value is None:
            player.queue.shuffle = ShuffleMode.off if player.queue.shuffle else ShuffleMode.on
        else:
            player.queue.shuffle = ShuffleMode.on if value else ShuffleMode.off
        refresh_panel = True
    elif action == 'jump':
        try:
            index = int(body.index)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="'index' is required")
        if not await player.jump_to(index):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='invalid queue index')
        await player.stop()
    elif action == 'move':
        try:
            from_idx = int(body.from_idx)  # type: ignore[arg-type]
            to_idx = int(body.to)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="'from' and 'to' indices are required")
        items = player.queue._items  # wavelink stores the upcoming queue here
        if not (0 <= from_idx < len(items)) or not (0 <= to_idx < len(items)):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='index out of range')
        items.insert(to_idx, items.pop(from_idx))
        refresh_panel = True

    if refresh_panel and player.connected:
        await player.refresh_panel()

    return {'ok': True, 'action': action, 'paused': player.paused if player.connected else False}


@router.get("/music/lyrics")
async def get_music_lyrics(guild: GuildDep, bot: BotDep) -> dict:
    """Resolve time-synced lyrics for the current track."""
    _, player = _get_guild_player(bot, guild.id)

    empty = {'ok': True, 'has_synced': False, 'title': None, 'source': None, 'lines': [], 'plain': None}
    if player is None or player.current is None:
        return empty

    cog = bot.get_cog('Music')
    if cog is None:
        return empty

    try:
        result = await cog.fetch_lyrics_for_player(player)  # type: ignore[attr-defined]
    except Exception:
        result = None
    if result is None:
        return empty

    lines = []
    if result.has_synced and result.synced is not None:
        lines = [{'time': line.timestamp, 'text': line.text} for line in result.synced.lines]

    return {
        'ok': True,
        'has_synced': result.has_synced,
        'title': result.title,
        'source': result.source,
        'lines': lines,
        'plain': result.plain,
    }
