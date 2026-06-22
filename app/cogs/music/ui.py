from __future__ import annotations

import asyncio
import datetime
import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands
from discord.utils import MISSING
from wavelink import QueueMode

from app.core import Bot, Context, LayoutView
from app.core.pagination import BasePaginator
from app.core.views import ConfirmationView
from app.services import LyricsResult
from app.utils import (
    convert_duration,
    helpers,
    letter_emoji,
    pluralize,
    truncate,
)
from config import Emojis

from .models import DJMode, PlayerState, Playlist, ShuffleMode, is_dj

if TYPE_CHECKING:
    from app.database.base import GuildConfig

    from .cog import Music
    from .player import Player

log = logging.getLogger(__name__)

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

    def __init__(self, *, player: Player, state: PlayerState, disabled: bool, guild_config: GuildConfig | None = None) -> None:
        super().__init__(timeout=None)
        self.bot: Bot = player.client  # type: ignore

        self.player: Player = player
        self.state: PlayerState = state
        self._disabled = disabled
        self._guild_config: GuildConfig | None = guild_config

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
        self._bar_files: list[discord.File] = []
        self.add_item(self.build_container())

    async def _respond_with_rebuild(self, interaction: discord.Interaction) -> None:
        """Edit the interaction response with the rebuilt view and attached bar files."""
        files = self._bar_files or []
        await interaction.response.edit_message(view=self, attachments=files)

    def build_container(self) -> discord.ui.Container:
        """Builds the Components V2 now-playing card for the panel."""
        container = discord.ui.Container(accent_colour=helpers.Colour.brand())

        if self.state == PlayerState.PLAYING and self.player.current is not None:
            assert self.player.guild is not None

            track = self.player.current
            artist = f"[{track.author}]({track.artist.url})" if track.artist.url else track.author

            # Position accounts for autoplay: recommendations live in auto_queue /
            # auto_queue.history, so plain queue.all would always read 1/1 in 24/7
            # autoplay. played_history holds every played track (current last) and
            # upcoming holds what's still to come (manual queue + auto_queue).
            history = self.player.played_history
            upcoming = self.player.upcoming
            try:
                queue_pos = history.index(self.player.current) + 1
            except ValueError:
                queue_pos = len(history) or 1
            queue_total = max(len(history) + len(upcoming), queue_pos, 1)

            now_playing = (
                f"## Now Playing\n"
                f"**Track:** [{track.title}]({track.uri})\n"
                f"**Artist:** {artist}\n"
                f"**Bound to:** {self.player.channel.mention}\n"
                f"**Position in Queue:** {queue_pos}/{queue_total}"
            )

            if artwork := track.artwork:
                container.add_item(discord.ui.Section(now_playing, accessory=discord.ui.Thumbnail(artwork)))
            else:
                container.add_item(discord.ui.TextDisplay(now_playing))

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

            position = min(max(self.player.position, 0), track.length)
            # Lavalink may report a non-stream track with an absurd length (max int64)
            # for radio/live sources. Treat anything over 24h as a stream for display.
            effectively_stream = track.is_stream or track.length > 86_400_000

            container.add_item(discord.ui.Separator())

            if effectively_stream:
                live_file = self.bot.render.progress_bar(0, variant='live', filename='live.png')
                self._bar_files.append(live_file)
                container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(live_file)))
            else:
                pos_ratio = round((position / track.length) * 50) if track.length > 0 else 0
                pos_file = self.bot.render.progress_bar(pos_ratio, variant='position', filename='pos.png')
                self._bar_files.append(pos_file)
                container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(pos_file)))
                container.add_item(discord.ui.TextDisplay(
                    f"`{convert_duration(max(position, 0.0))}` / `{convert_duration(track.length)}`"
                ))

            vol_ratio = round((self.player.volume / 100) * 50)
            vol_file = self.bot.render.progress_bar(vol_ratio, variant='volume', filename='vol.png')
            self._bar_files.append(vol_file)
            container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(vol_file)))
            container.add_item(discord.ui.TextDisplay(f"🔊 **{self.player.volume}%**"))

            container.add_item(discord.ui.Separator())

            loop_mode = self.player.queue.mode.name.replace("_", " ").upper()
            shuffle = {
                ShuffleMode.off: "<:off1:1322338488443736257> **``Off``**",
                ShuffleMode.on: "<:on1:1322338500300771458> **``On``**",
            }.get(self.player.queue.shuffle)

            container.add_item(discord.ui.TextDisplay(
                f"**Loop:** `{loop_mode}` {Emojis.empty} • {Emojis.empty} **Shuffle:** {shuffle}"
            ))

            extras: list[str] = []
            if track.recommended:
                extras.append(f"**Recommended via:** {EMOJI_KEYS[track.source]} **`{track.source.title()}`**")
            if not effectively_stream and not self.player.queue.is_empty and (upcoming := self.player.queue.peek(0)):
                remaining_ms = max(track.length - position, 0)
                eta = discord.utils.utcnow() + datetime.timedelta(milliseconds=remaining_ms)
                extras.append(f"**Next Track:** [{upcoming.title}]({upcoming.uri}) {discord.utils.format_dt(eta, 'R')}")
            if extras:
                container.add_item(discord.ui.TextDisplay("\n".join(extras)))

            container.add_item(discord.ui.Separator())

            container.add_item(discord.ui.ActionRow(self.on_shuffle, self.on_back, self.on_pause_play, self.on_forward, self.on_loop))
            container.add_item(discord.ui.ActionRow(self.on_stop, self.on_volume, self.on_like))

            container.add_item(discord.ui.Separator())
            mode = "Auto-Playing" if self.player.autoplay != 2 else "Manual-Playing"
            container.add_item(discord.ui.TextDisplay(f"-# {mode} • last updated {discord.utils.format_dt(discord.utils.utcnow(), 'R')} • [Web Panel](https://percy.klappstuhl.me/dashboard/guild/{self.player.guild.id}/overview)"))
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
            container.add_item(discord.ui.TextDisplay("-# last updated • [Web Panel](https://percy.klappstuhl.me/dashboard/guild/{self.player.guild.id}/overview)"))

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
        # Only treat the player as "empty" when nothing is playing AND there's
        # nothing queued anywhere — under 24/7 autoplay the manual queue/history can
        # be empty while a recommendation plays and more sit in the auto_queue.
        empty = self.player.queue.all_is_empty and not self.player.upcoming and self.player.current is None
        return bool(check) or self.state == PlayerState.STOPPED or empty

    def update_buttons(self) -> None:
        """Updates the buttons of the panel."""
        button_updates: list[tuple[discord.Button, bool, str | None]] = [
            (self.on_shuffle, self.disabled_state(), EMOJI_KEYS["shuffle"][self.player.queue.shuffle]),
            # Back needs a previous track; forward needs a next one. Both account for
            # autoplay, where played/upcoming tracks live in auto_queue(.history)
            # rather than the manual queue (which is empty in 24/7 autoplay).
            (self.on_back, self.disabled_state(len(self.player.played_history) < 2), None),
            (
                self.on_pause_play,
                self.disabled_state(),
                EMOJI_KEYS["pause_play"][bool(not self.player.paused and self.player.playing)],
            ),
            (self.on_forward, self.disabled_state(not self.player.upcoming), None),
            (self.on_loop, self.disabled_state(), EMOJI_KEYS["loop"][self.player.queue.mode]),  # type: ignore
            (self.on_stop, self.disabled_state(), None),
            (self.on_volume, self.disabled_state(), None),
            (self.on_like, self.disabled_state(), None),
        ]

        for button, disabled, emoji in button_updates:
            button.disabled = disabled
            if emoji is not None:
                button.emoji = emoji  # type: ignore

    def _has_dj_access(self, member: discord.Member) -> bool:
        """Returns True if the member has DJ-level access (DJ role or manage_guild)."""
        return is_dj(member) or member.guild_permissions.manage_guild

    @property
    def _dj_mode(self) -> DJMode:
        return DJMode(getattr(self._guild_config, "music_dj_mode", 0))

    async def _require_dj(self, interaction: discord.Interaction) -> bool:
        """Check DJ permission for hybrid mode destructive actions.

        Returns True if the user is allowed. Sends an error and returns False otherwise.
        """
        assert isinstance(interaction.user, discord.Member)
        if self._dj_mode == DJMode.hybrid and not self._has_dj_access(interaction.user):
            await interaction.response.send_message(
                f"{Emojis.error} This action requires the **DJ** role.", ephemeral=True
            )
            return False
        return True

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

        # DJs and manage_guild members can always interact (even from outside voice).
        if self._has_dj_access(interaction.user):
            return True

        # DJ-only mode: reject everyone else immediately.
        if self._dj_mode == DJMode.dj_only:
            await interaction.response.send_message(
                f"{Emojis.error} Only members with the **DJ** role can control the player.", ephemeral=True
            )
            return False

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

        files = getattr(self, '_bar_files', None) or []
        if self.msg is not MISSING:
            await self.msg.edit(view=self, attachments=files)
        else:
            self.msg = await self.channel.send(view=self, files=files)

        return self.msg

    async def stop(self) -> None:
        """Stops the player and resets the queue."""
        self.player.queue.reset()
        await self.update(PlayerState.STOPPED)

        super().stop()

    async def _on_shuffle(self, interaction: discord.Interaction) -> None:
        if not await self._require_dj(interaction):
            return

        TOGGLE = {ShuffleMode.off: ShuffleMode.on, ShuffleMode.on: ShuffleMode.off}
        self.player.queue.shuffle = TOGGLE.get(self.player.queue.shuffle)

        self._rebuild()
        await self._respond_with_rebuild(interaction)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        if not await self._require_dj(interaction):
            return

        self._rebuild()
        await self._respond_with_rebuild(interaction)
        await self.player.back()

    async def _on_pause_play(self, interaction: discord.Interaction) -> None:
        await self.player.pause(not self.player.paused)
        self._rebuild()
        await self._respond_with_rebuild(interaction)

    async def _on_forward(self, interaction: discord.Interaction) -> None:
        if not await self._require_dj(interaction):
            return

        self._rebuild()
        await self._respond_with_rebuild(interaction)
        await self.player.skip()

    async def _on_loop(self, interaction: discord.Interaction) -> None:
        if not await self._require_dj(interaction):
            return

        TRANSITIONS = {
            QueueMode.normal: QueueMode.loop,
            QueueMode.loop: QueueMode.loop_all,
            QueueMode.loop_all: QueueMode.normal,
        }
        self.player.queue.mode = TRANSITIONS.get(self.player.queue.mode)

        self._rebuild()
        await self._respond_with_rebuild(interaction)

    async def _on_stop(self, interaction: discord.Interaction) -> None:
        if not await self._require_dj(interaction):
            return

        await self.player.disconnect()
        await interaction.response.send_message(f"{Emojis.success} Stopped Track and cleaned up queue.", delete_after=10)

    async def _on_volume(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AdjustVolumeModal(self))

    async def _on_like(self, interaction: discord.Interaction) -> None:
        music_cog: Any = self.bot.get_cog("Music")
        if not music_cog:
            await interaction.response.send_message("This feature is currently disabled.", ephemeral=True)
            return

        liked_songs = await music_cog.get_liked_songs(interaction.user.id)

        if not liked_songs:
            await music_cog.initizalize_user(interaction.user)

        assert self.player.current is not None
        track_urls = [t.url for t in liked_songs.tracks]
        if self.player.current.uri not in track_urls:
            await liked_songs.add_track(self.player.current)
            await interaction.response.send_message(
                f"{Emojis.success} Added `{self.player.current.title}` to your liked songs.", ephemeral=True
            )
        else:
            await liked_songs.remove_track(discord.utils.get(liked_songs.tracks, url=self.player.current.uri))
            await interaction.response.send_message(
                f"{Emojis.success} Removed `{self.player.current.title}` from your liked songs.", ephemeral=True
            )

        music_cog.get_playlists.invalidate(interaction.user.id)

    @classmethod
    async def start(
        cls: type[PlayerPanel],
        player: Player,
        *,
        channel: discord.TextChannel,
        disabled: bool,
        state: PlayerState = PlayerState.STOPPED,
        existing_message_id: int | None = None,
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
        existing_message_id: :class:`int` | None
            An optional message ID to try fetching and reusing (for temporary panels after restart).

        Returns
        -------
        :class:`PlayerPanel`
            The paginator object.
        """
        assert player.guild is not None
        config = await player.client.db.get_guild_config(player.guild.id)  # type: ignore[attr-defined]
        self = cls(player=player, state=state, disabled=disabled, guild_config=config)

        await self.fetch_player_channel(channel)

        if existing_message_id and self.__is_temporary__:
            with suppress(discord.HTTPException):
                self.msg = await self.channel.fetch_message(existing_message_id)

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
        container.add_item(discord.ui.ActionRow(select, cancel_btn))
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


DEFAULT_CHANNEL_DESCRIPTION = """
This is the Channel where you can see {bot}'s current playing songs.
You can interact with the **control panel** and manage the current songs.

__Be careful not to delete the **control panel** message.__
If you accidentally deleted the message, you have to redo the setup with </music setup:1207828024666497090>.

ℹ️** | Every Message if not pinned, gets deleted within 60 seconds.**
"""


class MusicSetupView(LayoutView):
    """Dashboard for music player channel configuration.

    Consolidates setup, panel toggle, and reset into one CV2 card.
    """

    _DJ_MODE_LABELS: dict[DJMode, str] = {
        DJMode.everyone: "DJ Mode: Everyone",
        DJMode.dj_only: "DJ Mode: DJ Only",
        DJMode.hybrid: "DJ Mode: Hybrid",
    }

    _DJ_MODE_STYLES: dict[DJMode, discord.ButtonStyle] = {
        DJMode.everyone: discord.ButtonStyle.grey,
        DJMode.dj_only: discord.ButtonStyle.red,
        DJMode.hybrid: discord.ButtonStyle.blurple,
    }

    def __init__(self, bot: Bot, member: discord.Member, config: GuildConfig) -> None:
        super().__init__(timeout=300.0, members=member, delete_on_timeout=True)
        self.bot = bot
        self.config = config
        self.guild: discord.Guild = member.guild

        self._channel_select: discord.ui.ChannelSelect = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text],
            placeholder="Select an existing channel...",
            min_values=1, max_values=1,
        )
        self._channel_select.callback = self._on_channel_select

        self._create_btn: discord.ui.Button = discord.ui.Button(
            label="Create Channel", style=discord.ButtonStyle.green,
        )
        self._create_btn.callback = self._on_create_channel

        self._toggle_panel_btn: discord.ui.Button = discord.ui.Button(
            label="Panel: Enabled", style=discord.ButtonStyle.grey,
        )
        self._toggle_panel_btn.callback = self._on_toggle_panel

        self._dj_mode_btn: discord.ui.Button = discord.ui.Button(
            label="DJ Mode: Everyone", style=discord.ButtonStyle.grey,
        )
        self._dj_mode_btn.callback = self._on_cycle_dj_mode

        self._reset_btn: discord.ui.Button = discord.ui.Button(
            label="Reset Configuration", style=discord.ButtonStyle.red,
        )
        self._reset_btn.callback = self._on_reset

        self._update_state()
        self._rebuild_layout()

    def _update_state(self) -> None:
        has_channel = bool(self.config.music_panel_channel_id)
        dj_mode = DJMode(getattr(self.config, "music_dj_mode", 0))

        # DJ mode button is always available (not tied to channel setup).
        self._dj_mode_btn.label = self._DJ_MODE_LABELS[dj_mode]
        self._dj_mode_btn.style = self._DJ_MODE_STYLES[dj_mode]

        # Panel toggle is always available — it controls whether a panel is
        # shown at all, regardless of whether a dedicated channel exists.
        if self.config.use_music_panel:
            self._toggle_panel_btn.label = "Panel: Enabled"
            self._toggle_panel_btn.style = discord.ButtonStyle.green
        else:
            self._toggle_panel_btn.label = "Panel: Disabled"
            self._toggle_panel_btn.style = discord.ButtonStyle.grey
        self._toggle_panel_btn.disabled = False

        # Channel setup controls — only allow changes when no channel is configured.
        self._channel_select.disabled = has_channel
        self._create_btn.disabled = has_channel
        self._reset_btn.disabled = not has_channel

    def _rebuild_layout(self) -> None:
        self.clear_items()
        container = discord.ui.Container(accent_colour=helpers.Colour.brand())

        container.add_item(discord.ui.Section(
            "## Music Player Setup\n-# Configure the music player panel and dedicated channel",
            accessory=discord.ui.Thumbnail(
                self.guild.icon.url if self.guild.icon else "https://cdn.discordapp.com/embed/avatars/0.png"
            ),
        ))
        container.add_item(discord.ui.Separator())

        # --- Panel toggle (always available, independent of channel) ---
        if self.config.use_music_panel:
            has_channel = bool(self.config.music_panel_channel_id)
            if has_channel:
                panel_desc = (
                    f"The player panel is pinned in <#{self.config.music_panel_channel_id}>. "
                    "Messages are auto-deleted after 60s."
                )
            else:
                panel_desc = (
                    "A temporary panel appears in the channel where playback starts "
                    "and is removed when the session ends."
                )
            container.add_item(discord.ui.TextDisplay(
                f"### Player Panel\n"
                f"-# {panel_desc}\n"
                f"-# Disable the panel to hide all in-channel player controls."
            ))
        else:
            container.add_item(discord.ui.TextDisplay(
                "### Player Panel\n"
                "-# The panel is disabled — no in-channel player controls will be shown."
            ))
        container.add_item(discord.ui.ActionRow(self._toggle_panel_btn))
        container.add_item(discord.ui.Separator())

        # --- Dedicated channel (optional) ---
        has_channel = bool(self.config.music_panel_channel_id)
        if has_channel:
            container.add_item(discord.ui.TextDisplay(
                f"### Dedicated Channel\n"
                f"**Channel:** <#{self.config.music_panel_channel_id}>\n"
                f"-# The panel is permanently pinned here. Remove the channel to switch back to temporary panels."
            ))
            container.add_item(discord.ui.ActionRow(self._reset_btn))
        else:
            container.add_item(discord.ui.TextDisplay(
                "### Dedicated Channel\n"
                "Optionally assign a permanent channel for the player panel.\n"
                "-# The bot will configure it with slowmode and pin a persistent panel message."
            ))
            container.add_item(discord.ui.ActionRow(self._channel_select))
            container.add_item(discord.ui.ActionRow(self._create_btn))

        # --- DJ mode (always shown) ---
        dj_mode = DJMode(getattr(self.config, "music_dj_mode", 0))
        dj_descriptions = {
            DJMode.everyone: "Anyone in the voice channel can use all player controls.",
            DJMode.dj_only: "Only DJ role holders (or Manage Server) can control the player.",
            DJMode.hybrid: "Everyone can pause/resume and adjust volume; skip, stop, shuffle, and loop require the DJ role.",
        }
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            f"### DJ Mode\n"
            f"{dj_descriptions[dj_mode]}\n"
            f"-# Click the button to cycle between modes."
        ))
        container.add_item(discord.ui.ActionRow(self._dj_mode_btn))

        self.add_item(container)

    async def _setup_channel(self, channel: discord.TextChannel, interaction: discord.Interaction) -> None:
        """Shared logic: configure the channel as the music player channel."""
        assert self.bot.user is not None
        await channel.edit(
            slowmode_delay=3,
            topic=DEFAULT_CHANNEL_DESCRIPTION.format(bot=self.bot.user.mention),
        )

        from .player import Player

        view = LayoutView()
        view.add_item(Player.preview_container(channel.guild))
        message = await channel.send(view=view)

        await message.pin()
        await channel.purge(limit=5, check=lambda msg: not msg.pinned)

        self.config = await self.config.update(
            music_panel_channel_id=channel.id,
            music_panel_message_id=message.id,
            use_music_panel=True,
        )
        self._update_state()
        self._rebuild_layout()

        await interaction.followup.send(
            f"{Emojis.success} Music player channel set to {channel.mention}.",
            ephemeral=True,
        )
        if interaction.message is not None:
            await interaction.message.edit(view=self)

    async def _on_channel_select(self, interaction: discord.Interaction) -> None:
        channel = self._channel_select.values[0].resolve()
        if channel is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                f"{Emojis.error} Could not resolve that channel.", ephemeral=True
            )
            return

        perms = channel.permissions_for(self.guild.me)
        if not perms.manage_messages or not perms.send_messages:
            await interaction.response.send_message(
                f"{Emojis.error} I need Send Messages and Manage Messages in that channel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        await self._setup_channel(channel, interaction)

    async def _on_create_channel(self, interaction: discord.Interaction) -> None:
        if not interaction.app_permissions.manage_channels:
            await interaction.response.send_message(
                f"{Emojis.error} I need Manage Channels permission to create a channel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        parent = (self.guild.text_channels[0].category if self.guild.text_channels else None) or self.guild
        channel = await parent.create_text_channel(name="\U0001f3b6percy-music")
        await self._setup_channel(channel, interaction)

    async def _on_toggle_panel(self, interaction: discord.Interaction) -> None:
        new_value = not self.config.use_music_panel
        self.config = await self.config.update(use_music_panel=new_value)

        self._update_state()
        self._rebuild_layout()

        await interaction.response.edit_message(view=self)

    async def _on_cycle_dj_mode(self, interaction: discord.Interaction) -> None:
        current = DJMode(getattr(self.config, "music_dj_mode", 0))
        cycle = {DJMode.everyone: DJMode.dj_only, DJMode.dj_only: DJMode.hybrid, DJMode.hybrid: DJMode.everyone}
        new_mode = cycle[current]
        self.config = await self.config.update(music_dj_mode=new_mode.value)

        self._update_state()
        self._rebuild_layout()

        await interaction.response.edit_message(view=self)

    async def _on_reset(self, interaction: discord.Interaction) -> None:
        confirm = ConfirmationView(
            interaction.user, timeout=60.0, delete_after=True,
            content="This will delete the dedicated music channel. The panel will continue as a temporary one. Continue?",
        )
        await interaction.response.send_message(view=confirm, ephemeral=True)
        confirm.message = await interaction.original_response()
        await confirm.wait()
        if not confirm.value:
            return

        channel = self.config.music_panel_channel
        self.config = await self.config.update(
            music_panel_channel_id=None, music_panel_message_id=None
        )

        if channel:
            with suppress(discord.HTTPException):
                await channel.delete(reason="Dedicated music channel removed")

        self._update_state()
        self._rebuild_layout()

        await interaction.followup.send(
            f"{Emojis.success} Dedicated channel removed. The panel will now appear temporarily where playback starts.",
            ephemeral=True,
        )
        if interaction.message is not None:
            await interaction.message.edit(view=self)


class LiveLyricsView(LayoutView):
    """A self-updating message that follows the playing track's synced lyrics.

    Rate-limit strategy (the whole point of the design):

    * The lyrics are fetched **once** per track; line timing is driven entirely by
      ``player.position``, which wavelink interpolates locally -- so following the
      song costs **no** network/Lavalink calls.
    * The message is edited **only when the highlighted line changes** *and* at
      most once every :attr:`MIN_EDIT_INTERVAL` seconds. Faster line changes are
      coalesced -- at the next allowed edit we render whatever the live position
      says is current -- so a dense rap section can never trigger a burst of edits.
    * The loop sleeps until the next line boundary (cheap, no edit) instead of
      polling every second, then re-checks.

    The session binds to the player (``player.lyrics_session``); starting a new one
    replaces the old. It ends itself when the player disconnects or stops, and can
    auto-follow into the next track (re-fetching its lyrics once).

    **Edits go through the slash-command interaction token** (the ``@original`` /
    followup webhook route). Per Discord's docs those endpoints are *not* counted
    against the bot's global 50-req/s budget and live in a separate rate-limit
    bucket from normal channel sends -- so the live feed never contends with the
    player panel or other messages in the channel. The catch is that interaction
    tokens die after 15 minutes; shortly before that we transparently migrate to a
    normal channel message (:meth:`_migrate_to_channel`) and keep going.
    """

    #: Hard floor between message edits. Even on the (global-exempt) interaction
    #: route the webhook bucket still applies, so we edit on line-change only and
    #: never more than once per this many seconds.
    MIN_EDIT_INTERVAL: float = 5.0
    #: Interaction tokens are valid for 15 minutes; migrate to a real message a
    #: minute early so an edit never lands on a dead token.
    TOKEN_LIFETIME: datetime.timedelta = datetime.timedelta(minutes=14)

    def __init__(self, *, player: Player, cog: Music, result: LyricsResult) -> None:
        super().__init__(timeout=None)
        self.player: Player = player
        self.cog: Music = cog
        self.result: LyricsResult = result
        self.bot: Bot = player.client  # type: ignore[assignment]

        self.message: discord.Message = MISSING
        self._track = player.current
        self._task: asyncio.Task[None] | None = None
        self._closed: bool = False
        self._finalized: bool = False
        self._last_index: int = -2
        self._last_edit: float = 0.0

        # Interaction-token edit backend (slash command). Once the token nears
        # expiry we flip ``_using_interaction`` off and edit a plain channel message.
        self._interaction: discord.Interaction | None = None
        self._channel: discord.abc.Messageable | None = None
        self._using_interaction: bool = False
        self._token_deadline: datetime.datetime = MISSING

        self.stop_button = discord.ui.Button(
            style=discord.ButtonStyle.red, emoji="⏹️", label="Stop Live Lyrics"
        )
        self.stop_button.callback = self._on_close

    # -- rendering -------------------------------------------------------

    def _build(self, *, footer: str | None = None) -> discord.ui.Container:
        container = discord.ui.Container(accent_colour=helpers.Colour.brand())

        track = self.player.current or self._track
        title = self.result.title or (track.title if track else "Unknown")
        header = f"## 🎤 Live Lyrics\n**{title}**"
        if track is not None and track.artwork:
            container.add_item(discord.ui.Section(header, accessory=discord.ui.Thumbnail(track.artwork)))
        else:
            container.add_item(discord.ui.TextDisplay(header))

        container.add_item(discord.ui.Separator())

        if self.result.has_synced and self.result.synced is not None:
            body = self.result.synced.render(self.player.position) or "♪"
        else:
            body = "*No time-synced lyrics are available for this track.*"
        container.add_item(discord.ui.TextDisplay(body))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(footer or f"-# Source: {self.result.source} • updates live"))
        if not self._closed:
            container.add_item(discord.ui.ActionRow(self.stop_button))
        return container

    def _rebuild(self, *, footer: str | None = None) -> None:
        self.clear_items()
        self.add_item(self._build(footer=footer))

    async def _safe_edit(self, *, footer: str | None = None) -> None:
        self._rebuild(footer=footer)
        if self.message is not MISSING:
            with suppress(discord.HTTPException):
                await self.message.edit(view=self)

    # -- lifecycle -------------------------------------------------------

    async def start_from_interaction(self, interaction: discord.Interaction) -> discord.Message:
        """Post the public live message via the interaction token and start the loop.

        The message is sent as a (non-ephemeral) followup so it is edited through the
        global-exempt interaction route; the original ephemeral defer is resolved with
        a small confirmation. Assumes the command already deferred the interaction.
        """
        prev = getattr(self.player, "lyrics_session", None)
        if prev is not None and prev is not self:
            await prev.stop()

        self._interaction = interaction
        self._channel = interaction.channel  # type: ignore[assignment]
        self._token_deadline = interaction.created_at + self.TOKEN_LIFETIME

        self._rebuild()
        self.message = await interaction.followup.send(view=self, wait=True)
        self._using_interaction = True
        with suppress(discord.HTTPException):
            await interaction.edit_original_response(content=f"{Emojis.success} Showing **live** lyrics below.")

        self.player.lyrics_session = self
        self._task = self.bot.loop.create_task(self._run())
        return self.message

    async def _migrate_to_channel(self) -> None:
        """Move off the (soon-to-expire) interaction token onto a normal channel message."""
        if not self._using_interaction:
            return
        self._using_interaction = False

        old = self.message
        self.message = MISSING
        if self._channel is not None:
            with suppress(discord.HTTPException):
                self._rebuild()
                self.message = await self._channel.send(view=self)
        # Token is still valid for ~1 more minute, so the stale followup can be removed.
        with suppress(discord.HTTPException, AttributeError):
            if old is not MISSING:
                await old.delete()

    async def _run(self) -> None:
        loop = self.bot.loop
        try:
            while not self._closed:
                if not self.player.connected or self.player.current is None:
                    break

                # Interaction token is about to expire -> switch to a normal message.
                if self._using_interaction and discord.utils.utcnow() >= self._token_deadline:
                    await self._migrate_to_channel()

                # Track changed -> re-fetch lyrics for the new track (once) and follow it.
                if self.player.current != self._track:
                    self._track = self.player.current
                    self._last_index = -2
                    self.result = await self.cog.fetch_lyrics_for_player(self.player) or LyricsResult(
                        title=self.player.current.title, source="—"
                    )

                if self.player.paused:
                    await asyncio.sleep(2.0)
                    continue

                synced = self.result.synced if self.result.has_synced else None
                if synced is None:
                    # Nothing to sync for this track: render the notice once, then idle.
                    if self._last_index != -3:
                        await self._safe_edit()
                        self._last_index = -3
                    await asyncio.sleep(3.0)
                    continue

                position = self.player.position
                index = synced.active_index(position)
                now = loop.time()
                if index != self._last_index and (now - self._last_edit) >= self.MIN_EDIT_INTERVAL:
                    await self._safe_edit()
                    self._last_index = index
                    self._last_edit = now

                # Sleep until the next line is due (cheap; edits stay gated above).
                nxt = synced.next_timestamp(index)
                if nxt is None:
                    await asyncio.sleep(3.0)
                else:
                    await asyncio.sleep(max(0.3, min((nxt - position) / 1000 + 0.05, 5.0)))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Live lyrics loop crashed for guild %s", getattr(self.player.guild, "id", None))
        finally:
            await self._finalize()

    async def _on_close(self, interaction: discord.Interaction) -> None:
        with suppress(discord.HTTPException):
            await interaction.response.defer()
        await self.stop()

    async def stop(self) -> None:
        """Stop following and finalise the message (idempotent)."""
        if self._closed:
            return
        self._closed = True
        if getattr(self.player, "lyrics_session", None) is self:
            self.player.lyrics_session = None
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
        await self._finalize()

    async def _finalize(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        self._closed = True
        await self._safe_edit(footer="-# Live lyrics ended.")
        super().stop()
