from __future__ import annotations

import asyncio
import datetime
import logging
import random
import re
from contextlib import suppress
from typing import Annotated, Any, ClassVar, Literal, cast
from urllib.parse import urljoin

import discord
import wavelink
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString
from discord import app_commands, player
from discord.app_commands import Choice
from discord.ext import commands, tasks
from discord.utils import MISSING
from wavelink import Playable

from app.core import Bot, Cog, Context, Flags, command, describe, flag, group, store_true
from app.core.pagination import BasePaginator
from app.utils import (
    ProgressBar,
    cache,
    checks,
    convert_duration,
    fuzzy,
    get_shortened_string,
    helpers,
    pagify,
    pluralize,
)
from config import Emojis, genius_key

from .models import Playlist, PlaylistTrack, SearchReturn, ShuffleMode
from .player import MAX_CONSECUTIVE_ERRORS, Player
from .ui import MusicSetupView, PlaylistPaginator

log = logging.getLogger(__name__)

# Curated, verified direct-stream radio presets for `music 247 radio <name>`.
# Each value is (human label, direct stream URL). These are real audio streams
# (SomaFM / Antenne Bayern), not station landing pages, so Lavalink can play them.
RADIO_PRESETS: dict[str, tuple[str, str]] = {
    "lofi": ("Fluid — instrumental hip-hop / lofi", "https://ice.somafm.com/fluid-128-mp3"),
    "chill": ("Groove Salad — chilled ambient beats", "https://ice.somafm.com/groovesalad-128-mp3"),
    "ambient": ("Drone Zone — atmospheric ambient", "https://ice.somafm.com/dronezone-128-mp3"),
    "space": ("Space Station Soma — ambient electronica", "https://ice.somafm.com/spacestation-128-mp3"),
    "vocals": ("Lush — electronic female vocals", "https://ice.somafm.com/lush-128-mp3"),
    "house": ("Beat Blender — deep house & downtempo", "https://ice.somafm.com/beatblender-128-mp3"),
    "indie": ("Indie Pop Rocks!", "https://ice.somafm.com/indiepop-128-mp3"),
    "pop": ("PopTron — electro-pop & indie dance", "https://ice.somafm.com/poptron-128-mp3"),
    "lounge": ("Secret Agent — spy lounge", "https://ice.somafm.com/secretagent-128-mp3"),
    "70s": ("Left Coast 70s — mellow album rock", "https://ice.somafm.com/seventies-128-mp3"),
    "metal": ("Metal Detector — all things metal", "https://ice.somafm.com/bagel-128-mp3"),
    "antenne": ("ANTENNE BAYERN — German pop & hits", "https://s1-webradio.antenne.de/antenne"),
}


class PlayFlags(Flags):
    """Flags for the music commands."""

    source: Literal["yt", "sp", "sc", "am"] = flag(
        name="source", description="What source to search for your query.", aliases=["s"], default="yt"
    )
    force: bool = store_true(name="force", description="Whether to force play the track/playlist.", aliases=["f"])
    recommendations: bool = store_true(
        name="recommendations",
        short="r",
        description="Whether to auto-fill the queue with recommended tracks if the queue is empty.",
    )


class VolumeConverter(commands.Converter[int]):
    VOLUME_REGEX: ClassVar[re.Pattern] = re.compile(r"^[+-]?\d+$")

    async def convert(self, ctx: Context, argument: str) -> int:
        player: Player = cast("Player", ctx.voice_client)

        if not (match := self.VOLUME_REGEX.match(argument)):
            raise commands.BadArgument(
                "Invalid Volume provided.\n"
                "Please provide a valid number between **0-100** or a relative number, e.g. **+10** or **-15**."
            )

        if match.group().startswith(("+", "-")):
            return player.volume + int(match.group())
        return int(match.group())


class Music(Cog):
    """Commands for playing music in a voice channel."""

    emoji = "<:music:1322338453937193000>"

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        # Guards the one-shot session restore (node-ready can fire multiple times).
        self._restored: bool = False
        self.persist_sessions.start()
        # The node is already connected by the time this cog loads (setup() checks
        # Pool.get_node()), so on_wavelink_node_ready has already fired and won't
        # re-fire. Kick off restore immediately.
        self._restored = True
        self.bot.loop.create_task(self._restore_sessions())

    async def cog_load(self) -> None:
        await self.bot.wait_until_ready()

        self.playlist_tools: PlaylistTools | None = self.bot.get_cog("PlaylistTools")  # type: ignore

    async def cog_unload(self) -> None:
        self.persist_sessions.cancel()
        # Persist final state and destroy players on the Lavalink node so it
        # doesn't keep streaming audio after the bot process exits. We call
        # _destroy() directly (not disconnect()) because discord.py will handle
        # the voice state change during close(), and we need the Lavalink-side
        # teardown to happen while the websocket is still open.
        for guild in list(self.bot.guilds):
            player = guild.voice_client
            if isinstance(player, Player) and player.connected:
                with suppress(Exception):
                    await player.persist()
                with suppress(Exception):
                    await player._destroy()

    @tasks.loop(seconds=20)
    async def persist_sessions(self) -> None:
        """Periodically snapshot active players so a crash/restart can resume them.

        Runs cheaply: only touches guilds that currently have a connected player.
        """
        for guild in list(self.bot.guilds):
            player = guild.voice_client
            if isinstance(player, Player) and player.connected and player.current is not None:
                await player.persist()

    @persist_sessions.before_loop
    async def _before_persist(self) -> None:
        await self.bot.wait_until_ready()

    async def _purge_music_channels(self) -> None:
        """Delete non-pinned messages that accumulated in music panel channels while offline."""
        rows = await self.bot.db.fetch(
            "SELECT id, music_panel_channel_id, music_panel_message_id FROM guild_config "
            "WHERE music_panel_channel_id IS NOT NULL AND music_panel_message_id IS NOT NULL"
        )
        for row in rows:
            channel = self.bot.get_channel(row["music_panel_channel_id"])
            if channel is None or not isinstance(channel, discord.TextChannel):
                continue
            panel_id = row["music_panel_message_id"]
            try:
                await channel.purge(
                    limit=50,
                    check=lambda msg: not msg.pinned and msg.id != panel_id,
                )
            except discord.HTTPException:
                pass

    async def _restore_sessions(self) -> None:
        """Reconnect and resume every persisted player after a (re)start."""
        await self.bot.wait_until_ready()
        await self._purge_music_channels()
        try:
            records = await self.bot.db.music_sessions.get_all_sessions()
        except Exception as exc:
            log.error("Failed to load persisted music sessions: %s", exc)
            return
        log.info("Restoring %d persisted music session(s).", len(records))
        for record in records:
            guild = self.bot.get_guild(record["guild_id"])
            if guild is None:
                continue

            vc = guild.voice_client
            if isinstance(vc, Player):
                # wavelink already resumed this session — just re-apply our metadata.
                try:
                    await vc.hydrate(record)
                except Exception as exc:
                    log.error("Failed to hydrate resumed session for guild %s: %s", record["guild_id"], exc)
                continue
            if vc is not None:
                continue  # some other/unknown voice client; leave it alone

            try:
                await Player.restore(self.bot, record)
            except Exception as exc:
                log.error("Failed to restore music session for guild %s: %s", record["guild_id"], exc)
            await asyncio.sleep(1)  # stagger reconnects to avoid a thundering herd

    async def cog_before_invoke(self, ctx: Context) -> None:
        playlist_tools: PlaylistTools | None = self.bot.get_cog("PlaylistTools")  # type: ignore
        if playlist_tools:
            await playlist_tools.initizalize_user(ctx.author)

    @Cog.listener()
    async def on_wavelink_websocket_closed(self, payload: wavelink.WebsocketClosedEventPayload) -> None:
        """Handle a closed voice websocket.

        We deliberately do **not** tear the player down here. Benign codes (normal close,
        session invalid, disconnected) need no action, and for everything else the node's
        ``resume_timeout`` lets Lavalink recover the session without cutting off audio.
        """
        if payload.code.value in (1000, 4006, 4014):
            return
        log.warning(
            "Voice websocket closed: code=%s reason=%r by_remote=%s",
            payload.code, getattr(payload, "reason", None), getattr(payload, "by_remote", None),
        )

    @Cog.listener()
    async def on_wavelink_track_exception(self, payload: wavelink.TrackExceptionEventPayload) -> None:
        await self._recover_player(payload, reason=f"track exception: {getattr(payload, 'exception', None)}")

    @Cog.listener()
    async def on_wavelink_track_stuck(self, payload: wavelink.TrackStuckEventPayload) -> None:
        await self._recover_player(payload, reason="track stuck (no audio data received)")

    @Cog.listener()
    async def on_wavelink_extra_event(self, payload: wavelink.ExtraEventPayload) -> None:
        log.debug("Wavelink extra event: %s", getattr(payload, "data", payload))

    async def _recover_player(
        self,
        payload: wavelink.TrackExceptionEventPayload | wavelink.TrackStuckEventPayload,
        *,
        reason: str,
    ) -> None:
        """Recover from a track failure without dropping the whole session.

        Instead of disconnecting on every error (which is what caused playback to "cut
        off"), we advance to the next track. Only after repeated, consecutive failures do
        we give up — and for 24/7 players we refill rather than leave.
        """
        player: Player | None = cast("Player", payload.player)
        if not player:
            return

        guild_id = player.guild.id if player.guild else None
        log.warning("Music recovery in guild %s — %s", guild_id, reason)

        player._consecutive_errors += 1
        if player._consecutive_errors > MAX_CONSECUTIVE_ERRORS:
            log.error("Too many consecutive playback errors in guild %s; aborting current source.", guild_id)
            if player.panel is not MISSING:
                with suppress(discord.HTTPException):
                    await player.panel.channel.send(
                        f"{Emojis.error} Too many playback errors in a row — I had to stop the current source."
                    )
            player._consecutive_errors = 0
            if player.always_on:
                await player.refill_always_on()
            else:
                await player.disconnect()
            return

        try:
            if not player.queue.is_empty:
                await player.skip(force=True)
            elif player.always_on:
                await player.refill_always_on()
            elif player.autoplay != wavelink.AutoPlayMode.enabled and player.panel is not MISSING:
                with suppress(discord.HTTPException):
                    await player.panel.channel.send(
                        f"{Emojis.warning} That track failed to play and the queue is empty.", delete_after=15
                    )
        except Exception as exc:
            log.debug("Error while recovering player in guild %s: %s", guild_id, exc)

    @Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        log.info("Wavelink Node connected: %s | Resumed: %s", payload.node.uri, payload.resumed)
        if not self._restored:
            self._restored = True
            self.bot.loop.create_task(self._restore_sessions())

    @Cog.listener()
    async def on_wavelink_inactive_player(self, player: Player) -> None:
        if not player:
            return

        # 24/7 players never time out — keep the session alive and refill if needed.
        if player.always_on:
            if player.connected and not player.playing:
                await player.refill_always_on()
            return

        with suppress(discord.HTTPException):
            await player.channel.send(f"The player has been inactive for `{player.inactive_timeout}` seconds. *Goodbye!*")

        if player.connected:
            await player.disconnect()

    @Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        player: Player | None = cast("Player", payload.player)
        if not player:
            return

        if player.queue.listen_together is not MISSING:
            guild = player.guild
            assert guild is not None
            member = await self.bot.get_or_fetch_member(guild, player.queue.listen_together)
            if (
                member is None
                or (activity := next((a for a in member.activities if isinstance(a, discord.Spotify)), None)) is None
            ):
                await player.disconnect()
                return

            try:
                track = await player.search(activity.track_url)
            except Exception as exc:
                log.debug("Error while searching for track: %s", exc)
                await player.panel.channel.send("I couldn't find the track you were listening to on Spotify.")
                await player.disconnect()
                return

            if not isinstance(track, (wavelink.Playable, wavelink.Playlist)):
                await player.disconnect()
                return

            player.queue.reset()
            await player.queue.put_wait(track)
            await player.play(player.queue.get())
            await player.send_track_add(track)
            return

        if player.autoplay != wavelink.AutoPlayMode.enabled and player.queue.is_empty:
            # A 24/7 player must never leave — refill from its configured source instead.
            if player.always_on:
                await player.refill_always_on()
                return
            # we gracefully disconnect if there are no tracks left
            # in the queue and autoplay is disabled/partial enabled
            await player.disconnect()
            return

        # This is a custom shuffle to preserve
        # insert order of the tracks to the queue
        # This only plays random tracks by indexing tracks
        # with random numbers in the queue.

        # This makes it possible for the user to turn of shuffle and still have
        # the original insert order of tracks in the queue.
        if player.queue.shuffle is ShuffleMode.on:
            queue = player.queue.all
            next_random_track = queue[random.randint(0, len(queue) - 1)]

            # Add all tracks that are before the next random track to the history
            assert player.queue.history is not None
            player.queue.history.clear()
            player.queue.history.put(queue[: queue.index(next_random_track)])

            # Add all tracks that are after the next random track to the queue
            player.queue.clear()
            await player.queue.put_wait(queue[queue.index(next_random_track) :])

    @Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload) -> None:
        player: Player | None = cast("Player", payload.player)
        if not player:
            return

        # A track started cleanly — clear the failure streak used by _recover_player.
        player._consecutive_errors = 0

        if player.current is not None and player.current.recommended:
            assert player.queue.history is not None
            current = player.current
            assert current is not None
            player.queue.history.put(current)

        # Wait (bounded) until the current track is registered in the queue. The cap
        # prevents the handler from hanging forever — which would stop the panel from
        # ever rendering — if a track never lands in the queue (e.g. some stream cases).
        waited = 0.0
        while (not player.queue.all or player.current not in player.queue.all) and waited < 10:
            await asyncio.sleep(0.5)
            waited += 0.5

        channel = player.channel
        if isinstance(channel, discord.StageChannel):
            assert player.current is not None
            intance = channel.instance or await channel.fetch_instance()
            if not intance:
                await channel.create_instance(topic=player.current.title)
            else:
                await intance.edit(topic=player.current.title)

        if player.panel is not MISSING:
            await player.panel.update()
        # Snapshot the new now-playing state so a restart resumes at the right track.
        await player.persist()

    @staticmethod
    def _get_spotify_activity(member: discord.Member) -> discord.Spotify | None:
        return next((a for a in member.activities if isinstance(a, discord.Spotify)), None)

    @Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member) -> None:
        await self.bot.wait_until_ready()
        player: Player | None = cast("Player", before.guild.voice_client)
        if not player:
            return

        user_id = player.queue.listen_together
        if user_id is MISSING or (user_id and before.id != user_id):
            return

        before_activity = self._get_spotify_activity(before)
        after_activity = self._get_spotify_activity(after)

        if before_activity and after_activity:
            if before_activity.title == after_activity.title:
                now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
                start = after_activity.start.replace(tzinfo=None)

                position = round((now - start).total_seconds()) * 1000

                await player.seek(position)
                await player.panel.update()
            else:
                new_activity = self._get_spotify_activity(after)
                if new_activity and new_activity.title == before_activity.title:
                    await player.pause(False)
                else:
                    player.queue.reset()

                    if new_activity is None:
                        await player.disconnect()
                        return

                    try:
                        track = await player.search(new_activity.track_url)
                    except Exception as exc:
                        log.debug("Error while searching for track: %s", exc)
                        await player.panel.channel.send(
                            f"{Emojis.error} I couldn't find the track <@{user_id}> was listening to on spotify.",
                            delete_after=10,
                        )
                        await player.disconnect()
                        return

                    if not isinstance(track, (wavelink.Playable, wavelink.Playlist)):
                        await player.disconnect()
                        return

                    await player.queue.put_wait(track)
                    await player.send_track_add(track)
                    await player.play(player.queue.get())

                    position = (
                        round(
                            (
                                datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
                                - new_activity.start.replace(tzinfo=None)
                            ).total_seconds()
                        )
                        * 1000
                    )
                    await player.seek(position)
        else:
            await player.panel.channel.send("The host has stopped listening to Spotify.")
            await player.disconnect()

    @command(description="Adds a track/playlist to the queue.", guild_only=True, hybrid=True, bot_permissions=["connect", "speak"])
    @describe(query="The track/playlist to add to the queue. Can be a URL or a search query.")
    @app_commands.choices(
        source=[
            app_commands.Choice(name="SoundCloud (Default)", value="sc"),
            app_commands.Choice(name="Spotify", value="sp"),
            app_commands.Choice(name="Apple Music", value="am"),
        ],
    )
    @checks.is_author_connected()
    @checks.is_listen_together()
    async def play(self, ctx: Context, *, query: str, flags: PlayFlags) -> None:
        """Play Music in a voice channel by searching for a track/playlist."""
        player: Player = cast("Player", ctx.voice_client)
        if player and player.always_on:
            await ctx.send_error(
                f"A 24/7 session is currently active. "
                f"Disable it first with `{ctx.clean_prefix}music 247 off` before playing new tracks."
            )
            return

        await ctx.defer()

        if not player:
            player = await Player.join(ctx)

        # Note: Due to Discords ToS, we can't use YouTube as our source of music
        SOURCE_LOOKUP = {
            # 'yt': wavelink.TrackSource.YouTubeMusic,
            "sp": "spsearch",
            "sc": wavelink.TrackSource.SoundCloud,
            "am": "amsearch",  # Apple Music (LavaSrc)
        }
        source = SOURCE_LOOKUP.get(flags.source, wavelink.TrackSource.SoundCloud)

        player.autoplay = wavelink.AutoPlayMode.enabled if flags.recommendations else wavelink.AutoPlayMode.partial

        if not query:
            await ctx.send_error("Please provide a search query.")
            return

        result = await player.search(query, source=source, ctx=ctx, return_first=not hasattr(flags, "__with_search__"))

        if isinstance(result, SearchReturn):
            if result == SearchReturn.NO_RESULTS:
                await ctx.send_error("Sorry! No results found matching your query.")
            elif result == SearchReturn.NO_YOUTUBE_ALLOWED:
                await ctx.send_error("Sorry, you can't play YouTube tracks from this bot.")
            elif result == SearchReturn.AMAZON_UNSUPPORTED:
                await ctx.send_error(
                    "Amazon Music isn't supported \N{EM DASH} there's no streaming source for it.\n"
                    "Try Spotify, Apple Music, or SoundCloud instead."
                )
            return

        if isinstance(result, wavelink.Playlist):
            await player.queue.put_wait(result)
        else:
            if flags.force:
                player.queue.put_at(0, result)
            else:
                await player.queue.put_wait(result)

        short: bool = True
        if player.playing and flags.force:
            await player.skip()
        elif not player.playing:
            await player.play(player.queue.get(), volume=70)
        else:
            short = False
            await player.panel.update()
        await player.send_track_add(result, ctx, short=short)

    @command(
        description="Adds a track/playlist to the queue by choosing from a set of examples.",
        guild_only=True, hybrid=True, bot_permissions=["connect", "speak"],
    )
    @describe(query="The track/playlist to add to the queue. Can be a URL or a search query.")
    @app_commands.choices(
        source=[
            app_commands.Choice(name="YouTube (Default)", value="yt"),
            app_commands.Choice(name="Spotify", value="sp"),
            app_commands.Choice(name="SoundCloud", value="sc"),
        ]
    )
    @checks.is_author_connected()
    @checks.is_listen_together()
    async def playsearch(self, ctx: Context, *, query: str, flags: PlayFlags) -> None:
        """Adds a track/playlist to the queue by choosing from a variety of examples."""
        setattr(flags, "__with_search__", True)
        await ctx.invoke(self.play, query=query, flags=flags)  # type: ignore

    @group(
        "listen-together",
        fallback="start",
        description="Start a listen-together activity with a user.",
        guild_only=True,
        hybrid=True,
        bot_permissions=["connect", "speak"],
    )
    @describe(member="The user you want to start a listen-together activity with.")
    @checks.is_author_connected()
    async def listen_together(self, ctx: Context, member: discord.Member) -> None:
        """Start a listen-together activity with an user.
        `Note:` Only supported for Spotify Music."""
        await ctx.defer()

        assert ctx.guild is not None
        if not ctx.guild.voice_client:
            await Player.join(ctx)

        player: Player = cast("Player", ctx.guild.voice_client)
        if not player:
            return

        # We need to fetch the member to get the current activity
        guild = ctx.guild
        assert guild is not None
        fetched_member = await self.bot.get_or_fetch_member(guild, member.id)
        if fetched_member is None:
            await ctx.send_error(f"**{member.display_name}** could not be found.")
            return
        member = fetched_member

        if not (activity := next((a for a in member.activities if isinstance(a, discord.Spotify)), None)):
            await ctx.send_error(f"**{member.display_name}** is not currently listening to Spotify.")
            return

        if player.playing or player.queue.listen_together is not MISSING:
            player.queue.reset()
            await player.stop()

        player.autoplay = wavelink.AutoPlayMode.disabled

        try:
            track = await player.search(activity.track_url)
        except Exception as exc:
            log.debug("Error while searching for track: %s", exc)
            await ctx.send_error(f"I couldn't find the track <@{member.id}> was listening to on Spotify.")
            await player.disconnect()
            return

        if not isinstance(track, (wavelink.Playable, wavelink.Playlist)):
            await ctx.send_error("Sorry! No results found matching your query.")
            return

        await player.queue.put_wait(track)
        player.queue.listen_together = member.id
        await player.play(player.queue.get())

        poss = (
            round(
                (
                    datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - activity.start.replace(tzinfo=None)
                ).total_seconds()
            )
            * 1000
        )
        await player.seek(poss)

        await player.send_track_add(track, ctx)
        await player.panel.update()

    @listen_together.command(name="stop", description="Stops the current listen-together activity.")
    async def listen_together_stop(self, ctx: Context) -> None:
        """Stops the current listen-together activity."""
        assert ctx.guild is not None
        player: Player = cast("Player", ctx.guild.voice_client)
        if not player:
            return

        if player.queue.listen_together is MISSING:
            await ctx.send_error("There is no listen-together activity to stop.")
            return

        await player.disconnect()
        await ctx.send_success(f"{Emojis.success} Stopped the current listen-together activity.", delete_after=10)

    @command("connect", description="Connect me to a voice-channel.", hybrid=True, guild_only=True, bot_permissions=["connect", "speak"])
    @describe(channel="The Voice/Stage-Channel you want to connect to.")
    async def connect(self, ctx: Context, channel: discord.VoiceChannel | discord.StageChannel | None = None) -> None:
        """Connect me to a voice-channel."""
        if ctx.voice_client:
            await ctx.send_error("I am already connected to a voice channel. Please disconnect me first.")
            return

        try:
            channel = channel or ctx.author.voice.channel
        except AttributeError:
            await ctx.send_error("No voice channel to connect to. Please either provide one or join one.")
            return

        await Player.join(ctx)
        await ctx.send_success(f"Connected and bound to {channel.mention}", delete_after=10)

    @command(description="Disconnect me from a voice-channel.", hybrid=True, guild_only=True)
    @checks.is_author_connected()
    @checks.is_player_connected()
    async def leave(self, ctx: Context) -> None:
        """Disconnect me from a voice-channel."""
        player: Player = cast("Player", ctx.voice_client)
        if not player:
            return

        await player.disconnect()
        await ctx.send_success("Disconnected Channel and cleaned up the queue.", delete_after=10)

    @command("stop", description="Clears the queue and stop the current plugins.", hybrid=True, guild_only=True)
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def stop(self, ctx: Context) -> None:
        """Clears the queue and stop the current plugins."""
        player: Player = cast("Player", ctx.voice_client)
        if not player:
            return

        await player.disconnect()
        await ctx.send_success("Stopped Track and cleaned up queue.", delete_after=10)

    @command(
        "toggle", aliases=["pause", "resume"], description="Pause/Resume the current track.", guild_only=True, hybrid=True
    )
    @checks.is_author_connected()
    @checks.is_listen_together()
    async def pause_or_resume(self, ctx: Context) -> None:
        """Pause the current playing track."""
        player: Player = cast("Player", ctx.voice_client)
        if not player:
            return

        await player.pause(not player.paused)
        assert player.current is not None
        await ctx.send_success(
            f"{'Paused' if player.paused else 'Resumed'} Track [{player.current.title}]({player.current.uri})",
            delete_after=10,
            suppress_embeds=True,
        )
        await player.panel.update()

    @command(description="Sets a loop mode for the plugins.", hybrid=True, guild_only=True)
    @describe(mode="Select a loop mode.")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Normal", value="normal"),
            app_commands.Choice(name="Track", value="track"),
            app_commands.Choice(name="Queue", value="queue"),
        ]
    )
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def loop(self, ctx: Context, mode: Literal["normal", "track", "queue"]) -> None:
        """Sets a loop mode for the plugins."""
        player: Player = cast("Player", ctx.voice_client)
        if not player:
            return

        from wavelink import QueueMode as _QueueMode

        player.queue.mode = {"normal": _QueueMode.normal, "track": _QueueMode.loop, "queue": _QueueMode.loop_all}.get(
            mode, _QueueMode.normal
        )

        await player.panel.update()
        await ctx.send_success(f"Loop Mode changed to `{mode}`", delete_after=10)

    @command(description="Sets the shuffle mode for the plugins.", hybrid=True, guild_only=True)
    @describe(mode="Select a shuffle mode.")
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def shuffle(self, ctx: Context, mode: bool) -> None:
        """Sets the shuffle mode for the plugins."""
        player: Player = cast("Player", ctx.voice_client)
        if not player:
            return

        player.queue.shuffle = ShuffleMode.on if mode else ShuffleMode.off
        await player.panel.update()
        await ctx.send_success(f"Shuffle Mode changed to `{mode}`", delete_after=10)

    @command(description="Seek to a specific position in the tack.", hybrid=True, guild_only=True)
    @describe(position="The position to seek to. (Format: H:M:S or S)")
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def seek(self, ctx: Context, position: str | None = None) -> None:
        """Seek to a specific position in the tack."""
        player: Player = cast("Player", ctx.voice_client)
        if not player:
            return

        assert player.current is not None
        if player.current.is_stream:
            await ctx.send_error("Cannot seek if track is a stream.")
            return

        if position is None:
            seconds = 0
            await player.seek(seconds)
        else:
            try:
                seconds = sum(int(x) * 60**i for i, x in enumerate(reversed(position.split(":"))))
            except ValueError:
                await ctx.send_error("Please provide a valid timestamp format. (e.g. 3:20, 23)", ephemeral=True)
                return

            seconds *= 1000  # Convert to milliseconds
            if seconds in range(player.current.length):
                await player.seek(seconds)
            else:
                await ctx.send_error(
                    "Please provide a seek time within the range of the track.", ephemeral=True, delete_after=10
                )
                return

        await ctx.send_success(f"Seeked to position `{convert_duration(seconds)}`", delete_after=10)
        await player.panel.update()

    @seek.autocomplete("position")
    async def seek_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[Any] | list[Choice[str | int | float]]:
        assert interaction.guild is not None
        player: Player = cast("Player", interaction.guild.voice_client)
        if not player:
            return []

        def _timestamp(secs: int) -> str:
            return datetime.datetime.fromtimestamp(secs, datetime.UTC).strftime("%H:%M:%S")

        try:
            seconds = sum(int(x.strip('""')) * 60**inT for inT, x in enumerate(reversed(current.split(":"))))
        except ValueError:
            # Return a list of 3 choice timestamps -> track length, 1/3, 2/3
            assert player.current is not None
            length = player.current.length / 1000  # Convert to seconds
            return [
                app_commands.Choice(name=_timestamp(int(length / 3)), value=str(int(length / 3))),
                app_commands.Choice(name=_timestamp(int(length / 3 * 2)), value=str(int(length / 3 * 2))),
            ]

        timestamp = _timestamp(seconds)
        return [app_commands.Choice(name=timestamp, value=timestamp)]

    @command(description="Set the volume for the plugins.", hybrid=True, guild_only=True)
    @describe(amount="The volume to set the plugins to. (0-100)")
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def volume(self, ctx: Context, amount: Annotated[int, VolumeConverter] | None = None) -> None:
        """Set the volume for the plugins."""
        player: Player = cast("Player", ctx.voice_client)
        if not player:
            return

        if amount is None:
            embed = discord.Embed(title="Current Volume", color=helpers.Colour.white())
            embed.add_field(
                name="Volume:", value=f"```swift\n{ProgressBar(0, 100, player.volume)} [ {player.volume}% ]```", inline=False
            )
            await ctx.send(embed=embed, delete_after=15)
            return

        await player.set_volume(amount)
        await player.panel.update()

        embed = discord.Embed(
            title="Changed Volume",
            color=helpers.Colour.white(),
            description="*It may takes a while for the changes to apply.*",
        )
        embed.add_field(
            name="Volume:", value=f"```swift\n{ProgressBar(0, 100, player.volume)} [ {player.volume}% ]```", inline=False
        )
        await ctx.send(embed=embed, delete_after=15)

    @command(description="Removes all songs from users that are not in the voice channel.", hybrid=True, guild_only=True)
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def cleanupleft(self, ctx: Context) -> None:
        """Removes all songs from users that are not in the voice channel."""
        player: Player = cast("Player", ctx.voice_client)
        if not player:
            return

        await player.cleanupleft()
        await player.panel.update()
        await ctx.send_success("Cleaned up the queue.", delete_after=10)

    @group(description="Manage Advanced Filters to specify you listening experience.", guild_only=True, hybrid=True)
    @checks.is_player_playing()
    async def filter(self, ctx: Context) -> None:
        """Display all active filters and the current equalizer, or use a subcommand to modify them."""
        if ctx.invoked_subcommand is not None:
            return

        player: Player = cast("Player", ctx.voice_client)
        if not player:
            return

        await ctx.defer()

        filters: wavelink.Filters = player.filters
        eq_gains = [entry["gain"] for entry in filters.equalizer.payload.values()]

        active: list[str] = []

        # Timescale (nightcore etc.)
        ts = filters.timescale.payload
        if ts:
            parts = [f"{k}={v}" for k, v in ts.items()]
            active.append(f"**Timescale** — {', '.join(parts)}")

        # Rotation (8D)
        rot = filters.rotation.payload
        if rot:
            hz = rot.get("rotationHz", 0)
            active.append(f"**Rotation (8D)** — {hz} Hz")

        # Low Pass
        lp = filters.low_pass.payload
        if lp:
            smoothing = lp.get("smoothing", 0)
            active.append(f"**Low Pass** — smoothing: {smoothing}")

        # Tremolo
        trem = filters.tremolo.payload
        if trem:
            parts = [f"{k}={v}" for k, v in trem.items()]
            active.append(f"**Tremolo** — {', '.join(parts)}")

        # Vibrato
        vib = filters.vibrato.payload
        if vib:
            parts = [f"{k}={v}" for k, v in vib.items()]
            active.append(f"**Vibrato** — {', '.join(parts)}")

        # Karaoke
        kar = filters.karaoke.payload
        if kar:
            parts = [f"{k}={v}" for k, v in kar.items()]
            active.append(f"**Karaoke** — {', '.join(parts)}")

        # Distortion
        dist = filters.distortion.payload
        if dist:
            active.append(f"**Distortion** — {len(dist)} parameter(s) set")

        # Channel Mix
        cm = filters.channel_mix.payload
        if cm:
            parts = [f"{k}={v}" for k, v in cm.items()]
            active.append(f"**Channel Mix** — {', '.join(parts)}")

        # Equalizer (non-flat = active)
        eq_active = any(g != 0.0 for g in eq_gains)
        if eq_active:
            active.append("**Equalizer** — custom (see chart below)")

        embed = discord.Embed(
            title="Active Audio Filters",
            color=helpers.Colour.white(),
        )

        if active:
            embed.description = "\n".join(f"• {entry}" for entry in active)
        else:
            embed.description = "*No filters are currently active.* All audio is playing at default settings."

        embed.set_footer(text=f"Use \"{ctx.clean_prefix}filter <subcommand>\" to modify filters.")

        image = await self.bot.render.equalizer(eq_gains)
        embed.set_image(url="attachment://image.png")
        await ctx.send(embed=embed, file=image)

    @filter.command("equalizer", description="Set the equalizer for the current Track.")
    @describe(band="The Band you want to change. (1-15)", gain="The Gain you want to set. (-0.25-+1.0)")
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def filter_equalizer(
        self,
        ctx: Context,
        band: app_commands.Range[int, 1, 15] | None = None,
        gain: app_commands.Range[float, -0.25, +1.0] | None = None,
    ) -> None:
        """Set a custom Equalizer for the current Track.

        Notes
        -----
        The preset paremeter will be given priority, if provided.
        """
        player: Player = cast("Player", ctx.voice_client)
        if not player:
            return

        await ctx.defer()

        filters: wavelink.Filters = player.filters
        if not band or not gain:
            await ctx.send_error("Please provide a valid Band and Gain or a Preset.")
            return

        band -= 1

        eq = filters.equalizer.payload
        eq[band]["gain"] = gain
        filters.equalizer.set(bands=list(eq.values()))
        await player.set_filters(filters)

        embed = discord.Embed(
            title="Changed Filter",
            color=helpers.Colour.white(),
            description="*It may takes a while for the changes to apply.*",
        )
        image = await self.bot.render.equalizer([entry["gain"] for entry in filters.equalizer.payload.values()])
        embed.set_image(url="attachment://image.png")
        embed.set_footer(text=f"Requested by: {ctx.author}")
        await ctx.send(embed=embed, file=image, delete_after=20)

    @filter.command("bassboost", description="Enable/Disable the bassboost filter.")
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def filter_bassboost(self, ctx: Context) -> None:
        """Apply a bassboost filter for the current track."""
        player: Player = cast("Player", ctx.voice_client)
        if not player:
            return

        await ctx.defer()

        filters: wavelink.Filters = player.filters
        filters.equalizer.set(
            bands=[  # type: ignore
                {"band": 0, "gain": 0.2},
                {"band": 1, "gain": 0.15},
                {"band": 2, "gain": 0.1},
                {"band": 3, "gain": 0.05},
                {"band": 4, "gain": 0.0},
                {"band": 5, "gain": -0.05},
                {"band": 6, "gain": -0.1},
                {"band": 7, "gain": -0.1},
                {"band": 8, "gain": -0.1},
                {"band": 9, "gain": -0.1},
                {"band": 10, "gain": -0.1},
                {"band": 11, "gain": -0.1},
                {"band": 12, "gain": -0.1},
                {"band": 13, "gain": -0.1},
                {"band": 14, "gain": -0.1},
            ]
        )
        await player.set_filters(filters)

        embed = discord.Embed(
            title="Changed Filter",
            color=helpers.Colour.white(),
            description="*It may takes a while for the changes to apply.*",
        )
        image = await self.bot.render.equalizer([entry["gain"] for entry in filters.equalizer.payload.values()])
        embed.set_image(url="attachment://image.png")
        embed.set_footer(text=f"Requested by: {ctx.author}")
        await ctx.send(embed=embed, file=image, delete_after=20)

    @filter.command(name="nightcore", description="Enables/Disables the nightcore filter.")
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def filter_nightcore(self, ctx: Context) -> None:
        """Apply a Nightcore Filter to the current track."""
        player: Player = cast("Player", ctx.voice_client)
        if not player:
            return

        filters: wavelink.Filters = player.filters
        filters.timescale.set(speed=1.25, pitch=1.3, rate=1.3)
        await player.set_filters(filters)

        await ctx.send_success("Applied Nightcore Filter.", delete_after=10)

    @filter.command("8d", description="Enable/Disable the 8d filter.")
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def filter_8d(self, ctx: Context) -> None:
        """Apply an 8D Filter to create a 3D effect."""
        player: Player = cast("Player", ctx.voice_client)
        if not player:
            return

        filters: wavelink.Filters = player.filters
        filters.rotation.set(rotation_hz=0.15)
        await player.set_filters(filters)

        await ctx.send_success("Applied 8D Filter.", delete_after=10)

    @filter.command("lowpass", description="Suppresses higher frequencies while allowing lower frequencies to pass through.")
    @describe(smoothing="The smoothing of the lowpass filter. (2.5-50.0)")
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def filter_lowpass(self, ctx: Context, smoothing: app_commands.Range[float, 2.5, 50.0]) -> None:
        """Apply a Lowpass Filter to the current Track."""
        player: Player = cast("Player", ctx.voice_client)
        if not player:
            return

        filters: wavelink.Filters = player.filters
        filters.low_pass.set(smoothing=smoothing)
        await player.set_filters(filters)

        await ctx.send_success(f"Set Lowpass Filter to **{smoothing}**.", delete_after=10)

    @filter.command("reset", description="Reset all active filters.")
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def filter_reset(self, ctx: Context) -> None:
        """Reset all active filters."""
        player: Player = cast("Player", ctx.voice_client)
        if not player:
            return

        player.filters.reset()
        await player.set_filters()
        await ctx.send_success("Removed all active filters.", delete_after=10)

    @command(description="Skip the playing song to the next.", hybrid=True, guild_only=True)
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def forceskip(self, ctx: Context) -> None:
        """Skip the playing song."""
        player: Player = cast("Player", ctx.voice_client)
        if not player:
            return

        if player.queue.is_empty:
            await ctx.send_error("The queue is empty.")
            return

        await player.skip(force=True)
        await ctx.send_success("An admin or DJ has to the next track.", delete_after=10)

    @command("jump-to", description="Jump to a track in the Queue.", hybrid=True, guild_only=True)
    @describe(position="The index of the track you want to jump to.")
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def jump_to(self, ctx: Context, position: int) -> None:
        """Jump to a track in the Queue.
        Note: The number you enter is the count of how many tracks in the queue will be skipped."""
        player: Player = cast("Player", ctx.voice_client)
        if not player:
            return

        if player.queue.all_is_empty:
            await ctx.send_error("The queue is empty.")
            return

        if position < 0:
            await ctx.send_error("The index must be greater than or 0.")
            return

        if (position - 1) > len(player.queue.all):
            await ctx.send_error("There are not that many tracks in the queue.")
            return

        success = await player.jump_to(position - 1)
        if not success:
            await ctx.send_error("Failed to jump to the specified track.")
            return

        await player.stop()

        if position != 1:
            await ctx.send_success(f"Playing the **{position}** track in queue.", delete_after=10)
        else:
            await ctx.send_success("Playing the next track in queue.", delete_after=10)

    @command(description="Plays the previous Track.", hybrid=True, guild_only=True)
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def back(self, ctx: Context) -> None:
        """Plays the previous Track."""
        player: Player = cast("Player", ctx.voice_client)
        if not player:
            return

        assert player.queue.history is not None
        if player.queue.history.is_empty:
            await ctx.send_error("There are no tracks in the history.")
            return

        await player.back()
        await ctx.send_success("An admin or DJ has skipped to the previous song.", delete_after=10)

    @command(description="Display the active queue.", hybrid=True, guild_only=True)
    async def queue(self, ctx: Context) -> None:
        """Display the active queue."""
        assert ctx.guild is not None
        player: Player = cast("Player", ctx.voice_client)
        if not player:
            return

        if player.queue.all_is_empty:
            await ctx.send_error("No items currently in the queue.", ephemeral=True)
            return

        await ctx.defer()

        class QueuePaginator(BasePaginator):
            @staticmethod
            def fmt(track: wavelink.Playable, index: int) -> str:
                return (
                    f"`[ {index}. ]` [{track.title}]({track.uri}) by **{track.author or 'Unknown'}** "
                    f"[`{convert_duration(track.length) if not track.is_stream else 'LIVE'}`]"
                )

            async def format_page(self, entries: list, /) -> discord.Embed:
                assert ctx.guild is not None
                assert player.current is not None
                assert player.queue.history is not None
                embed = discord.Embed(color=helpers.Colour.white())
                icon_url = ctx.guild.icon.url if ctx.guild.icon else None
                embed.set_author(name=f"{ctx.guild.name}'s Current Queue", icon_url=icon_url)

                embed.description = (
                    "**╔ Now Playing:**\n"
                    f"[{player.current.title}]({player.current.uri}) by **{player.current.author or 'Unknown'}** "
                    f"[`{convert_duration(player.current.length)}`]\n\n"
                )

                tracks = (
                    "\n".join(
                        self.fmt(track, i) for i, track in enumerate(entries, (self._current_page * self.per_page) + 1)
                    )
                    if not isinstance(entries[0], str)
                    else (
                        "*It seems like there are currently no upcoming tracks.*\nAdd one with </play:1207828024037216283>."
                    )
                )

                embed.description += "**╠ Up Next:**\n" + tracks

                embed.add_field(
                    name="╚ Settings:", value=f"DJ(s): {', '.join([x.mention for x in player.djs])}", inline=False
                )
                embed.set_footer(text=f"Total: {len(player.queue.all)} • History: {len(player.queue.history) - 1}")
                return embed

        await QueuePaginator.start(ctx, entries=list(player.queue) or ["PLACEHOLDER"], per_page=30)

    # Lyrics Stuff

    @classmethod
    def _get_text(cls, element: NavigableString | Tag) -> str:
        """Recursively parse an element and its children into a markdown string."""
        if isinstance(element, NavigableString):
            return element.strip()
        elif element.name == "br":
            return "\n"
        else:
            return "".join(cls._get_text(child) for child in element.contents)  # type: ignore

    @classmethod
    def _extract_lyrics(cls, html: str) -> str | None:
        """Extract lyrics from the provided HTML."""
        soup = BeautifulSoup(html, "html.parser")

        lyrics_container = soup.find_all("div", {"data-lyrics-container": "true"})

        if not lyrics_container:
            return None

        text_parts = [cls._get_text(part) for part in lyrics_container]

        return "\n".join(text_parts)

    @command(description="Search for some lyrics.", hybrid=True, guild_only=True)
    @describe(song="The song you want to search for.")
    @commands.guild_only()
    async def lyrics(self, ctx: Context, *, song: str | None = None) -> None:
        """Search for some lyrics."""
        await ctx.defer(ephemeral=True)

        player: Player = cast("Player", ctx.voice_client)
        if not player and not song:
            await ctx.send_error("Please provide a song to search for.")
            return

        song = song or (player.current.title if player and player.current else None)
        if not song:
            await ctx.send_error("Please provide a song to search for.")
            return

        async with ctx.progress("Searching for lyrics...") as progress:
            headers = {"Accept": "application/json", "Authorization": f"Bearer {genius_key}"}

            async with self.bot.session.get(
                "https://api.genius.com/search",
                headers=headers,
                params={"q": song.replace("by", "").replace("from", "").strip()},
            ) as resp:
                if resp.status != 200:
                    await ctx.send_error(f"{Emojis.error} I cannot find lyrics for the current track.")
                    return

                data = (await resp.json())["response"]["hits"][0]["result"]
                song_url = urljoin("https://genius.com", data["path"])

            await progress.update("Fetching lyrics page...")
            async with self.bot.session.get(song_url) as res:
                if res.status != 200:
                    await ctx.send_error(f"{Emojis.error} I cannot find lyrics for the current track.")
                    return

                html = await res.text()

            await progress.update("Extracting lyrics...")
            lyrics_data = self._extract_lyrics(html)

            if lyrics_data is None:
                await ctx.send_error(f"{Emojis.error} I cannot find lyrics for the current track.")
                return

            mapped = list(pagify(lyrics_data, page_length=4096))

        class TextPaginator(BasePaginator):
            async def format_page(self, entries: list, /) -> discord.Embed:
                embed = discord.Embed(
                    title=data["full_title"], url=song_url, description=entries[0], colour=helpers.Colour.white()
                )
                embed.set_thumbnail(url=data["header_image_url"])
                return embed

        await TextPaginator.start(ctx, entries=mapped, per_page=1, ephemeral=True)

    # DJ

    @group("dj", description="Manage the DJ role.", guild_only=True, hybrid=True)
    async def _dj(self, ctx: Context) -> None:
        """Manage the DJ Role."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @_dj.command(
        "add",
        description="Adds the DJ Role with which you have extended control rights to a member.",
        bot_permissions=["manage_roles"],
        user_permissions=["manage_roles"],
    )
    @describe(member="The member you want to add the DJ Role to.")
    async def dj_add(self, ctx: Context, member: discord.Member) -> None:
        """Adds the DJ Role with which you have extended control rights to a member.

        Note: The DJ role is used to give extended control rights to a member.

        If no DJ role exists, the bot will create a new one.
        """
        assert ctx.guild is not None
        assert ctx.guild.me is not None
        assert isinstance(ctx.author, discord.Member)
        dj_role = discord.utils.get(ctx.guild.roles, name="DJ")
        if dj_role is None:
            dj_role = await ctx.guild.create_role(name="DJ")
            await ctx.send_success(f"Created new DJ Role {dj_role.mention}.")

        if dj_role in member.roles:
            await ctx.send_error(f"{member} already has the DJ role.")
            return

        if dj_role.position >= ctx.guild.me.top_role.position:
            await ctx.send_error("The DJ role is higher than my top role.")
            return

        if dj_role.position >= ctx.author.top_role.position:
            await ctx.send_error("The DJ role is higher than your top role.")
            return

        await member.add_roles(dj_role)
        await ctx.send_success(f"Added the {dj_role.mention} role to user {member}.")

    @_dj.command(
        "remove",
        description="Removes the DJ Role with which you have extended control rights from a member.",
        bot_permissions=["manage_roles"],
        user_permissions=["manage_roles"],
    )
    @describe(member="The member you want to remove the DJ Role from.")
    async def dj_remove(self, ctx: Context, member: discord.Member) -> None:
        """Removes the DJ Role with which you have extended control rights from a member."""
        assert ctx.guild is not None
        assert ctx.guild.me is not None
        assert isinstance(ctx.author, discord.Member)
        dj_role = discord.utils.get(ctx.guild.roles, name="DJ")
        if not dj_role:
            await ctx.send_error("There is currently no existing DJ role.")
            return

        if dj_role not in member.roles:
            await ctx.send_error(f"**{member}** has not the DJ role.")
            return

        if dj_role.position >= ctx.guild.me.top_role.position:
            await ctx.send_error("The DJ role is higher than my top role.")
            return

        if dj_role.position >= ctx.author.top_role.position:
            await ctx.send_error("The DJ role is higher than your top role.")
            return

        await member.remove_roles(dj_role)
        await ctx.send_success(f"Removed the {dj_role.mention} role from user {member.mention}.")

    # SETUP

    @group(
        "music",
        description="Manage the Music Configuration.",
        guild_only=True,
        hybrid=True,
        fallback="setup",
        bot_permissions=["manage_channels", "manage_messages"],
        user_permissions=["manage_channels"],
    )
    async def _music(self, ctx: Context) -> None:
        """Opens the music configuration dashboard."""
        assert ctx.guild is not None
        assert isinstance(ctx.author, discord.Member)

        config = await self.bot.db.get_guild_config(guild_id=ctx.guild.id)
        view = MusicSetupView(self.bot, ctx.author, config)
        view.message = await ctx.send(view=view)

    # -- 24/7 ("always-on") -------------------------------------------------

    async def _resolve_user_playlist(self, user_id: int, source: str) -> Playlist | None:
        """Try to resolve source as a user playlist by index or name. Returns None if not matched."""
        pt: PlaylistTools | None = self.bot.get_cog("PlaylistTools")  # type: ignore[assignment]
        if pt is None:
            return None
        playlists = await pt.get_playlists(user_id=user_id)
        if not playlists:
            return None
        try:
            playlist_id = int(source)
            for p in playlists:
                if p.id == playlist_id:
                    return p
        except ValueError:
            pass
        source_lower = source.lower()
        for p in playlists:
            if p.name.lower() == source_lower:
                return p
        return None

    async def enable_always_on(
        self,
        guild: discord.Guild,
        voice_channel: discord.VoiceChannel | discord.StageChannel,
        text_channel: discord.abc.MessageableChannel | None,
        mode: str,
        source: str,
    ) -> Player:
        """Connect (if needed) and switch a guild's player into 24/7 mode.

        Shared by the ``/music 247`` command and the dashboard internal API so both
        behave identically. Resets the queue and starts the configured endless source.
        """
        from .ui import PlayerPanel

        player = cast("Player", guild.voice_client)
        if not isinstance(player, Player):
            player = await voice_channel.connect(cls=Player, self_deaf=True)  # type: ignore[assignment]
            with suppress(discord.HTTPException):
                if guild.me is not None:
                    await guild.me.edit(deafen=True)

        # Ensure a control panel exists (also when converting an already-connected player).
        if player.panel is MISSING:
            config = await self.bot.db.get_guild_config(guild.id)
            disabled = bool(config and not config.use_music_panel)
            # Prefer the configured panel channel; otherwise fall back to where the command
            # ran. Accept any messageable guild channel (text, thread, or a voice text-chat),
            # since the panel only needs send/fetch_message.
            messageable = (discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel)
            panel_channel = config.music_panel_channel or (
                text_channel if isinstance(text_channel, messageable) else None
            )
            if panel_channel is not None:
                try:
                    player.panel = await PlayerPanel.start(player, channel=panel_channel, disabled=disabled)  # type: ignore[arg-type]
                    log.info(
                        "24/7 panel started in guild %s (channel=%s, disabled=%s)",
                        guild.id, panel_channel.id, disabled,
                    )
                except Exception as exc:
                    log.warning("Failed to start music panel for 24/7 in guild %s: %s", guild.id, exc, exc_info=exc)
            else:
                log.warning(
                    "24/7 in guild %s: no usable panel channel (configured=%s, fallback=%s) — panel not shown",
                    guild.id, config.music_panel_channel_id, type(text_channel).__name__,
                )

        player.always_on = True
        player.always_on_mode = mode
        player.always_on_source = source
        player.inactive_timeout = None  # never time out
        if mode == "autoplay":
            player.autoplay = wavelink.AutoPlayMode.enabled

        player.queue.reset()
        if player.playing:
            await player.stop()
        await player.refill_always_on()
        # Render the panel immediately so it reflects the now-playing track right away
        # (don't rely solely on the track-start event, which may race with this).
        if player.panel is not MISSING:
            with suppress(Exception):
                await player.panel.update()
        await player.persist()
        return player

    async def disable_always_on(self, guild: discord.Guild) -> bool:
        """Turn off 24/7 mode for a guild. Returns whether a player was affected."""
        player = cast("Player", guild.voice_client)
        if not isinstance(player, Player):
            await self.bot.db.music_sessions.delete_session(guild.id)
            return False

        player.always_on = False
        player.always_on_mode = None
        player.always_on_source = None
        player.inactive_timeout = 600  # restore a sane default
        await player.persist()
        return True

    @_music.command(
        "247",
        description="Set up a 24/7 always-on player that keeps playing a stream, playlist, or autoplay.",
    )
    @describe(
        mode="The kind of endless source to keep playing (or 'off' to disable).",
        source="A stream/radio URL, a playlist/album link or search, or an autoplay seed. Leave empty to turn off.",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Radio / Stream URL", value="radio"),
            app_commands.Choice(name="Playlist (looped)", value="playlist"),
            app_commands.Choice(name="Autoplay (endless recommendations)", value="autoplay"),
            app_commands.Choice(name="Off", value="off"),
        ]
    )
    async def music_247(
        self,
        ctx: Context,
        mode: Literal["radio", "playlist", "autoplay", "off"],
        *,
        source: str | None = None,
    ) -> None:
        """Configure a 24/7 always-on player.

        The player reconnects automatically after a disconnect or bot restart and keeps
        the chosen source running forever:
        - **radio** — an endless internet-radio / live-stream URL
        - **playlist** — a playlist/album link or search, looped endlessly
        - **autoplay** — seed a track and let recommendations keep it going
        """
        assert ctx.guild is not None

        if mode == "off":
            affected = await self.disable_always_on(ctx.guild)
            if affected:
                await ctx.send_success("24/7 mode disabled. The player will now time out when idle.")
            else:
                await ctx.send_error("There is no 24/7 player running in this server.")
            return

        if not source:
            await ctx.send_error("Please provide a source (a stream URL, playlist link/search, or autoplay seed).")
            return

        # Radio mode accepts a friendly preset name (e.g. "lofi") as a shortcut.
        preset_label: str | None = None
        if mode == "radio":
            preset = RADIO_PRESETS.get(source.strip().lower())
            if preset:
                preset_label, source = preset[0], preset[1]

        # Playlist mode accepts a user playlist index/name as a shortcut.
        playlist_label: str | None = None
        if mode == "playlist":
            resolved_playlist = await self._resolve_user_playlist(ctx.author.id, source)
            if resolved_playlist is not None:
                if len(resolved_playlist) == 0:
                    await ctx.send_error("That playlist is empty — add some tracks first.")
                    return
                playlist_label = resolved_playlist.name
                source = f"percy:playlist:{resolved_playlist.id}"

        channel: discord.VoiceChannel | discord.StageChannel | None = None
        if isinstance(ctx.author, discord.Member) and ctx.author.voice and ctx.author.voice.channel:
            channel = ctx.author.voice.channel
        elif ctx.voice_client is not None:
            channel = cast("Player", ctx.voice_client).channel  # type: ignore[assignment]

        if channel is None:
            await ctx.send_error("Join a voice channel first, or have me already connected to one.")
            return

        await ctx.defer()

        # User playlists are DB-backed; skip the external probe.
        if not source.startswith("percy:playlist:"):
            probe = await Player.search(source, return_first=True)
            if isinstance(probe, SearchReturn) or not probe:
                if probe == SearchReturn.AMAZON_UNSUPPORTED:
                    await ctx.send_error("Amazon Music isn't supported. Try Spotify, Apple Music, SoundCloud, or a stream URL.")
                elif mode == "radio":
                    await ctx.send_error(
                        "I couldn't load that radio source. **Radio needs a *direct* stream URL** "
                        "(ending in `.mp3`/`.aac`/`.m3u8`/`.pls`), not a station web page.\n"
                        "For example, `https://www.radio.de/s/antennebayern` won't work — use the actual stream, "
                        "e.g. `https://s1-webradio.antenne.de/antenne`.\n"
                        f"No URL handy? Pick a built-in preset — see `{ctx.clean_prefix}music radios`."
                    )
                else:
                    await ctx.send_error("I couldn't resolve that source. Double-check the URL or search term.")
                return

        await self.enable_always_on(ctx.guild, channel, ctx.channel, mode, source)
        target = f"**{playlist_label}**" if playlist_label else (f"**{preset_label}**" if preset_label else f"**24/7 ({mode})**")
        await ctx.send_success(
            f"Now running {target} in {channel.mention}.\n"
            f"I'll automatically reconnect and resume after disconnects or restarts."
        )

    @music_247.autocomplete("source")
    async def music_247_source_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[Choice[str]]:
        """Suggest radio presets or user playlists depending on the selected mode."""
        mode = interaction.namespace.mode
        if mode == "radio":
            current_l = current.lower()
            choices: list[Choice[str]] = []
            for slug, preset in RADIO_PRESETS.items():
                label = preset[0]
                if current_l in slug or current_l in label.lower():
                    choices.append(Choice(name=f"{slug} — {label}"[:100], value=slug))
                if len(choices) >= 25:
                    break
            return choices

        if mode == "playlist":
            pt: PlaylistTools | None = self.bot.get_cog("PlaylistTools")  # type: ignore[assignment]
            if pt is None:
                return []
            playlists = await pt.get_playlists(user_id=interaction.user.id)
            current_l = current.lower()
            choices = []
            for p in playlists:
                display = f"[{p.id}] {p.name} ({len(p.tracks)} tracks)"
                if not current_l or current_l in p.name.lower() or current_l in str(p.id):
                    choices.append(Choice(name=display[:100], value=str(p.id)))
                if len(choices) >= 25:
                    break
            return choices
        return []

    @_music.command("radios", description="List the built-in 24/7 radio presets.")
    async def music_radios(self, ctx: Context) -> None:
        """List the curated radio presets usable with `music 247 radio <name>`."""
        embed = discord.Embed(
            title="\N{RADIO} 24/7 Radio Presets",
            description=(
                f"Use any of these with `{ctx.clean_prefix}music 247 radio <name>` "
                f"(e.g. `{ctx.clean_prefix}music 247 radio lofi`):"
            ),
            colour=helpers.Colour.white(),
        )
        for slug, preset in RADIO_PRESETS.items():
            embed.add_field(name=f"`{slug}`", value=preset[0], inline=True)
        embed.set_footer(text="You can also pass any direct stream URL (.mp3 / .aac / .m3u8 / .pls).")
        await ctx.send(embed=embed)

    @Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Handle Auto-Delete for messages in a given music player channel."""
        await self.bot.wait_until_ready()
        if message.guild is None:
            return

        config = await self.bot.db.get_guild_config(guild_id=message.guild.id)
        if not (config.music_panel_channel_id and config.music_panel_message_id):
            return

        if (
            message.channel.id == config.music_panel_channel_id
            and not message.pinned
            and message.id != config.music_panel_message_id
        ):
            await message.delete(delay=60)


class PlaylistNameOrID(commands.clean_content):
    """Converts the content to either an integer or string."""

    def __init__(self, *, lower: bool = False, with_id: bool = False) -> None:
        self.lower: bool = lower
        self.with_id: bool = with_id
        super().__init__()

    async def convert(self, ctx: Context, argument: str) -> str | int:
        converted = await super().convert(ctx, argument)
        lower = converted.lower().strip()

        if not lower:
            raise commands.BadArgument("Please enter a valid playlist name" + " or id." if self.with_id else ".")

        if len(lower) > 100:
            raise commands.BadArgument(
                f"Playlist names must be 100 characters or less. (You have *{len(lower)}* characters)"
            )

        cog: PlaylistTools | None = ctx.bot.get_cog("PlaylistTools")  # type: ignore
        if cog is None:
            raise commands.BadArgument("Playlist tools are currently unavailable.")

        if self.with_id and converted and converted.isdigit():
            return int(converted)

        return converted.strip() if not self.lower else lower


class PlaylistTools(Cog):
    """Additional Music Tools for the Music Cog.
    Like: Playlist, DJ, Setup etc."""

    emoji = "\N{GUITAR}"

    async def cog_before_invoke(self, ctx: Context) -> None:
        await self.initizalize_user(ctx.author)

    async def initizalize_user(self, user: discord.abc.User | discord.Member) -> int | None:
        # Creates a static Playlist for every new User that interacts with the Bot
        # called 'Liked Songs', this Playlist cannot be deleted
        # and is used to store all liked songs from the user.

        # The User can store Liked Songs using the Button the Player Control Panel

        playlists = await self.get_playlists(user_id=user.id)
        if any(playlist.is_liked_songs for playlist in playlists):
            return None

        record = await self.bot.db.playlists.create_playlist(
            user.id, "Liked Songs", discord.utils.utcnow().replace(tzinfo=None)
        )
        self.get_playlists.invalidate(user.id)
        return record

    async def playlist_autocomplete(self, interaction: discord.Interaction, current: str) -> list[Choice[str | int | float]]:
        playlists = await self.get_playlists(user_id=interaction.user.id)

        def key(p: Playlist) -> str:
            cmd = interaction.command
            if cmd is not None and cmd.name == "play":
                return p.choice_text
            return p.choice_text if p.name != "Liked Songs" else ""

        results = fuzzy.finder(current, playlists, key=key, raw=True)

        return [
            app_commands.Choice(name=get_shortened_string(length, start, playlist.choice_text), value=playlist.id)
            for length, start, playlist in results[:20]
        ]

    async def _get_playlist_tracks(self, playlist_id: int) -> list[PlaylistTrack]:
        records = await self.bot.db.playlists.get_playlist_tracks(playlist_id)
        return [PlaylistTrack(record=record) for record in records]

    async def get_playlist(
        self, ctx: Context | discord.Interaction, name_or_id: str | int, *, pass_tracks: bool = False
    ) -> Playlist | None:
        """Gets a poll by ID.

        Parameters
        ----------
        ctx: Context | discord.Interaction
            The Context or Interaction.
        name_or_id: str | int
            The name or ID of the playlist.
        pass_tracks: bool
            Whether to skip gathering the tracks of the playlist.

        Returns
        -------
        Playlist
            The Playlist if found, else None.
        """
        if isinstance(name_or_id, int):
            record = await self.bot.db.playlists.get_playlist_by_id(name_or_id)
        else:
            record = await self.bot.db.playlists.get_playlist_by_name(ctx.user.id, name_or_id)

        playlist = Playlist(cog=self, record=record) if record else None

        if playlist and pass_tracks is False:
            playlist.tracks = await self._get_playlist_tracks(playlist.id)
        return playlist

    async def get_liked_songs(self, user_id: int) -> Playlist | None:
        """Gets a User 'Liked Songs' playlist."""
        record = await self.bot.db.playlists.get_liked_songs(user_id)
        playlist = Playlist(cog=self, record=record) if record else None

        if playlist:
            playlist.tracks = await self._get_playlist_tracks(playlist.id)
        return playlist

    @cache.cache()
    async def get_playlists(self, user_id: int) -> list[Playlist]:
        """Get all playlists from a user.

        Parameters
        ----------
        user_id: int
            The user id to get the playlists from.

        Returns
        -------
        list[Playlist]
            A list of all playlists from the user.
        """
        records = await self.bot.db.playlists.get_user_playlists(user_id)
        playlists = [Playlist(cog=self, record=record) for record in records]

        for playlist in playlists:
            playlist.tracks = await self._get_playlist_tracks(playlist.id)
        return playlists

    @group(name="playlist", alias="pl", description="Manage your playlist.", guild_only=True, hybrid=True)
    async def playlist(self, ctx: Context) -> None:
        """Manage your playlist."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @playlist.command(name="show", description="Display all your playlists and tracks.")
    async def playlist_show(self, ctx: Context) -> None:
        """Display all your playlists and tracks."""
        playlists = await self.get_playlists(user_id=ctx.author.id)
        if not playlists:
            await ctx.send_error(
                f"You don't have any playlists. You can create a playlist using `{ctx.prefix}playlist create`."
            )
            return

        items = [playlist.field_tuple for playlist in playlists]

        fields = [items[i : i + 12] for i in range(0, len(items), 12)]

        embeds = []
        for field in fields:
            embed = discord.Embed(
                title="Your Playlists",
                description="Here are your playlists, use the buttons and view to navigate",
                color=helpers.Colour.white(),
            )
            embed.set_author(name=ctx.author, icon_url=ctx.author.display_avatar.url)
            embed.set_footer(text=f"{pluralize(len(playlists)):playlist}")
            for name, value in field:
                embed.add_field(name=name, value=value, inline=False)
            embeds.append(embed)

        await PlaylistPaginator.start(
            ctx, entries=embeds, per_page=1, ephemeral=True, playlists=playlists, start_pages=embeds
        )

    @playlist.command(name="create", description="Create a new playlist.")
    @app_commands.describe(name="The name of your new playlist.")
    async def playlist_create(self, ctx: Context, name: str) -> None:
        """Create a new playlist."""
        playlists = await self.get_playlists(user_id=ctx.author.id)

        if len(playlists) == 3 and not await self.bot.is_owner(ctx.author):
            await ctx.send_error("You can only have `3` playlists at the same time.")
            return

        if any(playlist.name == name for playlist in playlists):
            await ctx.send_error("There is already a playlist with this name, please choose another name.")
            return

        if len(name) > 100:
            await ctx.send_error("The name of the playlist must be 100 characters or less.")
            return

        playlist_id = await self.bot.db.playlists.create_playlist(ctx.author.id, name, discord.utils.utcnow())
        self.get_playlists.invalidate(ctx.author.id)

        await ctx.send_success(f"Successfully created playlist **{name}** [`{playlist_id}`].")

    @playlist.command(
        name="play",
        description="Add the songs from you playlist to the plugins queue and play them.",
    )
    @app_commands.rename(name_or_id="name-or-id")
    @app_commands.describe(name_or_id="The name or id of your playlist to play.")
    @app_commands.autocomplete(name_or_id=playlist_autocomplete)  # type: ignore
    @checks.is_listen_together()
    @checks.is_author_connected()
    async def playlist_play(
        self,
        ctx: Context,
        *,
        name_or_id: Annotated[str | int, PlaylistNameOrID(lower=True, with_id=True)],
    ) -> None:
        """Add the songs from you playlist to the plugins queue and play them."""
        player: Player = cast("Player", ctx.voice_client)
        if player and player.always_on:
            await ctx.send_error(
                f"A 24/7 session is currently active. "
                f"Disable it first with `{ctx.clean_prefix}music 247 off` before playing a playlist."
            )
            return

        playlist = await self.get_playlist(ctx, name_or_id)
        if playlist is None:
            await ctx.send_error("There is no playlist with this id.")
            return

        if len(playlist) == 0:
            await ctx.send_error("There are no tracks in this playlist, please add some using `/playlist add`.")
            return

        if not player:
            player = await Player.join(ctx)

        old_stamp = len(player.queue.all)
        wait_message = await ctx.send(f"*{Emojis.loading} adding tracks from your playlist to the queue... please wait...*")

        for track in playlist.tracks:
            resolved: Playable | Playlist | SearchReturn = await player.search(track.url, ctx=ctx)
            if not resolved or isinstance(resolved, SearchReturn):
                continue
            await player.queue.put_wait(resolved)

        new_queue = len(player.queue.all) - old_stamp
        succeeded = bool(new_queue == len(playlist.tracks))

        description = (
            f"`🎶` Successfully added **{new_queue}/{len(playlist.tracks)}** tracks from your playlist to the queue."
        )
        if not succeeded:
            description += f"\n{Emojis.warning} *Some tracks may not have been added due to unexpected issues.*"
        embed = discord.Embed(description=description, color=helpers.Colour.teal())
        embed.set_author(name=f"[{playlist.id}] • {playlist.name}", icon_url=ctx.author.display_avatar.url)
        embed.set_footer(text="Now Playing")
        await wait_message.delete()
        await ctx.send(embed=embed, delete_after=15)

        if not player.playing:
            player.autoplay = wavelink.AutoPlayMode.enabled
            await player.play(player.queue.get(), volume=70)
        else:
            await player.panel.update()

    @playlist.command(name="add", description="Adds the current playing track or a track via a direct-url to your playlist.")
    @app_commands.rename(name_or_id="name-or-id")
    @app_commands.describe(
        query="The direct-url of the track/playlist/album you want to add to your playlist.",
        name_or_id="The id of your playlist.",
    )
    @app_commands.autocomplete(name_or_id=playlist_autocomplete)  # type: ignore
    async def playlist_add(
        self,
        ctx: Context,
        name_or_id: Annotated[str | int, PlaylistNameOrID(lower=True, with_id=True)],
        *,
        query: str | None = None,
    ) -> None:
        """Adds the current playing track or a track via a direct-url to your playlist."""
        if not query and not (ctx.voice_client and ctx.voice_client.channel):
            await ctx.send_error("You have to provide either the `link` parameter or a current playing track.")
            return

        playlist = await self.get_playlist(ctx, name_or_id)
        if playlist is None:
            await ctx.send_error("There is no playlist with that name.")
            return

        if not query and ctx.guild and ctx.guild.voice_client:
            player: Player = cast("Player", ctx.voice_client)

            if not player.current:
                await ctx.send_error("You have to provide either the `link` parameter or a current playing track.")
                return

            await playlist.add_track(player.current)
            embed = discord.Embed(
                description=f"Added Track **[{player.current.title}]({player.current.uri})** to your playlist "
                f"at Position **#{len(playlist.tracks)}**",
                color=helpers.Colour.teal(),
            )
            embed.set_thumbnail(url=player.current.artwork)
            embed.set_author(name=ctx.author, icon_url=ctx.author.display_avatar.url)
            embed.set_footer(text=f"[{playlist.id}] • {playlist.name}")
            await ctx.send(embed=embed, ephemeral=True)
        else:
            assert query is not None
            result = await Player.search(query, ctx=ctx)
            if isinstance(result, SearchReturn):
                if result == SearchReturn.NO_RESULTS:
                    await ctx.send_error("Sorry! No results found matching your query.")
                elif result == SearchReturn.NO_YOUTUBE_ALLOWED:
                    await ctx.send_error("Sorry, you can't add YouTube tracks with this bot.")
                return

            added = [track.url for track in playlist.tracks]
            if isinstance(result, wavelink.Playlist):
                success = 0
                for track in result.tracks:
                    if track.uri in added:
                        continue
                    await playlist.add_track(track)
                    success += 1

                embed = discord.Embed(
                    description=f"Added **{success}**/**{len(result.tracks)}** Tracks from {result.name} **[{result.name}]({result.url})** to your playlist.\n"
                    f"Next Track at Position **#{len(playlist.tracks)}**",
                    color=helpers.Colour.teal(),
                )
                embed.set_thumbnail(url=result.artwork)
                embed.set_author(name=ctx.author, icon_url=ctx.author.display_avatar.url)
                embed.set_footer(text=f"[{playlist.id}] • {playlist.name}")
                await ctx.send(embed=embed, ephemeral=True)
            else:
                if result.uri in added:
                    await ctx.send_error("This Track is already in your playlist.")
                    return
                await playlist.add_track(result)

                embed = discord.Embed(
                    description=f"Added Track **[{result.title}]({result.uri})** to your playlist.\n"
                    f"Track at Position **#{len(playlist.tracks)}**",
                    color=helpers.Colour.teal(),
                )
                embed.set_thumbnail(url=result.artwork)
                embed.set_author(name=ctx.author, icon_url=ctx.author.display_avatar.url)
                embed.set_footer(text=f"[{playlist.id}] • {playlist.name}")
                await ctx.send(embed=embed, ephemeral=True)

        self.get_playlists.invalidate(ctx.author.id)

    @playlist.command(name="delete", alias="del", description="Delete a playlist.")
    @app_commands.rename(name_or_id="name-or-id")
    @app_commands.describe(name_or_id="The name or id of the playlist you want to delete.")
    @app_commands.autocomplete(name_or_id=playlist_autocomplete)  # type: ignore
    async def playlist_delete(
        self,
        ctx: Context,
        *,
        name_or_id: Annotated[str | int, PlaylistNameOrID(lower=True, with_id=True)],
    ) -> None:
        """Delete a playlist."""
        playlist = await self.get_playlist(ctx, name_or_id, pass_tracks=True)
        if playlist is None:
            await ctx.send_error("No playlist was found matching your query.")
            return

        if playlist.name == "Liked Songs":
            await ctx.send_error("You cannot delete the Liked Songs playlist.")
            return

        await playlist.delete()
        await ctx.send_success(
            f"Successfully deleted playlist **{playlist.name}** [`{playlist.id}`] and all corresponding entries.",
            ephemeral=True,
        )
        self.get_playlists.invalidate(ctx.author.id)

    @playlist.command(
        name="clear", aliases=["purge", "clean"], description="Clear all Items in a playlist.", guild_only=True
    )
    @app_commands.rename(name_or_id="name-or-id")
    @app_commands.describe(name_or_id="The name or id of the playlist you want to clear.")
    @app_commands.autocomplete(name_or_id=playlist_autocomplete)  # type: ignore
    async def playlist_clear(
        self,
        ctx: Context,
        *,
        name_or_id: Annotated[str | int, PlaylistNameOrID(lower=True, with_id=True)],
    ) -> None:
        """Clear all Items in a playlist."""
        playlist = await self.get_playlist(ctx, name_or_id, pass_tracks=True)
        if playlist is None:
            await ctx.send_error("No playlist was found matching your query.")
            return

        await playlist.clear()
        await ctx.send_success(
            f"Successfully purged all corresponding entries of playlist **{playlist.name}** [`{playlist.id}`].",
            ephemeral=True,
        )
        self.get_playlists.invalidate(ctx.author.id)

    @playlist.command(name="remove", alias="rm", description="Remove a track from your playlist.", guild_only=True)
    @app_commands.rename(name_or_id='name-or-id')
    @app_commands.describe(
        name_or_id='The playlist ID you want to remove a track from.',
        track_id='The ID of the track to remove.')
    @app_commands.autocomplete(name_or_id=playlist_autocomplete)  # type: ignore
    async def playlist_remove(
            self,
            ctx: Context,
            name_or_id: Annotated[str | int, PlaylistNameOrID(lower=True, with_id=True)],
            track_id: int
    ) -> None:
        """Remove a track from your playlist."""
        playlist = await self.get_playlist(ctx, name_or_id)
        if playlist is None:
            await ctx.send_error('No playlist was found matching your query.')
            return

        track = discord.utils.get(playlist.tracks, id=track_id)
        if not track:
            await ctx.send_error('No track was found matching your query.')
            return

        await playlist.remove_track(track)
        await ctx.send_success(
            f'Successfully removed track **{track.name}** [`{track.id}`] from playlist **{playlist.name}** [`{playlist.id}`].',
            ephemeral=True)
        self.get_playlists.invalidate(ctx.author.id)


async def setup(bot: Bot) -> None:
    try:
        wavelink.Pool.get_node()
    except wavelink.InvalidNodeException:
        log.warning('Music Cog not being initialized as no nodes are available.')
        return

    await bot.add_cog(Music(bot))
    await bot.add_cog(PlaylistTools(bot))
