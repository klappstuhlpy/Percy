from __future__ import annotations

import datetime
import enum
import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Generic, Self, TypeVar

import discord
import wavelink
import yarl
from discord.ext import commands
from discord.utils import MISSING
from wavelink import QueueMode

from app.cogs.music._queue import Queue, ShuffleMode
from app.core import Bot, Context, View
from app.utils import PlayerStamp, ProgressBar, convert_duration, helpers, truncate, letter_emoji
from config import Emojis

if TYPE_CHECKING:
    from discord.abc import Connectable

    from app.core.models import CogT
    from app.database import GuildConfig

log = logging.getLogger(__name__)
T = TypeVar('T')


def is_dj(member: discord.Member) -> bool:
    """Checks if the Member has the DJ Role."""
    role = discord.utils.get(member.guild.roles, name='DJ')
    return role in member.roles


EMOJI_KEYS = {
    'shuffle': {
        ShuffleMode.on: '<:shuffleTrue:1322338138932248667>',
        ShuffleMode.off: '<:shuffleNone:1322338127511293962>'
    },
    'pause_play': {
        True: '⏸️',
        False: '▶️'
    },
    'loop': {
        QueueMode.loop: '<:repeatOne:1322338199472701451>',
        QueueMode.loop_all: '<:repeatAll:1322338180191621180>',
        QueueMode.normal: '<:repeatNone:1322338189998030952>'
    },
    'like': '<:liked:1322338435238858883>',
    # Music Sources
    'youtube': '<:youTube:1322362145865728020>',
    'spotify': '<:spotify:1322362153474330646>',
    'soundcloud': '<:soundcloud:1322362137993023519>',
}


class PlayerState(enum.Enum):
    PLAYING = 1
    PAUSED = 2
    STOPPED = 3


class SearchReturn(enum.Enum):
    NO_RESULTS = 1
    CANCELLED = 2
    NO_YOUTUBE_ALLOWED = 3


class Player(wavelink.Player):
    """Custom mdded-wavelink Player class."""

    def __init__(self, client: discord.Client = MISSING, channel: Connectable = MISSING) -> None:
        super().__init__(client, channel)

        self.panel: PlayerPanel = MISSING
        self.queue: Queue = Queue()

    @property
    def djs(self) -> list[discord.Member]:
        """Returns a list of all DJ's in the voice channel."""
        djs: list[discord.Member] = [member for member in self.channel.members if is_dj(member)]
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
    ) -> wavelink.Playable | wavelink.Playlist | SearchReturn:
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
                if 'youtube' in query.casefold():
                    return SearchReturn.NO_YOUTUBE_ALLOWED

                results = await wavelink.Playable.search(query)
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
        channel = obj.user.voice.channel if obj.user.voice else None
        if not channel:
            raise commands.BadArgument('You need to be in a voice channel or provide one to connect to.')

        self = await channel.connect(cls=cls, self_deaf=True)
        await obj.guild.me.edit(suppress=False if isinstance(channel, discord.StageChannel) else MISSING, deafen=True)

        disabled: bool = False
        config: GuildConfig = await obj.client.db.get_guild_config(obj.guild_id)
        if config and not config.use_music_panel:
            disabled = True

        self.panel = await PlayerPanel.start(self, channel=obj.channel, disabled=disabled)
        return self

    async def disconnect(self, **kwargs: Any) -> None:
        """Disconnects the player from the voice channel."""
        if self.panel is not MISSING:
            if self.panel.state != PlayerState.STOPPED:
                await self.panel.stop()

            if self.panel.__is_temporary__:
                with suppress(discord.HTTPException):
                    await self.panel.msg.delete()

        if len(self.channel.members) == 1 and isinstance(self.channel, discord.StageChannel):
            with suppress(discord.HTTPException):
                await self.channel.instance.delete()

        await super().disconnect(**kwargs)

    async def cleanupleft(self) -> None:
        """Removes all tracks from the queue that are not in the voice channel."""
        for track in self.queue.all:  # type: wavelink.Playable
            if not hasattr(track.extras, 'requester'):
                continue
            if track.extras.requester_id not in self.channel.members:
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
            embed.set_footer(text=f'Requested by {obj.user}', icon_url=obj.user.display_avatar.url)

        if isinstance(obj, Context):
            return await obj.send(embed=embed, delete_after=15)
        else:
            if obj and obj.response.is_done():
                return await obj.followup.send(embed=embed, delete_after=15)
            else:
                return await obj.response.send_message(embed=embed, delete_after=15)

    @classmethod
    def preview_embed(cls, guild: discord.Guild) -> discord.Embed:
        embed = discord.Embed(
            title='Music Player Panel',
            description='The control panel was closed, the queue is currently empty and I got nothing to do.\n'
                        'You can start a new player session by invoking the </play:1070054930125176923> command.\n\n'
                        '*Once you play a new track, this message is going to be the new player panel if it\'s not deleted, '
                        'otherwise I\'m going to create a new panel.*',
            timestamp=discord.utils.utcnow(),
            color=helpers.Colour.white())
        embed.set_footer(text='last updated')
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        return embed


class PlayerPanel(View):
    """The Main Class for a Player Panel.

    Attributes
    ----------
    bot: :class:`Bot`
        The bot instance.
    player: :class:`Player`
        The player that this panel is for.
    state: :class:`PlayerState`
        The state of the player.
    msg: :class:`discord.Message`
        The message of the panel.
    channel: :class:`discord.TextChannel`
        The channel where the panel is sent.
    cooldown: :class:`commands.CooldownMapping`
        The cooldown mapping for the panel.
    """

    if TYPE_CHECKING:
        bot: Bot
        player: Player
        state: PlayerState
        msg: discord.Message
        channel: discord.TextChannel
        cooldown: commands.CooldownMapping

    def __init__(self, *, player: Player, state: PlayerState, disabled: bool) -> None:
        super().__init__(timeout=None)
        self.bot = player.client

        self.player: Player = player
        self.state: PlayerState = state
        self._disabled = disabled

        self.msg: discord.Message = MISSING
        self.channel: discord.TextChannel = MISSING

        self.cooldown = commands.CooldownMapping.from_cooldown(3, 5, lambda ctx: ctx.user)
        self.__is_temporary__: bool = False

        self.update_buttons()

    def build_embed(self) -> discord.Embed:
        """Builds the embed for the panel."""
        embed = discord.Embed(
            title='Music Player Panel',
            timestamp=discord.utils.utcnow(),
            color=helpers.Colour.white()
        )

        if self.state == PlayerState.PLAYING:
            embed.description = (
                'This is the Bot\'s control panel where you can easily perform actions '
                'of the bot without using a command.'
            )

            track = self.player.current
            artist = f'[{track.author}]({track.artist.url})' if track.artist.url else track.author

            embed.add_field(
                name='╔ Now Playing:',
                value=f'╠ **Track:** [{track.title}]({track.uri})\n'
                      f'╠ **Artist:** {artist}\n'
                      f'╠ **Bound to:** {self.player.channel.mention}\n'
                      f'╠ **Position in Queue:** {self.player.queue.all.index(self.player.current) + 1}/{len(self.player.queue.all)}',
                inline=False
            )

            if track.album and track.album.name:
                embed.add_field(
                    name='╠ Album:',
                    value=f'[{track.album.name}]({track.album.url})' if track.album.url else track.album.name,
                    inline=False
                )
            if track.playlist:
                embed.add_field(
                    name='╠ Playlist:',
                    value=f'[{track.playlist.name}]({track.playlist.url})' if track.playlist.url else track.playlist.name,
                    inline=False
                )
            if self.player.queue.listen_together is not MISSING:
                user = self.player.guild.get_member(self.player.queue.listen_together)
                embed.add_field(name='╠ Listening-together with:', value=f'{user.mention}\'s Spotify', inline=False)

            embed.add_field(
                name='╠ Status:',
                value=f'```swift\n{PlayerStamp(track.length, self.player.position)}```'
                if not track.is_stream else '```swift\n[ 🔴 LIVE STREAM ]```',
                inline=False)

            loop_mode = self.player.queue.mode.name.replace('_', ' ').upper()
            embed.add_field(name='╠ Loop Mode:', value=f'`{loop_mode}`')
            embed.add_field(
                name='═ Shuffle Mode:',
                value={
                    ShuffleMode.off: '<:off1:1322338488443736257> **``Off``**',
                    ShuffleMode.on: '<:on1:1322338500300771458> **``On``**'
                }.get(self.player.queue.shuffle))
            embed.add_field(
                name='╠ Volume:',
                value=f'```swift\n{ProgressBar(0, 100, self.player.volume)} [ {self.player.volume}% ]```',
                inline=False)

            if track.recommended:
                embed.add_field(name='╠ Recommended via:',
                                value=f'{EMOJI_KEYS[track.source]} **`{track.source.title()}`**',
                                inline=False)

            if not self.player.queue.is_empty and (upcomming := self.player.queue.peek(0)):
                eta = discord.utils.utcnow() + datetime.timedelta(
                    milliseconds=(self.player.current.length - self.player.position))
                embed.add_field(name='╠ Next Track:',
                                value=f'[{upcomming.title}]({upcomming.uri}) {discord.utils.format_dt(eta, 'R')}')

            if artwork := self.player.current.artwork:
                embed.set_thumbnail(url=artwork)

            # Add '╚' to the last field's name
            field = getattr(embed, '_fields', [])[-1]
            field['name'] = '╚ ' + field['name'][1:]

            embed.set_footer(text=f'{'Auto-Playing' if self.player.autoplay != 2 else 'Manual-Playing'} • last updated')
        else:
            embed.description = (
                'The control panel was closed, the queue is currently empty and I got nothing to do.\n'
                'You can start a new player session by invoking the </play:1070054930125176923> command.\n\n'
                '*Once you play a new track, this message is going to be the new player panel if it\'s not deleted, '
                'otherwise I\'m going to create a new panel.*'
            )
            embed.set_footer(text='last updated')
            if self.player.guild.icon:
                embed.set_thumbnail(url=self.player.guild.icon.url)

        return embed

    def disabled_state(self, check: bool | None = None) -> bool:
        """Returns True if the button should be disabled.

        Parameters
        ----------
        check: bool
            The check to use.

        Returns
        -------
        bool
            Whether the button should be disabled.
        """
        return check or bool(self.state == PlayerState.STOPPED) or self.player.queue.all_is_empty

    def update_buttons(self) -> None:
        """Updates the buttons of the panel."""
        button_updates: list[tuple[discord.Button, bool, str | None]] = [
            (self.on_shuffle, self.disabled_state(), EMOJI_KEYS['shuffle'][self.player.queue.shuffle]),
            (self.on_back, self.disabled_state(self.player.queue.history_is_empty), None),
            (self.on_pause_play, self.disabled_state(), EMOJI_KEYS['pause_play'][
                bool(not self.player.paused and self.player.playing)]),
            (self.on_forward, self.disabled_state(self.player.queue.is_empty), None),
            (self.on_loop, self.disabled_state(), EMOJI_KEYS['loop'][self.player.queue.mode]),
            (self.on_stop, self.disabled_state(), None),
            (self.on_volume, self.disabled_state(), None),
            (self.on_like, self.disabled_state(), None)
        ]

        for button, disabled, emoji in button_updates:
            button.disabled = disabled
            if emoji is not None:
                button.emoji = emoji

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        assert isinstance(interaction.user, discord.Member)

        author_vc = interaction.user.voice and interaction.user.voice.channel
        bot_vc = interaction.guild.me.voice and interaction.guild.me.voice.channel

        if retry_after := self.cooldown.update_rate_limit(interaction):
            await interaction.response.send_message(
                f'{Emojis.error} You are being rate limited. Try again in **{retry_after:.2f}** seconds.',
                ephemeral=True)
            return False

        if is_dj(interaction.user) and bot_vc and not author_vc:
            return True

        if author_vc and bot_vc and (author_vc == bot_vc) and (
                interaction.user.voice.deaf or interaction.user.voice.self_deaf):
            await interaction.response.send_message(
                f'{Emojis.error} You are deafened, please undeafen yourself to use this menu.',
                ephemeral=True)
            return False
        elif (not author_vc and bot_vc) or (author_vc and bot_vc and author_vc != bot_vc):
            await interaction.response.send_message(
                f'{Emojis.error} You must be in {bot_vc.mention} to use this menu.',
                ephemeral=True)
            return False
        elif not author_vc:
            await interaction.response.send_message(
                f'{Emojis.error} You must be in a voice channel to use this menu.',
                ephemeral=True)
            return False

        return True

    async def fetch_player_channel(
            self, channel: discord.TextChannel | None = None
    ) -> discord.TextChannel | None:
        """|coro|

        Gets the channel where the player is currently playing.

        Parameters
        ----------
        channel: :class:`discord.TextChannel`
            The channel to use if no channel is set in the guild configuration.

        Returns
        -------
        :class:`discord.TextChannel`
            The channel where the player is currently playing.
        """
        config = await self.bot.db.get_guild_config(self.player.guild.id)
        if not channel and not config.music_panel_channel:
            raise ValueError('No channel provided and no music channel set in the guild configuration.')

        self.channel = config.music_panel_channel or channel
        self.__is_temporary__ = not config.music_panel_channel
        return self.channel

    async def get_player_message(self) -> discord.Message | None:
        """|coro|

        Gets the message of the current plugin's control panel.

        Returns
        -------
        :class:`discord.Message`
            The message of the panel.
        """
        config = await self.bot.db.get_guild_config(self.player.guild.id)
        message_id = config.music_panel_message_id

        if self.channel is MISSING:
            await self.fetch_player_channel()

        if self.msg is MISSING and message_id:
            try:
                self.msg = await self.channel.fetch_message(message_id)
            except discord.NotFound:
                return

        return self.msg

    async def update(self, state: PlayerState = PlayerState.PLAYING) -> discord.Message | None:
        """|coro|

        Updates the panel with the current state of the player.

        Parameters
        ----------
        state: :class:`PlayerState`
            The state of the player.

        Returns
        -------
        :class:`discord.Message`
            The message of the panel.
        """
        if self._disabled:
            return

        if self.state != state:
            self.state = state

        self.update_buttons()

        if self.msg is MISSING and not self.__is_temporary__:
            await self.get_player_message()

        if self.msg is not MISSING:
            await self.msg.edit(embed=self.build_embed(), view=self)
        else:
            self.msg = await self.channel.send(embed=self.build_embed(), view=self)

        return self.msg

    async def stop(self) -> None:
        """Stops the player and resets the queue."""
        self.player.queue.reset()
        await self.update(PlayerState.STOPPED)

        super().stop()

    @discord.ui.button(
        style=discord.ButtonStyle.grey,
        emoji=EMOJI_KEYS['shuffle'][ShuffleMode.off],
        disabled=True
    )
    async def on_shuffle(self, interaction: discord.Interaction, _) -> None:
        TOGGLE = {
            ShuffleMode.off: ShuffleMode.on,
            ShuffleMode.on: ShuffleMode.off
        }
        self.player.queue.shuffle = TOGGLE.get(self.player.queue.shuffle)

        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(
        style=discord.ButtonStyle.blurple,
        emoji='⏮️',
        disabled=True
    )
    async def on_back(self, interaction: discord.Interaction, _) -> None:
        await interaction.response.edit_message(view=self)
        await self.player.back()

    @discord.ui.button(
        style=discord.ButtonStyle.blurple,
        emoji=EMOJI_KEYS['pause_play'][False],
        disabled=True
    )
    async def on_pause_play(self, interaction: discord.Interaction, _) -> None:
        await self.player.pause(not self.player.paused)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(
        style=discord.ButtonStyle.blurple,
        emoji='⏭',
        disabled=True
    )
    async def on_forward(self, interaction: discord.Interaction, _) -> None:
        await interaction.response.edit_message(view=self)
        await self.player.skip()

    @discord.ui.button(
        style=discord.ButtonStyle.grey,
        emoji=EMOJI_KEYS['loop'][QueueMode.normal],
        disabled=True
    )
    async def on_loop(self, interaction: discord.Interaction, _) -> None:
        TRANSITIONS = {
            QueueMode.normal: QueueMode.loop,
            QueueMode.loop: QueueMode.loop_all,
            QueueMode.loop_all: QueueMode.normal,
        }
        self.player.queue.mode = TRANSITIONS.get(self.player.queue.mode)

        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(
        style=discord.ButtonStyle.red,
        emoji='⏹️',
        label='Stop',
        disabled=True
    )
    async def on_stop(self, interaction: discord.Interaction, _) -> None:
        await self.player.disconnect()
        await interaction.response.send_message(f'{Emojis.success} Stopped Track and cleaned up queue.',
                                                delete_after=10)

    @discord.ui.button(
        style=discord.ButtonStyle.grey,
        emoji='🔊',
        label='Adjust Volume',
        disabled=True
    )
    async def on_volume(self, interaction: discord.Interaction, _) -> None:
        await interaction.response.send_modal(AdjustVolumeModal(self))

    @discord.ui.button(
        style=discord.ButtonStyle.green,
        emoji=EMOJI_KEYS['like'],
        disabled=True
    )
    async def on_like(self, interaction: discord.Interaction, _) -> None:
        playlist_tools: CogT = self.bot.get_cog('PlaylistTools')
        if not playlist_tools:
            return await interaction.response.send_message('This feature is currently disabled.', ephemeral=True)

        liked_songs = await playlist_tools.get_liked_songs(interaction.user.id)

        if not liked_songs:
            await playlist_tools.initizalize_user(interaction.user)

        if self.player.current.uri not in liked_songs:
            await liked_songs.add_track(self.player.current)
            await interaction.response.send_message(
                f'{Emojis.success} Added `{self.player.current.title}` to your liked songs.',
                ephemeral=True)
        else:
            await liked_songs.remove_track(discord.utils.get(liked_songs.tracks, url=self.player.current.uri))
            await interaction.response.send_message(
                f'{Emojis.success} Removed `{self.player.current.title}` from your liked songs.',
                ephemeral=True)

        playlist_tools.get_playlists.invalidate(playlist_tools, interaction.user.id)

    @classmethod
    async def start(
            cls: type[PlayerPanel],
            player: Player,
            *,
            channel: discord.TextChannel,
            disabled: bool,
            state: PlayerState = PlayerState.STOPPED,
    ) -> PlayerPanel:
        """|coro|

        Used to start the paginator.

        Parameters
        ----------
        player: :class:`Player`
            The player to use for the panel.
        channel: :class:`discord.TextChannel`
            The channel to send the panel to. Only used if no configuration for the guild is found.
        disabled: bool
            Whether the panel should be disabled.
        state: :class:`PlayerState`
            The state of the player.

        Returns
        -------
        :class:`PlayerPanel`
            The paginator object.
        """
        self = cls(player=player, state=state, disabled=disabled)

        await self.fetch_player_channel(channel)

        self.msg = await self.update(state=state)
        return self


class AdjustVolumeModal(discord.ui.Modal, title='Volume Adjuster'):
    """Modal that prompts users for the volume to change to."""
    number = discord.ui.TextInput(
        label='Volume - %', style=discord.TextStyle.short, placeholder='Enter a Number between 1 and 100',
        min_length=1, max_length=3)

    def __init__(self, _view: PlayerPanel, /) -> None:
        super().__init__(timeout=30)
        self._view: PlayerPanel = _view

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        if not self.number.value.isdigit():
            return await interaction.response.send_message('Please enter a valid number.', ephemeral=True)

        value = int(self.number.value)
        await self._view.player.set_volume(value)
        return await interaction.response.edit_message(embed=self._view.build_embed(), view=self._view)


class TrackDisambiguatorView(View, Generic[T]):
    message: discord.Message
    selected: T

    def __init__(self, ctx: Context | discord.Interaction, tracks: list[T]) -> None:
        super().__init__(timeout=100.0, members=ctx.user)
        self.ctx = ctx
        self.tracks = tracks
        self.value = None

        # Use list comprehension for creating options
        options = [
            discord.SelectOption(
                label=truncate(x.title, 100),
                description='by ' + truncate(discord.utils.remove_markdown(x.author), 100),
                emoji=letter_emoji(i),
                value=str(i)
            )
            for i, x in enumerate(tracks)
        ]

        select = discord.ui.Select(options=options)
        select.callback = self.on_select_submit
        self.select = select
        self.add_item(select)

    async def on_select_submit(self, _) -> None:
        index = int(self.select.values[0])
        self.selected = self.tracks[index]
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red, row=1)
    async def cancel(self, _, __) -> None:
        self.selected = None
        self.stop()

    @classmethod
    async def start(
            cls: type[TrackDisambiguatorView],
            context: Context | discord.Interaction,
            *,
            tracks: list[T]
    ) -> T | None:
        """|coro|

        Used to start the disambiguator."""
        tracks = tracks[:5]

        if len(tracks) == 1:
            return tracks[0]
        if len(tracks) == 0:
            return None

        self = cls(context, tracks=tracks)

        description = '\n'.join(
            f'{letter_emoji(i)} [{track.title}]({track.uri}) by **{track.author}** | `{convert_duration(track.length)}`'
            for i, track in enumerate(tracks)
        )

        embed = discord.Embed(
            title='Choose a Track',
            description=description,
            timestamp=datetime.datetime.now(datetime.UTC),
            color=helpers.Colour.white())
        embed.set_footer(text=context.user, icon_url=context.user.display_avatar.url)

        self.message = await context.send(embed=embed, view=self)

        await self.wait()
        with suppress(discord.HTTPException):
            await self.message.delete()

        return self.selected
