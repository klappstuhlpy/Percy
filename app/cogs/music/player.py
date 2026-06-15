from __future__ import annotations

import datetime
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

from .models import PlayerState, Queue, SearchReturn, is_dj

if TYPE_CHECKING:
    from discord.abc import Connectable

    from app.database import GuildConfig

    from .ui import PlayerPanel

log = logging.getLogger(__name__)


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
