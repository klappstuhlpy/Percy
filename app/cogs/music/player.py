from __future__ import annotations

import datetime
import json
import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Literal, Self

import discord
import wavelink
import yarl
from discord.ext import commands
from discord.utils import MISSING
from wavelink import ChannelTimeoutException, Playable, Playlist

from app.core import Context
from app.utils import convert_duration, helpers
from config import Emojis

from .models import PlayerState, Queue, SearchReturn, ShuffleMode, is_dj

if TYPE_CHECKING:
    from discord.abc import Connectable

    from app.database import GuildConfig

    from .ui import PlayerPanel

log = logging.getLogger(__name__)

# Maps between wavelink's QueueMode enum and the small ints we persist in the DB.
_QUEUE_MODE_TO_INT: dict[wavelink.QueueMode, int] = {
    wavelink.QueueMode.normal: 0,
    wavelink.QueueMode.loop: 1,
    wavelink.QueueMode.loop_all: 2,
}
_INT_TO_QUEUE_MODE: dict[int, wavelink.QueueMode] = {v: k for k, v in _QUEUE_MODE_TO_INT.items()}

# Maximum consecutive playback failures before a 24/7 player gives up (avoids hot loops).
MAX_CONSECUTIVE_ERRORS = 5


class Player(wavelink.Player):
    """Custom mdded-wavelink Player class."""

    def __init__(self, client: discord.Client = MISSING, channel: Connectable = MISSING) -> None:
        super().__init__(client, channel)

        self.panel: PlayerPanel = MISSING
        self.queue: Queue = Queue()

        # -- 24/7 ("always-on") state --------------------------------------
        # When ``always_on`` is set the player never disconnects on inactivity or an
        # empty queue; instead it refills itself based on ``always_on_mode``:
        #   'radio'    -> re-plays ``always_on_source`` (an endless stream URL)
        #   'playlist' -> reloads the saved playlist/album/playlist-query and loops it
        #   'autoplay' -> lets wavelink autoplay keep generating similar tracks forever
        self.always_on: bool = False
        self.always_on_mode: str | None = None
        self.always_on_source: str | None = None

        # Guards refill so two near-simultaneous track-end events can't double-fill.
        self._refilling: bool = False
        # Counts consecutive playback failures to break retry loops on a dead source.
        self._consecutive_errors: int = 0

    @property
    def djs(self) -> list[discord.Member]:
        """Returns a list of all DJ's in the voice channel."""
        djs: list[discord.Member] = [member for member in self.channel.members if is_dj(member)]
        assert self.guild is not None
        assert self.guild.me is not None
        if self.guild.me not in djs:
            djs.append(self.guild.me)
        return djs

    @property
    def connected(self) -> bool:
        """Returns True if the player is connected to a voice channel."""
        return self.channel is not None

    @classmethod
    async def search(
            cls,
            query: str,
            *,
            source: wavelink.TrackSource | str = wavelink.TrackSource.SoundCloud,
            ctx: discord.Interaction | Context | None = None,
            return_first: bool = False
    ) -> Literal[
             SearchReturn.CANCELLED, SearchReturn.NO_YOUTUBE_ALLOWED, SearchReturn.NO_RESULTS] | Playable | Playlist | \
         list[Playable]:
        """Searches for a keyword/url on YouTube, Spotify, or SoundCloud.

        Parameters
        ----------
        query : str
            The keyword or URL to search for.
        source : wavelink.TrackSource | str
            The source to search from.
        ctx : discord.Interaction, Context
            The context of the command.
        return_first : bool
            Whether to return the first result if it's a list.

        Returns
        -------
        wavelink.Playable | wavelink.Playlist | None
            The result of the search.
        """
        from .ui import TrackDisambiguatorView

        check = yarl.URL(query)
        is_url = bool(check and check.host and check.scheme)

        query = query.strip('<>')

        try:
            if not is_url:
                results = await wavelink.Playable.search(query, source=source)
                if return_first and isinstance(results, list):
                    results = results[0]
                else:
                    results = await TrackDisambiguatorView.start(
                        ctx, tracks=results.tracks if isinstance(results, wavelink.Playlist) else results
                    ) if ctx else results

                    if not results:
                        return SearchReturn.CANCELLED
            else:
                lowered = query.casefold()
                if 'youtube' in lowered:
                    return SearchReturn.NO_YOUTUBE_ALLOWED

                # Amazon Music has no Lavalink/LavaSrc source and no public streaming API,
                # so we can't resolve these links. Fail fast with a clear signal.
                if 'music.amazon.' in lowered or 'amazon.com/music' in lowered:
                    return SearchReturn.AMAZON_UNSUPPORTED

                results = await wavelink.Playable.search(query)
        except wavelink.LavalinkLoadException as exc:
            # Expected "can't load this" outcome (bad/unsupported URL, non-stream page,
            # geo-blocked track, ...). Not a code error — log concisely, no traceback.
            log.warning("Lavalink could not load '%s': %s", query, getattr(exc, "error", None) or exc)
            return SearchReturn.NO_RESULTS
        except Exception as exc:
            log.error("Error while searching for '%s'", query, exc_info=exc)
            return SearchReturn.NO_RESULTS

        if not results:
            return SearchReturn.NO_RESULTS

        if ctx:
            if isinstance(results, wavelink.Playable):
                results.extras.requester_id = ctx.user.id
            elif isinstance(results, wavelink.Playlist):
                results.track_extras(requester_id=ctx.user.id)

        if isinstance(results, list) and is_url:
            results = results[0]

        return results

    @classmethod
    async def join(cls, obj: discord.Interaction | Context) -> Self:
        """Join a voice channel and apply the Player class to the voice client."""
        from .ui import PlayerPanel

        assert isinstance(obj.user, discord.Member)
        channel = obj.user.voice.channel if obj.user.voice else None
        if not channel:
            raise commands.BadArgument('You need to be in a voice channel or provide one to connect to.')

        try:
            self = await channel.connect(cls=cls, self_deaf=True)
        except ChannelTimeoutException:
            raise commands.BadArgument('I currently am unable to join your voice channel :/') from None
        assert obj.guild is not None
        assert obj.guild.me is not None
        await obj.guild.me.edit(suppress=False if isinstance(channel, discord.StageChannel) else MISSING, deafen=True)

        disabled: bool = False
        config: GuildConfig = await obj.client.db.get_guild_config(obj.guild_id)
        if config and not config.use_music_panel:
            disabled = True

        assert isinstance(obj.channel, discord.TextChannel)
        self.panel = await PlayerPanel.start(self, channel=obj.channel, disabled=disabled)  # type: ignore
        return self

    @property
    def db(self):  # noqa: ANN201 - returns app.database.Database
        """Shortcut to the bot's database from inside the player."""
        return self.client.db  # type: ignore[attr-defined]

    def _serialize_track(self, track: wavelink.Playable) -> dict[str, Any]:
        """Serialise a track to the minimal JSON we persist for restore."""
        return {
            'uri': track.uri,
            'title': track.title,
            'requester_id': getattr(track.extras, 'requester_id', None),
        }

    async def persist(self, *, position: int | None = None) -> None:
        """|coro|

        Snapshots the current player state into the ``music_sessions`` table so it can
        be restored after a restart or node reconnect. Best-effort: never raises.
        """
        if self.guild is None or self.channel is None:
            return

        text_channel_id: int | None = None
        if self.panel is not MISSING and self.panel.channel is not MISSING:
            text_channel_id = self.panel.channel.id

        # Live streams aren't seekable and their "position" grows unbounded — store 0.
        resume_position = position if position is not None else self.position
        if self.current is not None and self.current.is_stream:
            resume_position = 0

        try:
            await self.db.music_sessions.upsert_session(
                self.guild.id,
                voice_channel_id=self.channel.id,
                text_channel_id=text_channel_id,
                volume=self.volume,
                paused=self.paused,
                queue_mode=_QUEUE_MODE_TO_INT.get(self.queue.mode, 0),
                shuffle=bool(self.queue.shuffle),
                autoplay=self.autoplay.value,
                always_on=self.always_on,
                always_on_mode=self.always_on_mode,
                always_on_source=self.always_on_source,
                current_uri=self.current.uri if self.current else None,
                position=resume_position,
                tracks=[self._serialize_track(t) for t in self.queue],
            )
        except Exception as exc:
            log.debug('Failed to persist music session for guild %s: %s', self.guild.id, exc)

    async def refill_always_on(self) -> None:
        """|coro|

        Re-fills an empty 24/7 player according to its configured mode. Safe to call
        repeatedly; a re-entrancy guard prevents overlapping refills.
        """
        if not self.always_on or self._refilling or not self.always_on_source:
            return

        self._refilling = True
        try:
            source = self.always_on_source
            mode = self.always_on_mode

            if mode == 'autoplay':
                # Let wavelink keep generating recommendations; only re-seed if it dried up.
                self.autoplay = wavelink.AutoPlayMode.enabled
                if self.queue.is_empty and not self.playing:
                    result = await self.search(source, return_first=True)
                    if isinstance(result, (Playable, Playlist)):
                        await self.queue.put_wait(result)
            elif mode == 'playlist':
                if source.startswith('percy:playlist:'):
                    playlist_id = int(source.removeprefix('percy:playlist:'))
                    tracks = await self.db.playlists.get_playlist_tracks(playlist_id)
                    for track_record in tracks:
                        resolved = await self.search(track_record['url'], return_first=True)
                        if isinstance(resolved, (Playable, Playlist)):
                            await self.queue.put_wait(resolved)
                else:
                    result = await self.search(source, return_first=True)
                    if isinstance(result, (Playable, Playlist)):
                        await self.queue.put_wait(result)
                self.queue.mode = wavelink.QueueMode.loop_all
            else:  # 'radio' / direct stream
                result = await self.search(source, return_first=True)
                if isinstance(result, (Playable, Playlist)):
                    await self.queue.put_wait(result)

            if not self.playing and not self.queue.is_empty:
                await self.play(self.queue.get())

            self._consecutive_errors = 0
        except Exception as exc:
            log.warning(
                'Failed to refill 24/7 player in guild %s: %s',
                self.guild.id if self.guild else None, exc,
            )
        finally:
            self._refilling = False

    @classmethod
    async def restore(cls, bot: Any, record: Any) -> Self | None:
        """|coro|

        Rebuilds a player from a persisted ``music_sessions`` row after a restart.

        Reconnects to the saved voice channel, restores volume/loop/shuffle/autoplay and
        the 24/7 configuration, re-resolves the current + queued tracks by URI, then
        resumes playback (seeking back to the saved position). Returns ``None`` if the
        guild/channel is gone or reconnecting fails.
        """
        from .ui import PlayerPanel

        guild = bot.get_guild(record['guild_id'])
        if guild is None:
            return None

        channel = guild.get_channel(record['voice_channel_id'])
        if not isinstance(channel, discord.VoiceChannel | discord.StageChannel):
            return None

        try:
            self = await channel.connect(cls=cls, self_deaf=True)
        except Exception as exc:
            log.warning('Failed to reconnect player to %s during restore: %s', channel, exc)
            return None

        self.always_on = bool(record['always_on'])
        self.always_on_mode = record['always_on_mode']
        self.always_on_source = record['always_on_source']
        self.queue.mode = _INT_TO_QUEUE_MODE.get(record['queue_mode'], wavelink.QueueMode.normal)
        self.queue.shuffle = ShuffleMode.on if record['shuffle'] else ShuffleMode.off
        try:
            self.autoplay = wavelink.AutoPlayMode(record['autoplay'])
        except ValueError:
            self.autoplay = wavelink.AutoPlayMode.partial
        if self.always_on:
            self.inactive_timeout = None

        config: GuildConfig = await bot.db.get_guild_config(guild.id)
        disabled = bool(config and not config.use_music_panel)
        messageable = (discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel)
        text_channel = config.music_panel_channel
        if text_channel is None and record['text_channel_id']:
            resolved = guild.get_channel(record['text_channel_id'])
            text_channel = resolved if isinstance(resolved, messageable) else None  # type: ignore[assignment]
        if text_channel is not None:
            with suppress(Exception):
                self.panel = await PlayerPanel.start(self, channel=text_channel, disabled=disabled)  # type: ignore[arg-type]

        tracks = record['tracks']
        if isinstance(tracks, str):
            tracks = json.loads(tracks)

        uris: list[str] = []
        if record['current_uri']:
            uris.append(record['current_uri'])
        uris.extend(t['uri'] for t in (tracks or []) if t.get('uri'))

        for uri in uris:
            result = await cls.search(uri, return_first=True)
            if isinstance(result, Playable | Playlist):
                await self.queue.put_wait(result)

        if self.queue.is_empty:
            if self.always_on:
                await self.refill_always_on()
            else:
                await self.disconnect()
                return None
        else:
            await self.play(self.queue.get(), volume=record['volume'])
            if record['position'] and record['current_uri']:
                with suppress(Exception):
                    await self.seek(record['position'])
            if record['paused']:
                await self.pause(True)

        with suppress(Exception):
            await self.set_volume(record['volume'])
        if self.panel is not MISSING:
            try:
                await self.panel.update()
            except Exception as exc:
                log.warning('Failed to update panel during restore for guild %s: %s', guild.id, exc)

        log.info('Restored music session for guild %s (always_on=%s)', guild.id, self.always_on)
        return self

    async def hydrate(self, record: Any) -> None:
        """|coro|

        Re-apply persisted metadata onto an already-connected (wavelink-resumed) player.

        After a quick restart the Lavalink session resumes and audio keeps playing, but
        the reconstructed player has default state. This restores our 24/7 flags, panel
        and upcoming queue **without** re-playing the current track.
        """
        from .ui import PlayerPanel

        self.always_on = bool(record['always_on'])
        self.always_on_mode = record['always_on_mode']
        self.always_on_source = record['always_on_source']
        if self.always_on:
            self.inactive_timeout = None
        try:
            self.autoplay = wavelink.AutoPlayMode(record['autoplay'])
        except ValueError:
            pass
        self.queue.mode = _INT_TO_QUEUE_MODE.get(record['queue_mode'], wavelink.QueueMode.normal)
        self.queue.shuffle = ShuffleMode.on if record['shuffle'] else ShuffleMode.off

        if self.panel is MISSING and self.guild is not None:
            config = await self.client.db.get_guild_config(self.guild.id)  # type: ignore[attr-defined]
            disabled = bool(config and not config.use_music_panel)
            messageable = (discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel)
            text_channel = config.music_panel_channel
            if text_channel is None and record['text_channel_id']:
                resolved = self.guild.get_channel(record['text_channel_id'])
                text_channel = resolved if isinstance(resolved, messageable) else None  # type: ignore[assignment]
            if text_channel is not None:
                with suppress(Exception):
                    self.panel = await PlayerPanel.start(self, channel=text_channel, disabled=disabled)  # type: ignore[arg-type]

        if self.queue.is_empty:
            tracks = record['tracks']
            if isinstance(tracks, str):
                tracks = json.loads(tracks)
            for entry in (tracks or []):
                uri = entry.get('uri')
                if not uri:
                    continue
                result = await self.search(uri, return_first=True)
                if isinstance(result, Playable | Playlist):
                    await self.queue.put_wait(result)

        if self.panel is not MISSING:
            try:
                await self.panel.update()
            except Exception as exc:
                log.warning('Failed to update panel during hydrate for guild %s: %s',
                            self.guild.id if self.guild else None, exc)

    async def disconnect(self, **kwargs: Any) -> None:
        """Disconnects the player from the voice channel.

        Only an *explicit* (non-forced) disconnect forgets the persisted session.
        discord.py calls ``disconnect(force=True)`` for every voice client during bot
        shutdown — if we deleted the row there, a restart would have nothing to resume.
        So a forced disconnect keeps the session intact for restore.
        """
        if self.guild is not None and not kwargs.get("force"):
            with suppress(Exception):
                await self.db.music_sessions.delete_session(self.guild.id)

        if self.panel is not MISSING:
            if self.panel.state != PlayerState.STOPPED:
                await self.panel.stop()

            if self.panel.__is_temporary__:
                with suppress(discord.HTTPException):
                    await self.panel.msg.delete()

        if len(self.channel.members) == 1 and isinstance(self.channel, discord.StageChannel):
            with suppress(discord.HTTPException):
                if self.channel.instance is not None:
                    await self.channel.instance.delete()

        await super().disconnect(**kwargs)

    async def cleanupleft(self) -> None:
        """Removes all tracks from the queue that are not in the voice channel."""
        assert self.queue.history is not None
        member_ids = {m.id for m in self.channel.members}
        for track in self.queue.all:
            if not hasattr(track.extras, 'requester_id'):
                continue
            if track.extras.requester_id not in member_ids:
                if self.current == track:
                    await self.stop()

                if track in self.queue.history:
                    self.queue.history.remove(track)
                else:
                    self.queue.remove(track)

    async def back(self) -> bool:
        """Goes back to the previous track in the queue."""
        if self.queue.history_is_empty:
            return False

        assert self.queue.history is not None
        current_track = self.queue.history._items.pop()
        track_to_revert = self.queue.history._items.pop()

        self.queue.put_at(0, track_to_revert)
        self.queue.put_at(1, current_track)

        await self.stop()
        return True

    async def jump_to(self, index: int) -> bool:
        """Jumps to a specific track in the queue.

        Parameters
        ----------
        index : int
            The index to jump to.

        Returns
        -------
        bool
            Whether the jump was successful.
        """
        if index < 0 or index >= len(self.queue.all):
            return False

        tracks_to_queue = self.queue.all[index:]
        tracks_to_history = self.queue.all[:index]

        self.queue.clear()
        await self.queue.put_wait(tracks_to_queue)

        assert self.queue.history is not None
        self.queue.history.clear()
        await self.queue.history.put_wait(tracks_to_history)
        return True

    async def send_track_add(
            self,
            added: wavelink.Playable | wavelink.Playlist,
            obj: Context | discord.Interaction | None = None,
            short: bool = False
    ) -> discord.Message | None | Any:
        """Sends a message when a track is added to the queue.

        Parameters
        ----------
        added : wavelink.Playable | wavelink.Playlist
            The track or playlist that was added.
        obj : Context | discord.Interaction
            The context of the command.
        short : bool
            Whether to send a short message.
        """
        is_playlist = isinstance(added, wavelink.Playlist)
        position = self.queue.all.index(added.tracks[0] if is_playlist else added) + 1
        length = sum(track.length for track in added.tracks) if is_playlist else added.length
        playing_in = sum(track.length for track in self.queue[:position] if not track.is_stream)
        title = added.name if is_playlist else added.title
        url = added.url if is_playlist else added.uri
        description = f'**[{title}]({url})**'

        if short:
            embed = discord.Embed(
                description=f'{Emojis.success} Started playing {description}',
                color=helpers.Colour.white()
            )
        else:
            embed = discord.Embed(
                title=f'Added {'Playlist' if is_playlist else 'Track'}',
                description=description,
                color=helpers.Colour.white()
            )
            embed.add_field(name='ETA', value=discord.utils.format_dt(
                discord.utils.utcnow() + datetime.timedelta(milliseconds=playing_in), 'R'))
            embed.add_field(name='Track length', value=convert_duration(length))
            embed.add_field(name='\u200b', value='\u200b')
            embed.add_field(name='Position in Queue', value=f'**#{position}/{len(self.queue.all)}**')

            if added.artwork:
                embed.set_thumbnail(url=added.artwork)

        if not obj:
            return await self.panel.channel.send(embed=embed)

        if not short:
            embed.set_footer(text=f"Requested by {obj.user}", icon_url=obj.user.display_avatar.url)

        if isinstance(obj, Context):
            return await obj.send(embed=embed, delete_after=15)
        else:
            if obj and obj.response.is_done():
                return await obj.followup.send(embed=embed, delete_after=15)
            else:
                return await obj.response.send_message(embed=embed, delete_after=15)

    @classmethod
    def preview_container(cls, guild: discord.Guild) -> discord.ui.Container:
        container = discord.ui.Container(accent_color=helpers.Colour.brand())

        heading = (
            "## Music Player Panel\n"
            "The control panel was closed, the queue is currently empty and I got nothing to do.\n"
            "You can start a new player session by invoking the </play:1070054930125176923> command.\n\n"
            "*Once you play a new track, this message is going to be the new player panel if it's not deleted, "
            "otherwise I'm going to create a new panel.*"
        )
        icon = guild.icon.url if guild is not None and guild.icon else None
        if icon is not None:
            container.add_item(discord.ui.Section(heading, accessory=discord.ui.Thumbnail(icon)))
        else:
            container.add_item(discord.ui.TextDisplay(heading))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay("-# last updated"))

        return container
