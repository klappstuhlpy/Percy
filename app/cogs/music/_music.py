from __future__ import annotations

import asyncio
import datetime
import logging
import random
import re
from contextlib import suppress
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, Literal, cast
from urllib.parse import urljoin

import discord
import wavelink
from bs4 import BeautifulSoup, NavigableString, PageElement, Tag
from discord import app_commands
from discord.ext import commands
from discord.utils import MISSING

from app.core import Cog, Context, Flags, flag, store_true
from app.core.models import command, describe, group
from app.utils import ProgressBar, checks, convert_duration, helpers, pagify
from app.utils.pagination import BasePaginator
from config import Emojis, genius_key

from ...rendering import Music
from ._player import Player, SearchReturn
from ._queue import ShuffleMode

if TYPE_CHECKING:
    from ._playlist import PlaylistTools

log = logging.getLogger(__name__)

DEFAULT_CHANNEL_DESCRIPTION = """
This is the Channel where you can see {bot}\'s current playing songs.
You can interact with the **control panel** and manage the current songs.

__Be careful not to delete the **control panel** message.__
If you accidentally deleted the message, you have to redo the setup with </music setup:1207828024666497090>.

ℹ️** | Every Message if not pinned, gets deleted within 60 seconds.**
"""


class PlayFlags(Flags):
    """Flags for the music commands."""
    source: Literal['sp', 'sc'] = flag(
        name='source', description='What source to search for your query.', aliases=['s'], default='sc')
    force: bool = store_true(
        name='force', description='Whether to force play the track/playlist.', aliases=['f'])
    recommendations: bool = store_true(
        name='recommendations',
        short='r',
        description='Whether to auto-fill the queue with recommended tracks if the queue is empty.')


class VolumeConverter(commands.Converter[int]):
    VOLUME_REGEX: Final[ClassVar[re.Pattern]] = re.compile(r'^[+-]?\d+$')

    async def convert(self, ctx: Context, argument: str) -> int:
        player: Player = cast(Player, ctx.voice_client)

        if not (match := self.VOLUME_REGEX.match(argument)):
            raise commands.BadArgument(
                'Invalid Volume provided.\n'
                'Please provide a valid number between **0-100** or a relative number, e.g. **+10** or **-15**.')

        if match.group().startswith(('+', '-')):
            return player.volume + int(match.group()[1:])
        return int(match.group())


class Music(Cog):
    """Commands for playing music in a voice channel."""

    emoji = '<:music:1322338453937193000>'
    render = Music()

    async def cog_before_invoke(self, ctx: Context) -> None:
        playlist_tools: PlaylistTools | None = self.bot.get_cog('PlaylistTools')
        await playlist_tools.initizalize_user(ctx.author)

    @Cog.listener(name='on_wavelink_track_exception')
    @Cog.listener(name='on_wavelink_track_stuck')
    @Cog.listener(name='on_wavelink_websocket_closed')
    @Cog.listener(name='on_wavelink_extra_event')
    async def on_wavelink_intercourse(
            self,
            payload: wavelink.TrackExceptionEventPayload | wavelink.TrackStuckEventPayload | wavelink.WebsocketClosedEventPayload | wavelink.ExtraEventPayload
    ) -> None:
        """Handle Wavelink errors."""
        if isinstance(payload, wavelink.WebsocketClosedEventPayload) and payload.code.value in (1000, 4006, 4014):
            # Normal close, Session Invalid, Disconnected
            return

        player: Player | None = cast(Player, payload.player)

        if player:
            try:
                await player.disconnect()
            except Exception as exc:
                log.debug('Error while destroying player: %s', exc)
                pass

        args = [f'{k}={v!r}' for k, v in vars(payload).items()]
        log.warning('Wavelink Error occurred: %s | %s', payload.__class__.__name__, ', '.join(args))

    @Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        logging.info('Wavelink Node connected: %s | Resumed: %s', payload.node.uri, payload.resumed)

    @Cog.listener()
    async def on_wavelink_inactive_player(self, player: Player) -> None:
        if not player:
            return

        with suppress(discord.HTTPException):
            await player.channel.send(
                f'The player has been inactive for `{player.inactive_timeout}` seconds. *Goodbye!*')

        if player.connected:
            await player.disconnect()

    @Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        player: Player | None = cast(Player, payload.player)
        if not player:
            return

        if player.queue.listen_together is not MISSING:
            member = await self.bot.get_or_fetch_member(
                player.guild, player.queue.listen_together)
            if (activity := next((a for a in member.activities if isinstance(a, discord.Spotify)), None)) is None:
                return await player.disconnect()

            try:
                track = await player.search(activity.track_url)
            except Exception as exc:
                log.debug('Error while searching for track: %s', exc)
                await player.panel.channel.send('I couldn\'t find the track you were listening to on Spotify.')
                return await player.disconnect()

            player.queue.reset()
            await player.queue.put_wait(track)
            await player.play(player.queue.get())
            return await player.send_track_add(track)

        if player.autoplay != wavelink.AutoPlayMode.enabled and player.queue.is_empty:
            # we gracefully disconnect if there are no tracks left
            # in the queue and autoplay is disabled/partial enabled
            return await player.disconnect()

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
            player.queue.history.clear()
            player.queue.history.put(queue[:queue.index(next_random_track)])

            # Add all tracks that are after the next random track to the queue
            player.queue.clear()
            await player.queue.put_wait(queue[queue.index(next_random_track):])

    @Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload) -> None:
        player: Player | None = cast(Player, payload.player)
        if not player:
            return

        if player.current.recommended:
            player.queue.history.put(player.current)

        while not player.queue.all or player.current not in player.queue.all:
            # ensure that the current track is in the queue
            await asyncio.sleep(0.5)

        channel = player.channel
        if isinstance(channel, discord.StageChannel):
            intance = channel.instance or await channel.fetch_instance()
            if not intance:
                await channel.create_instance(topic=player.current.title)
            else:
                await intance.edit(topic=player.current.title)

        await player.panel.update()

    @staticmethod
    def _get_spotify_activity(member: discord.Member) -> discord.Spotify | None:
        return next((a for a in member.activities if isinstance(a, discord.Spotify)), None)

    @Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member) -> None:
        await self.bot.wait_until_ready()
        player: Player | None = cast(Player, before.guild.voice_client)
        if not player:
            return

        user_id = player.queue.listen_together
        if user_id is MISSING or user_id and before.id != user_id:
            return

        before_activity = self._get_spotify_activity(before)
        after_activity = self._get_spotify_activity(after)

        if before_activity and after_activity:
            if before_activity.title == after_activity.title:
                now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
                start = after_activity.start.replace(tzinfo=None)
                end = after_activity.end.replace(tzinfo=None)

                deter = (end - now if now > end else now - start).total_seconds()
                position = round(deter) * 1000

                await player.seek(position)
                await player.panel.update()
            else:
                new_activity = self._get_spotify_activity(after)
                if new_activity and new_activity.title == before_activity.title:
                    await player.pause(False)
                else:
                    player.queue.reset()

                    try:
                        track = await player.search(new_activity.track_url)
                    except Exception as exc:
                        log.debug('Error while searching for track: %s', exc)
                        await player.panel.channel.send(
                            f'{Emojis.error} I couldn\'t find the track <@{user_id}> was listening to on spotify.',
                            delete_after=10)
                        return await player.disconnect()

                    await player.queue.put_wait(track)
                    await player.send_track_add(track)
                    await player.play(player.queue.get())

                    position = round(
                        (datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - new_activity.start.replace(
                            tzinfo=None)).total_seconds()) * 1000
                    await player.seek(position)
        else:
            await player.panel.channel.send('The host has stopped listening to Spotify.')
            await player.disconnect()

    @command(
        description='Adds a track/playlist to the queue.',
        guild_only=True,
        hybrid=True
    )
    @describe(query='The track/playlist to add to the queue. Can be a URL or a search query.')
    @app_commands.choices(
        source=[
            app_commands.Choice(name='SoundCloud (Default)', value='sc'),
            app_commands.Choice(name='Spotify', value='sp')
        ],
    )
    @checks.is_author_connected()
    @checks.is_listen_together()
    async def play(self, ctx: Context, *, query: str, flags: PlayFlags) -> None:
        """Play Music in a voice channel by searching for a track/playlist."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            player = await Player.join(ctx)

        # Note: Due to Discords ToS, we can't use YouTube as our source of music.
        SOURCE_LOOKUP = {
            # 'yt': wavelink.TrackSource.YouTubeMusic,
            'sp': 'spsearch',
            'sc': wavelink.TrackSource.SoundCloud
        }
        source = SOURCE_LOOKUP.get(flags.source, wavelink.TrackSource.SoundCloud)

        player.autoplay = wavelink.AutoPlayMode.enabled if flags.recommendations else wavelink.AutoPlayMode.partial

        if not query:
            await ctx.send_error('Please provide a search query.')
            return

        result = await player.search(query, source=source, ctx=ctx,
                                     return_first=not hasattr(flags, '__with_search__'))
        if isinstance(result, SearchReturn):
            if result == SearchReturn.NO_RESULTS:
                await ctx.send_error('Sorry! No results found matching your query.')
            elif result == SearchReturn.NO_YOUTUBE_ALLOWED:
                await ctx.send_error('Sorry, you can\'t play YouTube tracks from this bot.')
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
        description='Adds a track/playlist to the queue by choosing from a set of examples.',
        guild_only=True,
        hybrid=True
    )
    @describe(query='The track/playlist to add to the queue. Can be a URL or a search query.')
    @app_commands.choices(
        source=[
            app_commands.Choice(name='YouTube (Default)', value='yt'),
            app_commands.Choice(name='Spotify', value='sp'),
            app_commands.Choice(name='SoundCloud', value='sc')]
    )
    @checks.is_author_connected()
    @checks.is_listen_together()
    async def playsearch(self, ctx: Context, *, query: str, flags: PlayFlags) -> None:
        """Adds a track/playlist to the queue by choosing from a variety of examples."""
        setattr(flags, '__with_search__', True)
        await ctx.invoke(self.play, query=query, flags=flags)  # type: ignore

    @group(
        'listen-together',
        fallback='start',
        description='Start a listen-together activity with a user.',
        guild_only=True,
        hybrid=True
    )
    @describe(member='The user you want to start a listen-together activity with.')
    @checks.is_author_connected()
    async def listen_together(self, ctx: Context, member: discord.Member) -> None:
        """Start a listen-together activity with an user.
        `Note:` Only supported for Spotify Music."""
        await ctx.defer()

        if not ctx.guild.voice_client:
            await Player.join(ctx)

        player: Player = cast(Player, ctx.guild.voice_client)
        if not player:
            return

        # We need to fetch the member to get the current activity
        member = await self.bot.get_or_fetch_member(ctx.guild, member.id)

        if not (activity := next((a for a in member.activities if isinstance(a, discord.Spotify)), None)):
            await ctx.send_error(f'**{member.display_name}** is not currently listening to Spotify.')
            return

        if player.playing or player.queue.listen_together is not MISSING:
            player.queue.reset()
            await player.stop()

        player.autoplay = wavelink.AutoPlayMode.disabled

        try:
            track = await player.search(activity.track_url)
        except Exception as exc:
            log.debug('Error while searching for track: %s', exc)
            await ctx.send_error(f'I couldn\'t find the track <@{member.id}> was listening to on Spotify.')
            return await player.disconnect()

        await player.queue.put_wait(track)
        player.queue.listen_together = member.id
        await player.play(player.queue.get())

        poss = round((datetime.datetime.now(datetime.UTC).replace(tzinfo=None) -
                      activity.start.replace(tzinfo=None)).total_seconds()) * 1000
        await player.seek(poss)

        await player.send_track_add(track, ctx)
        await player.panel.update()

    @listen_together.command(
        name='stop',
        description='Stops the current listen-together activity.'
    )
    async def listen_together_stop(self, ctx: Context) -> None:
        """Stops the current listen-together activity."""
        player: Player = cast(Player, ctx.guild.voice_client)
        if not player:
            return

        if player.queue.listen_together is MISSING:
            await ctx.send_error('There is no listen-together activity to stop.')
            return

        await player.disconnect()
        await ctx.send_success(f'{Emojis.success} Stopped the current listen-together activity.', delete_after=10)

    @command('connect', description='Connect me to a voice-channel.', hybrid=True, guild_only=True)
    @describe(channel='The Voice/Stage-Channel you want to connect to.')
    async def connect(self, ctx: Context, channel: discord.VoiceChannel | discord.StageChannel = None) -> None:
        """Connect me to a voice-channel."""
        if ctx.voice_client:
            await ctx.send_error('I am already connected to a voice channel. Please disconnect me first.')
            return

        try:
            channel = channel or ctx.author.voice.channel
        except AttributeError:
            await ctx.send_error('No voice channel to connect to. Please either provide one or join one.')
            return

        await Player.join(ctx)
        await ctx.send_success(f'Connected and bound to {channel.mention}', delete_after=10)

    @command(description='Disconnect me from a voice-channel.', hybrid=True, guild_only=True)
    @checks.is_author_connected()
    @checks.is_player_connected()
    async def leave(self, ctx: Context) -> None:
        """Disconnect me from a voice-channel."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        await player.disconnect()
        await ctx.send_success('Disconnected Channel and cleaned up the queue.', delete_after=10)

    @command('stop', description='Clears the queue and stop the current plugins.', hybrid=True, guild_only=True)
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def stop(self, ctx: Context) -> None:
        """Clears the queue and stop the current plugins."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        await player.disconnect()
        await ctx.send_success('Stopped Track and cleaned up queue.', delete_after=10)

    @command(
        'toggle',
        aliases=['pause', 'resume'],
        description='Pause/Resume the current track.',
        guild_only=True,
        hybrid=True
    )
    @checks.is_author_connected()
    @checks.is_listen_together()
    async def pause_or_resume(self, ctx: Context) -> None:
        """Pause the current playing track."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        await player.pause(not player.paused)
        await ctx.send_success(
            f'{'Paused' if player.paused else 'Resumed'} Track [{player.current.title}]({player.current.uri})',
            delete_after=10, suppress_embeds=True)
        await player.panel.update()

    @command(description='Sets a loop mode for the plugins.', hybrid=True, guild_only=True)
    @describe(mode='Select a loop mode.')
    @app_commands.choices(
        mode=[
            app_commands.Choice(name='Normal', value='normal'),
            app_commands.Choice(name='Track', value='track'),
            app_commands.Choice(name='Queue', value='queue')
        ]
    )
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def loop(self, ctx: Context, mode: Literal['normal', 'track', 'queue']) -> None:
        """Sets a loop mode for the plugins."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        player.queue.mode = {'normal': 0, 'track': 1, 'queue': 2}.get(mode)

        await player.panel.update()
        await ctx.send_success(f'Loop Mode changed to `{mode}`', delete_after=10)

    @command(description='Sets the shuffle mode for the plugins.', hybrid=True, guild_only=True)
    @describe(mode='Select a shuffle mode.')
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def shuffle(self, ctx: Context, mode: bool) -> None:
        """Sets the shuffle mode for the plugins."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        player.queue.shuffle = ShuffleMode.on if mode else ShuffleMode.off
        await player.panel.update()
        await ctx.send_success(f'Shuffle Mode changed to `{mode}`', delete_after=10)

    @command(description='Seek to a specific position in the tack.', hybrid=True, guild_only=True)
    @describe(position='The position to seek to. (Format: H:M:S or S)')
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def seek(self, ctx: Context, position: str | None = None) -> None:
        """Seek to a specific position in the tack."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        if player.current.is_stream:
            await ctx.send_error('Cannot seek if track is a stream.')
            return

        if position is None:
            seconds = 0
            await player.seek(seconds)
        else:
            try:
                seconds = sum(int(x) * 60 ** i for i, x in enumerate(reversed(position.split(':'))))
            except ValueError:
                await ctx.send_error('Please provide a valid timestamp format. (e.g. 3:20, 23)',
                                     ephemeral=True)
                return

            seconds *= 1000  # Convert to milliseconds
            if seconds in range(player.current.length):
                await player.seek(seconds)
            else:
                await ctx.send_error('Please provide a seek time within the range of the track.',
                                     ephemeral=True, delete_after=10)
                return

        await ctx.send_success(f'Seeked to position `{convert_duration(seconds)}`', delete_after=10)
        await player.panel.update()

    @seek.autocomplete('position')
    async def seek_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        player: Player = cast(Player, interaction.guild.voice_client)
        if not player:
            return []

        def _timestamp(secs: int) -> str:
            return datetime.datetime.fromtimestamp(secs, datetime.UTC).strftime('%H:%M:%S')

        try:
            seconds = sum(int(x.strip('""')) * 60 ** inT for inT, x in enumerate(reversed(current.split(':'))))
        except ValueError:
            # Return a list of 3 choice timestamps -> track length, 1/3, 2/3
            length = player.current.length / 1000  # Convert to seconds
            return [
                app_commands.Choice(name=_timestamp(int(length / 3)), value=str(int(length / 3))),
                app_commands.Choice(name=_timestamp(int(length / 3 * 2)), value=str(int(length / 3 * 2)))
            ]

        timestamp = _timestamp(seconds)
        return [app_commands.Choice(name=timestamp, value=timestamp)]

    @command(description='Set the volume for the plugins.', hybrid=True, guild_only=True)
    @describe(amount='The volume to set the plugins to. (0-100)')
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def volume(self, ctx: Context, amount: Annotated[int, VolumeConverter] | None = None) -> None:
        """Set the volume for the plugins."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        if amount is None:
            embed = discord.Embed(title='Current Volume', color=helpers.Colour.white())
            embed.add_field(
                name='Volume:',
                value=f'```swift\n{ProgressBar(0, 100, player.volume)} [ {player.volume}% ]```',
                inline=False)
            await ctx.send(embed=embed, delete_after=15)
            return

        await player.set_volume(amount)
        await player.panel.update()

        embed = discord.Embed(title='Changed Volume', color=helpers.Colour.white(),
                              description='*It may takes a while for the changes to apply.*')
        embed.add_field(
            name='Volume:',
            value=f'```swift\n{ProgressBar(0, 100, player.volume)} [ {player.volume}% ]```',
            inline=False)
        await ctx.send(embed=embed, delete_after=15)

    @command(
        description='Removes all songs from users that are not in the voice channel.',
        hybrid=True,
        guild_only=True
    )
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def cleanupleft(self, ctx: Context) -> None:
        """Removes all songs from users that are not in the voice channel."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        await player.cleanupleft()
        await player.panel.update()
        await ctx.send_success('Cleaned up the queue.', delete_after=10)

    @group(
        description='Manage Advanced Filters to specify you listening experience.',
        guild_only=True,
        hybrid=True
    )
    async def filter(self, ctx: Context) -> None:
        """Find useful information about the filter command group."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @filter.command(
        'equalizer',
        description='Set the equalizer for the current Track.'
    )
    @describe(band='The Band you want to change. (1-15)', gain='The Gain you want to set. (-0.25-+1.0)')
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def filter_equalizer(
            self,
            ctx: Context,
            band: app_commands.Range[int, 1, 15] = None,
            gain: app_commands.Range[float, -0.25, +1.0] = None
    ) -> None:
        """Set a custom Equalizer for the current Track.

        Notes
        -----
        The preset paremeter will be given priority, if provided.
        """
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        await ctx.defer()

        filters: wavelink.Filters = player.filters
        if not band or not gain:
            await ctx.send_error('Please provide a valid Band and Gain or a Preset.')
            return

        band -= 1

        eq = filters.equalizer.payload
        eq[band]['gain'] = gain
        filters.equalizer.set(bands=list(eq.values()))
        await player.set_filters(filters)

        embed = discord.Embed(title='Changed Filter', color=helpers.Colour.white(),
                              description='*It may takes a while for the changes to apply.*')
        image = self.render.generate_eq_image([entry['gain'] for entry in filters.equalizer.payload.values()])
        embed.set_image(url='attachment://image.png')
        embed.set_footer(text=f'Requested by: {ctx.author}')
        await ctx.send(embed=embed, file=image, delete_after=20)

    @filter.command(
        'bassboost',
        description='Enable/Disable the bassboost filter.'
    )
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def filter_bassboost(self, ctx: Context) -> None:
        """Apply a bassboost filter for the current track."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        await ctx.defer()

        filters: wavelink.Filters = player.filters
        filters.equalizer.set(bands=[
            {'band': 0, 'gain': 0.2}, {'band': 1, 'gain': 0.15}, {'band': 2, 'gain': 0.1},
            {'band': 3, 'gain': 0.05}, {'band': 4, 'gain': 0.0}, {'band': 5, 'gain': -0.05},
            {'band': 6, 'gain': -0.1}, {'band': 7, 'gain': -0.1}, {'band': 8, 'gain': -0.1},
            {'band': 9, 'gain': -0.1}, {'band': 10, 'gain': -0.1}, {'band': 11, 'gain': -0.1},
            {'band': 12, 'gain': -0.1}, {'band': 13, 'gain': -0.1}, {'band': 14, 'gain': -0.1}
        ])
        await player.set_filters(filters)

        embed = discord.Embed(
            title='Changed Filter',
            color=helpers.Colour.white(),
            description='*It may takes a while for the changes to apply.*')
        image = self.render.generate_eq_image([entry['gain'] for entry in filters.equalizer.payload.values()])
        embed.set_image(url='attachment://image.png')
        embed.set_footer(text=f'Requested by: {ctx.author}')
        await ctx.send(embed=embed, file=image, delete_after=20)

    @filter.command(
        name='nightcore',
        description='Enables/Disables the nightcore filter.'
    )
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def filter_nightcore(self, ctx: Context) -> None:
        """Apply a Nightcore Filter to the current track."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        filters: wavelink.Filters = player.filters
        filters.timescale.set(speed=1.25, pitch=1.3, rate=1.3)
        await player.set_filters(filters)

        await ctx.send_success('Applied Nightcore Filter.', delete_after=10)

    @filter.command(
        '8d',
        description='Enable/Disable the 8d filter.'
    )
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def filter_8d(self, ctx: Context) -> None:
        """Apply an 8D Filter to create a 3D effect."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        filters: wavelink.Filters = player.filters
        filters.rotation.set(rotation_hz=0.15)
        await player.set_filters(filters)

        await ctx.send_success('Applied 8D Filter.', delete_after=10)

    @filter.command(
        'lowpass',
        description='Suppresses higher frequencies while allowing lower frequencies to pass through.'
    )
    @describe(smoothing='The smoothing of the lowpass filter. (2.5-50.0)')
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def filter_lowpass(self, ctx: Context, smoothing: app_commands.Range[float, 2.5, 50.0]) -> None:
        """Apply a Lowpass Filter to the current Track."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        filters: wavelink.Filters = player.filters
        filters.low_pass.set(smoothing=smoothing)
        await player.set_filters(filters)

        await ctx.send_success(f'Set Lowpass Filter to **{smoothing}**.', delete_after=10)

    @filter.command(
        'reset',
        description='Reset all active filters.'
    )
    @checks.is_author_connected()
    @checks.is_player_playing()
    async def filter_reset(self, ctx: Context) -> None:
        """Reset all active filters."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        player.filters.reset()
        await player.set_filters()
        await ctx.send_success('Removed all active filters.', delete_after=10)

    @command(description='Skip the playing song to the next.', hybrid=True, guild_only=True)
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def forceskip(self, ctx: Context) -> None:
        """Skip the playing song."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        if player.queue.is_empty:
            await ctx.send_error('The queue is empty.')
            return

        await player.skip(force=True)
        await ctx.send_success('An admin or DJ has to the next track.', delete_after=10)

    @command('jump-to', description='Jump to a track in the Queue.', hybrid=True, guild_only=True)
    @describe(position='The index of the track you want to jump to.')
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def jump_to(self, ctx: Context, position: int) -> None:
        """Jump to a track in the Queue.
        Note: The number you enter is the count of how many tracks in the queue will be skipped."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        if player.queue.all_is_empty:
            await ctx.send_error('The queue is empty.')
            return

        if position < 0:
            await ctx.send_error('The index must be greater than or 0.')
            return

        if (position - 1) > len(player.queue.all):
            await ctx.send_error('There are not that many tracks in the queue.')
            return

        success = await player.jump_to(position - 1)
        if not success:
            await ctx.send_error('Failed to jump to the specified track.')
            return

        await player.stop()

        if position != 1:
            await ctx.send_success(f'Playing the **{position}** track in queue.', delete_after=10)
        else:
            await ctx.send_success('Playing the next track in queue.', delete_after=10)

    @command(description='Plays the previous Track.', hybrid=True, guild_only=True)
    @checks.is_author_connected()
    @checks.is_player_playing()
    @checks.is_listen_together()
    async def back(self, ctx: Context) -> None:
        """Plays the previous Track."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        if player.queue.history.is_empty:
            await ctx.send_error('There are no tracks in the history.')
            return

        await player.back()
        await ctx.send_success('An admin or DJ has skipped to the previous song.', delete_after=10)

    @command(description='Display the active queue.', hybrid=True, guild_only=True)
    async def queue(self, ctx: Context) -> None:
        """Display the active queue."""
        player: Player = cast(Player, ctx.voice_client)
        if not player:
            return

        if player.queue.all_is_empty:
            await ctx.send_error('No items currently in the queue.', ephemeral=True)
            return

        await ctx.defer()

        class QueuePaginator(BasePaginator):
            @staticmethod
            def fmt(track: wavelink.Playable, index: int) -> str:
                return (
                    f'`[ {index}. ]` [{track.title}]({track.uri}) by **{track.author or 'Unknown'}** '
                    f'[`{convert_duration(track.length)}`]'
                )

            async def format_page(self, entries: list, /) -> discord.Embed:
                embed = discord.Embed(color=helpers.Colour.white())
                embed.set_author(name=f'{ctx.guild.name}\'s Current Queue', icon_url=ctx.guild.icon.url)

                embed.description = (
                    '**╔ Now Playing:**\n'
                    f'[{player.current.title}]({player.current.uri}) by **{player.current.author or 'Unknown'}** '
                    f'[`{convert_duration(player.current.length)}`]\n\n'
                )

                tracks = '\n'.join(
                    self.fmt(track, i) for i, track in enumerate(entries, (self._current_page * self.per_page) + 1)
                ) if not isinstance(entries[0], str) else (
                    '*It seems like there are currently not upcomming tracks.*\n'
                    'Add one with </play:1207828024037216283>.'
                )

                embed.description += '**╠ Up Next:**\n' + tracks

                embed.add_field(
                    name='╚ Settings:',
                    value=f'DJ(s): {', '.join([x.mention for x in player.djs])}', inline=False)
                embed.set_footer(text=f'Total: {len(player.queue.all)} • History: {len(player.queue.history) - 1}')
                return embed

        await QueuePaginator.start(ctx, entries=list(player.queue) or ['PLACEHOLDER'], per_page=30)

    # Lyrics Stuff

    @classmethod
    def _get_text(cls, element: Tag | PageElement) -> str:
        """Recursively parse an element and its children into a markdown string."""
        if isinstance(element, NavigableString):
            return element.strip()
        elif element.name == 'br':
            return '\n'
        else:
            return ''.join(cls._get_text(child) for child in element.contents)

    @classmethod
    def _extract_lyrics(cls, html: str) -> str | None:
        """Extract lyrics from the provided HTML."""
        soup = BeautifulSoup(html, 'html.parser')

        lyrics_container = soup.find_all('div', {'data-lyrics-container': 'true'})

        if not lyrics_container:
            return None

        text_parts = []
        for part in lyrics_container:
            text_parts.append(cls._get_text(part))

        return '\n'.join(text_parts)

    @command(description='Search for some lyrics.', hybrid=True, guild_only=True)
    @describe(song='The song you want to search for.')
    @commands.guild_only()
    async def lyrics(self, ctx: Context, *, song: str | None = None) -> None:
        """Search for some lyrics."""
        await ctx.defer(ephemeral=True)

        player: Player = cast(Player, ctx.voice_client)
        if not player and not song:
            await ctx.send_error('Please provide a song to search for.')
            return

        song = song or player.current.title

        async with ctx.channel.typing():
            headers = {
                'Accept': 'application/json',
                'Authorization': f'Bearer {genius_key}'
            }

            async with self.bot.session.get(
                    'https://api.genius.com/search', headers=headers, params={
                        'q': song.replace('by', '').replace('from', '').strip()
                    }
            ) as resp:
                if resp.status != 200:
                    await ctx.send_error(f'{Emojis.error} I cannot find lyrics for the current track.')
                    return

                data = (await resp.json())['response']['hits'][0]['result']
                song_url = urljoin('https://genius.com', data['path'])

            async with self.bot.session.get(song_url) as res:
                if res.status != 200:
                    await ctx.send_error(f'{Emojis.error} I cannot find lyrics for the current track.')
                    return

                html = await res.text()

            lyrics_data = self._extract_lyrics(html)

            if lyrics_data is None:
                await ctx.send_error(f'{Emojis.error} I cannot find lyrics for the current track.')
                return

            mapped = list(pagify(lyrics_data, page_length=4096))

        class TextPaginator(BasePaginator):
            async def format_page(self, entries: list, /) -> discord.Embed:
                embed = discord.Embed(title=data['full_title'],
                                      url=song_url,
                                      description=entries[0],
                                      colour=helpers.Colour.white())
                embed.set_thumbnail(url=data['header_image_url'])
                return embed

        await TextPaginator.start(ctx, entries=mapped, per_page=1, ephemeral=True)

    # DJ

    @group(
        'dj',
        description='Manage the DJ role.',
        guild_only=True,
        hybrid=True
    )
    async def _dj(self, ctx: Context) -> None:
        """Manage the DJ Role."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @_dj.command(
        'add',
        description='Adds the DJ Role with which you have extended control rights to a member.',
        bot_permissions=['manage_roles'],
        user_permissions=['manage_roles']
    )
    @describe(member='The member you want to add the DJ Role to.')
    async def dj_add(self, ctx: Context, member: discord.Member) -> None:
        """Adds the DJ Role with which you have extended control rights to a member.

        Note: The DJ role is used to give extended control rights to a member.

        If no DJ role exists, the bot will create a new one.
        """
        dj_role = discord.utils.get(ctx.guild.roles, name='DJ')
        if dj_role is None:
            dj_role = await ctx.guild.create_role(name='DJ')
            await ctx.send_success(f'Created new DJ Role `{dj_role.mention}`.')

        if dj_role in member.roles:
            await ctx.send_error(f'{member} already has the DJ role.')
            return

        if dj_role.position >= ctx.guild.me.top_role.position:
            await ctx.send_error('The DJ role is higher than my top role.')
            return

        if dj_role.position >= ctx.author.top_role.position:
            await ctx.send_error('The DJ role is higher than your top role.')
            return

        await member.add_roles(dj_role)
        await ctx.send_success(f'Added the {dj_role.mention} role to user {member}.')

    @_dj.command(
        'remove',
        description='Removes the DJ Role with which you have extended control rights from a member.',
        bot_permissions=['manage_roles'],
        user_permissions=['manage_roles']
    )
    @describe(member='The member you want to remove the DJ Role from.')
    async def dj_remove(self, ctx: Context, member: discord.Member) -> None:
        """Removes the DJ Role with which you have extended control rights from a member."""
        dj_role = discord.utils.get(ctx.guild.roles, name='DJ')
        if not dj_role:
            await ctx.send_error('There is currently no existing DJ role.')
            return

        if dj_role not in member.roles:
            await ctx.send_error(f'**{member}** has not the DJ role.')
            return

        if dj_role.position >= ctx.guild.me.top_role.position:
            await ctx.send_error('The DJ role is higher than my top role.')
            return

        if dj_role.position >= ctx.author.top_role.position:
            await ctx.send_error('The DJ role is higher than your top role.')
            return

        await member.remove_roles(dj_role)
        await ctx.send_success(f'Removed the {dj_role.mention} role from user {member.mention}.')

    # SETUP

    @group(
        'music',
        description='Manage the Music Configuration.',
        guild_only=True,
        hybrid=True,
    )
    async def _music(self, ctx: Context) -> None:
        """Manage the Music Configuration."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @_music.command(
        'setup',
        description='Start the Music configuration setup.',
        bot_permissions=['manage_channels'],
        user_permissions=['manage_channels'],
    )
    @describe(channel='The channel you want to set as the music player channel.')
    async def music_setup(self, ctx: Context, channel: discord.TextChannel | None = None) -> None:
        """Sets up a new music player channel.
        If you don't provide a channel, the bot will create a new channel in the category where the command was executed.
        """
        await ctx.defer()

        config = await self.bot.db.get_guild_config(ctx.guild.id)
        if config.music_panel_channel_id and config.music_panel_message_id:
            await ctx.send_error('There is already a music configuration setup.')
            return

        if not channel:
            parent = ctx.channel.category or ctx.guild
            channel = await parent.create_text_channel(name='🎶percy-music')

        await channel.edit(
            slowmode_delay=3,
            topic=DEFAULT_CHANNEL_DESCRIPTION.format(bot=self.bot.user.mention))

        await ctx.send_success(f'Successfully set the new player channel to {channel.mention}.')

        message = await channel.send(embed=Player.preview_embed(ctx.guild))
        await message.pin()
        await channel.purge(limit=5, check=lambda msg: not msg.pinned)

        await config.update(music_panel_channel_id=channel.id, music_panel_message_id=message.id)

    @_music.command(
        'reset',
        description='Reset the Music configuration setup.',
        user_permissions=['manage_channels'],
    )
    async def setup_reset(self, ctx: Context) -> None:
        """Reset the Music configuration setup."""
        config = await self.bot.db.get_guild_config(ctx.guild.id)
        if not config.music_panel_channel_id or not config.music_panel_message_id:
            await ctx.send_error('There is currently no music configuration.')
            return

        await config.update(music_panel_channel_id=None, music_panel_message_id=None)
        await ctx.send_success('The Music Configuration for this Guild has been deleted.', ephemeral=True)

    @_music.command(
        'panel',
        description='Toggle the use of the Music Panel.',
        user_permissions=['manage_channels'],
    )
    async def setup_panel(self, ctx: Context) -> None:
        """This toggles the use of the music panel.
        If disabled, the bot won't send a player panel regardless of the setup."""
        config = await self.bot.db.get_guild_config(ctx.guild.id)
        if not config.music_panel_channel_id or not config.music_panel_message_id:
            await ctx.send_error('There is currently no music configuration.')
            return

        await config.update(use_music_panel=not config.use_music_panel)
        await ctx.send_success(f'The Music Panel has been {'enabled' if not config.use_music_panel else 'disabled'}.')

    @Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Handle Auto-Delete for messages in a given music player channel."""
        await self.bot.wait_until_ready()
        if message.guild is None:
            return

        config = await self.bot.db.get_guild_config(message.guild.id)
        if not (config.music_panel_channel_id and config.music_panel_message_id):
            return

        if (
                message.channel.id == config.music_panel_channel_id
                and not message.pinned
                and message.id != config.music_panel_message_id
        ):
            await message.delete(delay=60)
