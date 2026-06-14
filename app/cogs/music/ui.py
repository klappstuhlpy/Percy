from __future__ import annotations

import datetime
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands
from discord.utils import MISSING
from wavelink import QueueMode

from app.core import Context, LayoutView
from app.core.pagination import BasePaginator
from app.utils import (
    PlayerStamp,
    ProgressBar,
    convert_duration,
    helpers,
    letter_emoji,
    pluralize,
    truncate,
)
from config import Emojis

from .models import PlayerState, Playlist, ShuffleMode, is_dj

if TYPE_CHECKING:
    from app.core import Bot

    from .player import Player

EMOJI_KEYS: dict[str, dict[ShuffleMode, str] | dict[bool, str] | dict[QueueMode, str] | str] = {
    "shuffle": {ShuffleMode.on: "<:shuffleTrue:1322338138932248667>", ShuffleMode.off: "<:shuffleNone:1322338127511293962>"},
    "pause_play": {True: "⏸️", False: "▶️"},
    "loop": {
        QueueMode.loop: "<:repeatOne:1322338199472701451>",
        QueueMode.loop_all: "<:repeatAll:1322338180191621180>",
        QueueMode.normal: "<:repeatNone:1322338189998030952>",
    },
    "like": "<:liked:1322338435238858883>",
    # Music Sources
    "youtube": "<:youTube:1322362145865728020>",
    "spotify": "<:spotify:1322362153474330646>",
    "soundcloud": "<:soundcloud:1322362137993023519>",
}


class PlayerPanel(LayoutView):
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
        self.bot: Bot = player.client  # type: ignore

        self.player: Player = player
        self.state: PlayerState = state
        self._disabled = disabled

        self.msg: discord.Message = MISSING
        self.channel: discord.TextChannel = MISSING

        self.cooldown = commands.CooldownMapping.from_cooldown(3, 5, lambda ctx: ctx.user)
        self.__is_temporary__: bool = False

        # -- control buttons (stable instances, mutated by update_buttons) --
        self.on_shuffle = discord.ui.Button(
            style=discord.ButtonStyle.grey, emoji=EMOJI_KEYS["shuffle"][ShuffleMode.off], disabled=True
        )
        self.on_shuffle.callback = self._on_shuffle
        self.on_back = discord.ui.Button(style=discord.ButtonStyle.blurple, emoji="⏮️", disabled=True)
        self.on_back.callback = self._on_back
        self.on_pause_play = discord.ui.Button(
            style=discord.ButtonStyle.blurple, emoji=EMOJI_KEYS["pause_play"][False], disabled=True
        )
        self.on_pause_play.callback = self._on_pause_play
        self.on_forward = discord.ui.Button(style=discord.ButtonStyle.blurple, emoji="⏭", disabled=True)
        self.on_forward.callback = self._on_forward
        self.on_loop = discord.ui.Button(
            style=discord.ButtonStyle.grey, emoji=EMOJI_KEYS["loop"][QueueMode.normal], disabled=True
        )
        self.on_loop.callback = self._on_loop
        self.on_stop = discord.ui.Button(style=discord.ButtonStyle.red, emoji="⏹️", label="Stop", disabled=True)
        self.on_stop.callback = self._on_stop
        self.on_volume = discord.ui.Button(
            style=discord.ButtonStyle.grey, emoji="🔊", label="Adjust Volume", disabled=True
        )
        self.on_volume.callback = self._on_volume
        self.on_like = discord.ui.Button(style=discord.ButtonStyle.green, emoji=EMOJI_KEYS["like"], disabled=True)
        self.on_like.callback = self._on_like

        self.update_buttons()

    def _rebuild(self) -> None:
        """Recompose the layout: a fresh now-playing card plus the (mutated) control rows.

        The card is rebuilt every refresh because it shows live data (position, volume,
        queue), while the button instances are stable and only have their disabled/emoji
        state mutated by :meth:`update_buttons`.
        """
        self.update_buttons()
        self.clear_items()
        self.add_item(self.build_container())

    def build_container(self) -> discord.ui.Container:
        """Builds the Components V2 now-playing card for the panel."""
        container = discord.ui.Container(accent_colour=helpers.Colour.white())

        if self.state == PlayerState.PLAYING:
            assert self.player.current is not None
            assert self.player.guild is not None

            track = self.player.current
            artist = f"[{track.author}]({track.artist.url})" if track.artist.url else track.author

            heading = (
                "## Music Player Panel\n"
                "This is the Bot's control panel where you can easily perform actions of the bot without using a command."
            )
            if artwork := track.artwork:
                container.add_item(discord.ui.Section(heading, accessory=discord.ui.Thumbnail(artwork)))
            else:
                container.add_item(discord.ui.TextDisplay(heading))

            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(
                f"### Now Playing\n"
                f"**Track:** [{track.title}]({track.uri})\n"
                f"**Artist:** {artist}\n"
                f"**Bound to:** {self.player.channel.mention}\n"
                f"**Position in Queue:** "
                f"{self.player.queue.all.index(self.player.current) + 1}/{len(self.player.queue.all)}"
            ))

            details: list[str] = []
            if track.album and track.album.name:
                album = f"[{track.album.name}]({track.album.url})" if track.album.url else track.album.name
                details.append(f"**Album:** {album}")
            if track.playlist:
                playlist = (
                    f"[{track.playlist.name}]({track.playlist.url})" if track.playlist.url else track.playlist.name
                )
                details.append(f"**Playlist:** {playlist}")
            if self.player.queue.listen_together is not MISSING:
                user = self.player.guild.get_member(self.player.queue.listen_together)
                user_mention = user.mention if user is not None else "Unknown"
                details.append(f"**Listening-together with:** {user_mention}'s Spotify")
            if details:
                container.add_item(discord.ui.TextDisplay("\n".join(details)))

            status = (
                f"```swift\n{PlayerStamp(track.length, self.player.position)}```"
                if not track.is_stream
                else "```swift\n[ 🔴 LIVE STREAM ]```"
            )
            loop_mode = self.player.queue.mode.name.replace("_", " ").upper()
            shuffle = {
                ShuffleMode.off: "<:off1:1322338488443736257> **``Off``**",
                ShuffleMode.on: "<:on1:1322338500300771458> **``On``**",
            }.get(self.player.queue.shuffle)

            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(f"### Status\n{status}"))
            container.add_item(discord.ui.Separator())

            container.add_item(discord.ui.TextDisplay(
                f"**Loop Mode:** `{loop_mode}` {Emojis.empty} • {Emojis.empty} **Shuffle Mode:** {shuffle}"
            ))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(
                f"### Volume\n"
                f"```swift\n"
                f"{ProgressBar(0, 100, self.player.volume)} [ {self.player.volume}% ]```"
            ))

            extras: list[str] = []
            if track.recommended:
                extras.append(f"**Recommended via:** {EMOJI_KEYS[track.source]} **`{track.source.title()}`**")
            if not self.player.queue.is_empty and (upcoming := self.player.queue.peek(0)):
                eta = discord.utils.utcnow() + datetime.timedelta(milliseconds=(track.length - self.player.position))
                extras.append(f"**Next Track:** [{upcoming.title}]({upcoming.uri}) {discord.utils.format_dt(eta, 'R')}")
            if extras:
                container.add_item(discord.ui.TextDisplay("\n".join(extras)))

            container.add_item(discord.ui.Separator())

            container.add_item(discord.ui.ActionRow(self.on_shuffle, self.on_back, self.on_pause_play, self.on_forward, self.on_loop))
            container.add_item(discord.ui.ActionRow(self.on_stop, self.on_volume, self.on_like))

            container.add_item(discord.ui.Separator())
            mode = "Auto-Playing" if self.player.autoplay != 2 else "Manual-Playing"
            container.add_item(discord.ui.TextDisplay(f"-# {mode} • last updated {discord.utils.format_dt(discord.utils.utcnow(), 'R')}"))
        else:
            heading = (
                "## Music Player Panel\n"
                "The control panel was closed, the queue is currently empty and I got nothing to do.\n"
                "You can start a new player session by invoking the </play:1070054930125176923> command.\n\n"
                "*Once you play a new track, this message is going to be the new player panel if it's not deleted, "
                "otherwise I'm going to create a new panel.*"
            )
            icon = self.player.guild.icon.url if self.player.guild is not None and self.player.guild.icon else None
            if icon is not None:
                container.add_item(discord.ui.Section(heading, accessory=discord.ui.Thumbnail(icon)))
            else:
                container.add_item(discord.ui.TextDisplay(heading))

            container.add_item(discord.ui.Separator())

            container.add_item(discord.ui.ActionRow(self.on_shuffle, self.on_back, self.on_pause_play, self.on_forward, self.on_loop))
            container.add_item(discord.ui.ActionRow(self.on_stop, self.on_volume, self.on_like))

            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay("-# last updated"))

        return container

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
            (self.on_shuffle, self.disabled_state(), EMOJI_KEYS["shuffle"][self.player.queue.shuffle]),
            (self.on_back, self.disabled_state(self.player.queue.history_is_empty), None),
            (
                self.on_pause_play,
                self.disabled_state(),
                EMOJI_KEYS["pause_play"][bool(not self.player.paused and self.player.playing)],
            ),
            (self.on_forward, self.disabled_state(self.player.queue.is_empty), None),
            (self.on_loop, self.disabled_state(), EMOJI_KEYS["loop"][self.player.queue.mode]),  # type: ignore
            (self.on_stop, self.disabled_state(), None),
            (self.on_volume, self.disabled_state(), None),
            (self.on_like, self.disabled_state(), None),
        ]

        for button, disabled, emoji in button_updates:
            button.disabled = disabled
            if emoji is not None:
                button.emoji = emoji  # type: ignore

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        assert isinstance(interaction.user, discord.Member)
        assert interaction.guild is not None
        assert interaction.guild.me is not None

        author_vc = interaction.user.voice and interaction.user.voice.channel
        bot_vc = interaction.guild.me.voice and interaction.guild.me.voice.channel

        if retry_after := self.cooldown.update_rate_limit(interaction):
            await interaction.response.send_message(
                f"{Emojis.error} You are being rate limited. Try again in **{retry_after:.2f}** seconds.", ephemeral=True
            )
            return False

        if is_dj(interaction.user) and bot_vc and not author_vc:
            return True

        if (
            author_vc
            and bot_vc
            and (author_vc == bot_vc)
            and interaction.user.voice
            and (interaction.user.voice.deaf or interaction.user.voice.self_deaf)
        ):
            await interaction.response.send_message(
                f"{Emojis.error} You are deafened, please undeafen yourself to use this menu.", ephemeral=True
            )
            return False
        elif (not author_vc and bot_vc) or (author_vc and bot_vc and author_vc != bot_vc):
            await interaction.response.send_message(
                f"{Emojis.error} You must be in {bot_vc.mention} to use this menu.", ephemeral=True
            )
            return False
        elif not author_vc:
            await interaction.response.send_message(
                f"{Emojis.error} You must be in a voice channel to use this menu.", ephemeral=True
            )
            return False

        return True

    async def fetch_player_channel(self, channel: discord.TextChannel | None = None) -> discord.TextChannel | None:
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
        assert self.player.guild is not None
        config = await self.bot.db.get_guild_config(guild_id=self.player.guild.id)
        if not channel and not config.music_panel_channel:
            raise ValueError("No channel provided and no music channel set in the guild configuration.")

        self.channel = config.music_panel_channel or channel  # type: ignore
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
        assert self.player.guild is not None
        config = await self.bot.db.get_guild_config(guild_id=self.player.guild.id)
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

        self._rebuild()

        if self.msg is MISSING and not self.__is_temporary__:
            await self.get_player_message()

        if self.msg is not MISSING:
            await self.msg.edit(view=self)
        else:
            self.msg = await self.channel.send(view=self)

        return self.msg

    async def stop(self) -> None:
        """Stops the player and resets the queue."""
        self.player.queue.reset()
        await self.update(PlayerState.STOPPED)

        super().stop()

    async def _on_shuffle(self, interaction: discord.Interaction) -> None:
        TOGGLE = {ShuffleMode.off: ShuffleMode.on, ShuffleMode.on: ShuffleMode.off}
        self.player.queue.shuffle = TOGGLE.get(self.player.queue.shuffle)

        self._rebuild()
        await interaction.response.edit_message(view=self)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        self._rebuild()
        await interaction.response.edit_message(view=self)
        await self.player.back()

    async def _on_pause_play(self, interaction: discord.Interaction) -> None:
        await self.player.pause(not self.player.paused)
        self._rebuild()
        await interaction.response.edit_message(view=self)

    async def _on_forward(self, interaction: discord.Interaction) -> None:
        self._rebuild()
        await interaction.response.edit_message(view=self)
        await self.player.skip()

    async def _on_loop(self, interaction: discord.Interaction) -> None:
        TRANSITIONS = {
            QueueMode.normal: QueueMode.loop,
            QueueMode.loop: QueueMode.loop_all,
            QueueMode.loop_all: QueueMode.normal,
        }
        self.player.queue.mode = TRANSITIONS.get(self.player.queue.mode)

        self._rebuild()
        await interaction.response.edit_message(view=self)

    async def _on_stop(self, interaction: discord.Interaction) -> None:
        await self.player.disconnect()
        await interaction.response.send_message(f"{Emojis.success} Stopped Track and cleaned up queue.", delete_after=10)

    async def _on_volume(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AdjustVolumeModal(self))

    async def _on_like(self, interaction: discord.Interaction) -> None:
        playlist_tools: Any = self.bot.get_cog("PlaylistTools")
        if not playlist_tools:
            await interaction.response.send_message("This feature is currently disabled.", ephemeral=True)
            return

        liked_songs = await playlist_tools.get_liked_songs(interaction.user.id)

        if not liked_songs:
            await playlist_tools.initizalize_user(interaction.user)

        assert self.player.current is not None
        if self.player.current.uri not in liked_songs:
            await liked_songs.add_track(self.player.current)
            await interaction.response.send_message(
                f"{Emojis.success} Added `{self.player.current.title}` to your liked songs.", ephemeral=True
            )
        else:
            await liked_songs.remove_track(discord.utils.get(liked_songs.tracks, url=self.player.current.uri))
            await interaction.response.send_message(
                f"{Emojis.success} Removed `{self.player.current.title}` from your liked songs.", ephemeral=True
            )

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

        self.msg = await self.update(state=state)  # type: ignore
        return self


class AdjustVolumeModal(discord.ui.Modal, title="Volume Adjuster"):
    """Modal that prompts users for the volume to change to."""

    number = discord.ui.TextInput(
        label="Volume - %",
        style=discord.TextStyle.short,
        placeholder="Enter a Number between 1 and 100",
        min_length=1,
        max_length=3,
    )

    def __init__(self, _view: PlayerPanel, /) -> None:
        super().__init__(timeout=30)
        self._view: PlayerPanel = _view

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        await interaction.response.defer()

        if not self.number.value.isdigit():
            await interaction.followup.send("Please enter a valid number.", ephemeral=True)
            return

        value = int(self.number.value)
        await self._view.player.set_volume(value)
        if interaction.message is not None:
            self._view._rebuild()
            await interaction.message.edit(view=self._view)


class TrackDisambiguatorView[T](LayoutView):
    message: discord.Message
    selected: T

    def __init__(self, ctx: Context | discord.Interaction, tracks: list[T]) -> None:
        super().__init__(timeout=100.0, members=ctx.user, delete_on_timeout=True)
        self.ctx = ctx
        self.tracks = tracks
        self.selected = None

        options = [
            discord.SelectOption(
                label=truncate(x.title, 100),
                description="by " + truncate(discord.utils.remove_markdown(x.author), 100),
                emoji=letter_emoji(i),
                value=str(i),
            )
            for i, x in enumerate(tracks)
        ]

        select = discord.ui.Select(options=options)
        select.callback = self._on_select  # type: ignore[assignment]
        self.select = select

        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.red)
        cancel_btn.callback = self._on_cancel  # type: ignore[assignment]

        description = "\n".join(
            f"{letter_emoji(i)} [{track.title}]({track.uri}) by **{track.author}** | `{convert_duration(track.length)}`"
            for i, track in enumerate(tracks)
        )

        container = discord.ui.Container(accent_colour=helpers.Colour.brand())
        container.add_item(discord.ui.TextDisplay(f"## Choose a Track\n{description}"))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(select))
        container.add_item(discord.ui.ActionRow(cancel_btn))
        self.add_item(container)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        index = int(self.select.values[0])
        self.selected = self.tracks[index]
        self.stop()

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.selected = None
        self.stop()

    @classmethod
    async def start(
        cls: type[TrackDisambiguatorView], context: Context | discord.Interaction, *, tracks: list[T]
    ) -> T | None:
        tracks = tracks[:5]

        if len(tracks) == 1:
            return tracks[0]
        if len(tracks) == 0:
            return None

        self = cls(context, tracks=tracks)
        self.message = await context.send(view=self)

        await self.wait()
        with suppress(discord.HTTPException):
            await self.message.delete()

        return self.selected


class PlaylistSelect(discord.ui.Select):
    def __init__(self, parent: PlaylistPaginator, playlists: list[Playlist]) -> None:
        self.paginator: PlaylistPaginator = parent

        options = [
            discord.SelectOption(
                label="Start Page", emoji=Emojis.Arrows.left, value="__index", description="The front page of the Todo Menu."
            )
        ]
        options.extend([playlist.to_select_option(i) for i, playlist in enumerate(playlists)])
        super().__init__(placeholder=f"Select a playlist ({pluralize(len(playlists)):playlist} found)", options=options)

    async def callback(self: PlaylistSelect, interaction: discord.Interaction) -> Any:
        if self.values[0] == "__index":
            self.paginator.pages = self.paginator.start_pages
        else:
            playlist = self.paginator.playlists[int(self.values[0])]
            self.paginator.pages = playlist.to_embeds()

        self.paginator._current_page = 0
        self.paginator.update_buttons()
        await interaction.response.edit_message(**self.paginator.resolve_msg_kwargs(self.paginator.pages[0]))


class PlaylistPaginator(BasePaginator[discord.Embed | Any]):
    """A custom Paginator for the Playlist Cog."""

    playlists: list[Playlist]
    start_pages: list[discord.Embed]

    async def format_page(self, entries: list[discord.Embed | Any], /) -> discord.Embed:
        if isinstance(entries, discord.Embed):
            return entries
        return entries[0]

    @classmethod
    async def start(
        cls,
        context: Context | discord.Interaction,
        /,
        *,
        entries: list[discord.Embed | Any],
        per_page: int = 10,
        clamp_pages: bool = True,
        timeout: int = 180,
        search_for: bool = False,
        ephemeral: bool = False,
        **kwargs: Any,
    ) -> PlaylistPaginator:
        self = cls(entries=entries, per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)
        self.ctx = context

        self.playlists = kwargs.pop("playlists", [])
        self.start_pages = kwargs.pop("start_pages", [])

        if self.total_pages <= 1:
            self.clear_items()

        self.add_item(PlaylistSelect(self, self.playlists))
        page = await self.format_page(self.pages[0])

        self.msg = await cls._send(context, ephemeral, view=self, embed=page)
        return self
