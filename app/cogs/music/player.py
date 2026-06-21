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

# Direct stream/radio tracks carry no cover art, so we brand them with a fixed image.
RADIO_ARTWORK_URL = "https://klappstuhl.me/gallery/raw/gERCu.avif"


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
        # Active live-lyrics session (a ui.LiveLyricsView), if one is running.
        self.lyrics_session: Any = None

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

    @property
    def played_history(self) -> list[wavelink.Playable]:
        """All played tracks in chronological order (oldest first).

        Manual/seed plays live in ``queue.history`` and autoplay recommendations in
        ``auto_queue.history`` — wavelink keeps the two separate. The currently
        playing track is the most recent entry. Used for the panel's queue position,
        the back-button gating and ``back()`` itself.
        """
        hist = list(self.queue.history) if self.queue.history is not None else []
        auto = list(self.auto_queue.history) if self.auto_queue.history is not None else []
        return hist + auto

    @property
    def upcoming(self) -> list[wavelink.Playable]:
        """Upcoming tracks: the manual queue, plus autoplay recommendations from
        ``auto_queue`` when autoplay is enabled (they play once the queue drains).
        """
        items = list(self.queue)
        if self.autoplay is wavelink.AutoPlayMode.enabled:
            items += list(self.auto_queue)
        return items

    async def refresh_panel(self) -> None:
        """Re-render the control panel if one exists.

        Manual commands (volume, loop, shuffle, ...) call this to reflect their
        change in the panel. It is a no-op when no panel is active (disabled in
        config, or never started), so callers don't need to guard against the
        ``MISSING`` sentinel.
        """
        if self.panel is MISSING:
            return
        if self.panel.channel is MISSING:
            return
        if self.panel.message is MISSING:
            return

        try:
            await self.panel.update()
        except Exception as exc:
            log.warning('Failed to refresh music panel for guild %s: %s', getattr(self.guild, 'id', None), exc)

    @classmethod
    async def search(
            cls,
            query: str,
            *,
            source: wavelink.TrackSource | str = wavelink.TrackSource.YouTubeMusic,
            ctx: discord.Interaction | Context | None = None,
            return_first: bool = False
    ) -> Literal[
             SearchReturn.CANCELLED, SearchReturn.NO_RESULTS, SearchReturn.AMAZON_UNSUPPORTED] | Playable | Playlist | \
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

        # Normalise cover art once, right here, so every downstream consumer
        # (the panel, the dashboard, the internal API) can simply read
        # ``track.artwork`` without re-resolving the YouTube fallback.
        if isinstance(results, wavelink.Playlist):
            normalise = results.tracks
        elif isinstance(results, list):
            normalise = results
        else:
            normalise = [results]
        for track in normalise:
            cls._normalise_artwork(track)

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

    @staticmethod
    def _resolve_artwork(track: wavelink.Playable) -> str | None:
        """Best-effort cover art URL for a track.

        Lavalink hands YouTube tracks a ``maxresdefault`` thumbnail that 404s for any
        non-HD upload (and is sometimes absent entirely). Discord proxies images so it
        never notices, but a browser <img> just fails. Fall back to ``hqdefault``,
        which exists for every video, when there's no artwork or it's a maxres URL.
        """
        artwork = track.artwork
        source = (track.source or "").lower()
        is_youtube = source.startswith("youtube")
        if is_youtube and track.identifier:
            hq = f'https://i.ytimg.com/vi/{track.identifier}/hqdefault.jpg'
            if not artwork or 'maxresdefault' in artwork:
                return hq
        return artwork

    @classmethod
    def _normalise_artwork(cls, track: wavelink.Playable) -> None:
        """Write the display-ready cover-art URL back onto the track in place.

        ``Playable.artwork`` has no public setter, so we assign the backing field.
        Centralised so every track-entry path (search, 24/7 refill, autoplay) yields
        tracks whose ``artwork`` is ready to use without callers re-resolving. Safe to
        call more than once — ``_resolve_artwork`` is idempotent.
        """
        track._artwork = cls._resolve_artwork(track)

    @staticmethod
    def _serialize_track(track: wavelink.Playable) -> dict[str, Any]:
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
                    # Radio streams have no cover art — brand them with a fixed image.
                    tracks = result.tracks if isinstance(result, Playlist) else [result]
                    for track in tracks:
                        track._artwork = RADIO_ARTWORK_URL  # no public setter on Playable.artwork
                    await self.queue.put_wait(result)

            if not self.playing and not self.queue.is_empty:
                # In autoplay mode, eagerly populate the auto_queue with recommendations
                # so "up next" is visible immediately. wavelink otherwise only fills the
                # auto_queue on track end, leaving /queue and the dashboard empty right
                # after autoplay is enabled.
                populate = self.autoplay is wavelink.AutoPlayMode.enabled
                await self.play(self.queue.get(), populate=populate, max_populate=10)

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
        if self.lyrics_session is not None:
            with suppress(Exception):
                await self.lyrics_session.stop()

        if self.guild is not None and not kwargs.get("force"):
            with suppress(Exception):
                await self.db.music_sessions.delete_session(self.guild.id)

        if self.panel is not MISSING:
            if self.panel.state != PlayerState.STOPPED:
                await self.panel.stop()

            if self.panel.__is_temporary__ and self.panel.msg:
                with suppress(discord.HTTPException):
                    await self.panel.msg.delete()

        if len(self.channel.members) == 1 and isinstance(self.channel, discord.StageChannel):
            with suppress(discord.HTTPException):
                if self.channel.instance is not None:
                    await self.channel.instance.delete()

        await super().disconnect(**kwargs)

    async def cleanupleft(self) -> None:
        """Remove upcoming tracks requested by members who left the voice channel.

        Only the upcoming queue is pruned — already-played history is left intact
        (purging it would corrupt the history view and ``back()``). Autoplay
        recommendations have no requester and are never touched. If the currently
        playing track was requested by someone who left, it is skipped.
        """
        member_ids = {m.id for m in self.channel.members}

        def _requester_left(track: wavelink.Playable) -> bool:
            requester_id = getattr(track.extras, 'requester_id', None)
            return requester_id is not None and requester_id not in member_ids

        # Snapshot first — we mutate the queue while iterating.
        for track in list(self.queue):
            if _requester_left(track):
                self.queue.remove(track)

        # Skip the currently playing track if its requester is gone.
        if self.current is not None and _requester_left(self.current):
            await self.skip()

    async def back(self) -> bool:
        """Replay the previously played track.

        Works for both the manual queue (history in ``queue.history``) and autoplay
        (recommendation history in ``auto_queue.history``): it pulls the current and
        previous tracks out of whichever history actually holds them, re-queues them
        at the front (previous first), then stops so the next-track machinery plays
        them back in order. Returns ``False`` when there is no previous track.
        """
        assert self.queue.history is not None and self.auto_queue.history is not None
        q_items = self.queue.history._items
        a_items = self.auto_queue.history._items

        # The current track is the tail of whichever history it was added to
        # (manual plays -> queue.history, autoplay recommendations ->
        # auto_queue.history). Anchor on that so back() works in pure-manual,
        # pure-autoplay, and mixed sessions alike.
        if a_items and a_items[-1] is self.current:
            primary, secondary = a_items, q_items
        elif q_items and q_items[-1] is self.current:
            primary, secondary = q_items, a_items
        else:
            return False

        current = primary.pop()
        # The previous track is the new tail of the same history, or — if that
        # history only held the current track — the tail of the other one.
        if primary:
            previous = primary.pop()
        elif secondary:
            previous = secondary.pop()
        else:
            primary.append(current)  # nothing to go back to; restore and bail
            return False

        self.queue.put_at(0, previous)
        self.queue.put_at(1, current)

        await self.stop()
        return True

    async def jump_to(self, index: int) -> bool:
        """Jump to the track at ``index`` (0-based) in the *upcoming* queue.

        Tracks before the target are skipped (dropped from the queue); the target
        and everything after it stay queued. The caller is expected to ``stop()``
        afterwards so the target starts playing. History is left untouched — the
        currently playing track is already recorded there and remains the most
        recent entry, so ``back()`` keeps working.

        Parameters
        ----------
        index : int
            The index into the upcoming queue to jump to.

        Returns
        -------
        bool
            Whether the jump was successful.
        """
        upcoming = list(self.queue)
        if index < 0 or index >= len(upcoming):
            return False

        self.queue.clear()
        await self.queue.put_wait(upcoming[index:])
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
