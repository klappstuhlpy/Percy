from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, cast

import discord
import wavelink
from discord import app_commands
from discord.ext import commands
from wavelink import Playable

from app.cogs.music._player import Player, SearchReturn
from app.core import Cog, Context
from app.core.models import group
from app.database import BaseRecord
from app.utils import cache, checks, fuzzy, get_shortened_string, helpers, pluralize
from app.utils.pagination import BasePaginator, TextSource
from config import Emojis

if TYPE_CHECKING:
    import datetime


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
            raise commands.BadArgument('Please enter a valid playlist name' + ' or id.' if self.with_id else '.')

        if len(lower) > 100:
            raise commands.BadArgument(
                f'Playlist names must be 100 characters or less. (You have *{len(lower)}* characters)')

        cog: PlaylistTools | None = ctx.bot.get_cog('PlaylistTools')
        if cog is None:
            raise commands.BadArgument('Playlist tools are currently unavailable.')

        if self.with_id and converted and converted.isdigit():
            return int(converted)

        return converted.strip() if not self.lower else lower


class PlaylistSelect(discord.ui.Select):
    def __init__(self, parent: PlaylistPaginator, playlists: list[Playlist]) -> None:
        self.paginator = parent
        options = [
            discord.SelectOption(
                label='Start Page',
                emoji=Emojis.Arrows.left,
                value='__index',
                description='The front page of the Todo Menu.')]
        options.extend([playlist.to_select_option(i) for i, playlist in enumerate(playlists)])
        super().__init__(
            placeholder=f'Select a playlist ({pluralize(len(playlists)):playlist} found)',
            options=options
        )

    async def callback(self, interaction: discord.Interaction) -> Any:
        if self.values[0] == '__index':
            self.paginator.pages = self.paginator.start_pages
        else:
            playlist = self.paginator.playlists[int(self.values[0]) - 1]
            self.paginator.pages = playlist.to_embeds()

        self.paginator._current_page = 0
        self.paginator.update_buttons()
        await interaction.response.edit_message(
            **self.paginator.resolve_msg_kwargs(self.paginator.pages[0])
        )


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
            **kwargs: Any
    ) -> PlaylistPaginator:
        self = cls(entries=entries, per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)
        self.ctx = context

        self.playlists = kwargs.pop('playlists', [])
        self.start_pages = kwargs.pop('start_pages', [])

        if self.total_pages <= 1:
            self.clear_items()

        self.add_item(PlaylistSelect(self, self.playlists))
        page = await self.format_page(self.pages[0])

        self.msg = await cls._send(context, ephemeral, view=self, embed=page)
        return self


class Playlist(BaseRecord):

    cog: PlaylistTools
    id: int
    name: str
    owner_id: int
    created: datetime

    __slots__ = ('cog', 'id', 'name', 'owner_id', 'created', 'tracks')

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tracks: list[PlaylistTrack] = []

    def __repr__(self) -> str:
        return f'<Playlist id={self.id} name={self.name}>'

    def __str__(self) -> str:
        return self.name

    def __len__(self) -> int:
        return len(self.tracks)

    @property
    def is_liked_songs(self) -> bool:
        """:class:`bool`: Whether the playlist is the user's liked songs."""
        return self.name == 'Liked Songs'

    @property
    def field_tuple(self) -> tuple[str, str]:
        """:class:`tuple`: Returns a tuple of the Playlist's name and value."""
        name = f'#{self.id}: {self.name}'
        if self.is_liked_songs:
            name = self.name

        value = None
        if len(self.tracks) >= 1:
            value = f'with {pluralize(len(self.tracks)):Track}'

        return name, value or '...'

    @property
    def choice_text(self) -> str:
        """:class:`str`: Returns the name of the Playlist."""
        if self.is_liked_songs:
            return self.name
        return f'[{self.id}] {self.name}'

    async def add_track(self, track: Playable) -> PlaylistTrack:
        """Add a track to the playlist.

        Parameters
        ----------
        track: wavelink.Playable
            The track to add to the playlist.

        Returns
        -------
        PlaylistTrack
            The track that was added to the playlist.
        """
        query = "INSERT INTO playlist_lookup (playlist_id, name, url) VALUES ($1, $2, $3) RETURNING *;"
        record = await self.cog.bot.db.fetchrow(query, self.id, track.title, track.uri)

        track = PlaylistTrack(record=record)
        self.tracks.append(track)
        return track

    async def remove_track(self, track: PlaylistTrack) -> None:
        """Remove a track from the playlist.

        Parameters
        ----------
        track: PlaylistTrack
            The track to remove from the playlist.
        """
        await self.cog.bot.db.execute("DELETE FROM playlist_lookup WHERE id = $1;", track.id)
        self.tracks.remove(track)

    def to_embeds(self) -> list[discord.Embed]:
        """Converts the Playlist to a list of Embeds."""
        source = TextSource(prefix=None, suffix=None, max_size=3080)
        if len(self.tracks) == 0:
            source.add_line('*This playlist is empty.*')
        else:
            for index, track in enumerate(self.tracks):
                source.add_line(f'`{index + 1}.` {track.text}')

        embeds = []
        for page in source.pages:
            embed = discord.Embed(title=f'{self.name} ({pluralize(len(self.tracks)):Track})',
                                  timestamp=self.created,
                                  description=page)
            embed.set_footer(text=f'[{self.id}] • Created at')
            embeds.append(embed)

        return embeds

    def to_select_option(self, value: Any) -> discord.SelectOption:
        """Converts the Playlist to a SelectOption."""
        return discord.SelectOption(
            label=self.name,
            emoji='\N{MULTIPLE MUSICAL NOTES}',
            value=str(value),
            description=f'{len(self.tracks)} Tracks')

    async def delete(self) -> None:
        """Delete a playlist and all corresponding entries."""
        query = "DELETE FROM playlist WHERE id = $1;"
        await self.cog.bot.db.execute(query, self.id)

        query = "DELETE FROM playlist_lookup WHERE playlist_id = $1;"
        await self.cog.bot.db.execute(query, self.id)

        self.cog.get_playlists.invalidate(self.owner_id)

    async def clear(self) -> None:
        """Clear all Items in a playlist."""
        query = "DELETE FROM playlist_lookup WHERE playlist_id = $1;"
        await self.cog.bot.db.execute(query, self.id)

        self.tracks.clear()


class PlaylistTrack(BaseRecord):
    id: int
    name: str
    url: str

    __slots__ = ('id', 'name', 'url')

    @property
    def text(self) -> str:
        return f'[{self.name}]({self.url}) (ID: {self.id})'


class PlaylistTools(Cog):
    """Additional Music Tools for the Music Cog.
    Like: Playlist, DJ, Setup etc."""

    emoji = '\N{GUITAR}'

    async def cog_before_invoke(self, ctx: Context) -> None:
        await self.initizalize_user(ctx.author)

    async def initizalize_user(self, user: discord.abc.User | discord.Member) -> int | None:
        # Creates a static Playlist for every new User that interacts with the Bot
        # called 'Liked Songs', this Playlist cannot be deleted
        # and is used to store all liked songs from the user.

        # The User can store Liked Songs using the Button the Player Control Panel

        playlists = await self.get_playlists(user.id)
        if any(playlist.is_liked_songs for playlist in playlists):
            return None

        record = await self.bot.db.fetchval(
            "INSERT INTO playlist (user_id, name, created) VALUES ($1, $2, $3) RETURNING id;",
            user.id, 'Liked Songs', discord.utils.utcnow().replace(tzinfo=None))
        self.get_playlists.invalidate(user.id)
        return record

    async def playlist_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        playlists = await self.get_playlists(interaction.user.id)

        def key(p: Playlist) -> str:
            return p.choice_text if interaction.command.name == 'play' else p.choice_text and p.name != 'Liked Songs'

        results = fuzzy.finder(current, playlists, key=key, raw=True)

        return [
            app_commands.Choice(name=get_shortened_string(length, start, playlist.choice_text), value=playlist.id)
            for length, start, playlist in results[:20]
        ]

    async def _get_playlist_tracks(self, playlist_id: int) -> list[PlaylistTrack]:
        query = "SELECT * FROM playlist_lookup WHERE playlist_id=$1;"
        records = await self.bot.db.fetch(query, playlist_id)
        return [PlaylistTrack(record=record) for record in records]

    async def get_playlist(
            self,
            ctx: Context | discord.Interaction,
            name_or_id: str | int,
            *,
            pass_tracks: bool = False
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
            args = (name_or_id,)
            query = "SELECT * FROM playlist WHERE id = $1;"
        else:
            query = "SELECT * FROM playlist WHERE LOWER(name) = $1 AND user_id = $2;"
            args = (name_or_id.lower(), ctx.user.id)

        record = await self.bot.db.fetchrow(query, *args)
        playlist = Playlist(cog=self, record=record) if record else None

        if playlist and pass_tracks is False:
            playlist.tracks = await self._get_playlist_tracks(playlist.id)
        return playlist

    async def get_liked_songs(self, user_id: int) -> Playlist | None:
        """Gets a User 'Liked Songs' playlist."""
        query = "SELECT * FROM playlist WHERE user_id=$1 AND name='Liked Songs' LIMIT 1;"
        record = await self.bot.db.fetchrow(query, user_id)
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
        query = "SELECT * FROM playlist WHERE user_id=$1;"

        records = await self.bot.db.fetch(query, user_id)
        playlists = [Playlist(cog=self, record=record) for record in records]

        for playlist in playlists:
            playlist.tracks = await self._get_playlist_tracks(playlist.id)
        return playlists

    @group(
        name='playlist',
        alias='pl',
        description='Manage your playlist.',
        guild_only=True,
        hybrid=True
    )
    async def playlist(self, ctx: Context) -> None:
        """Manage your playlist."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @playlist.command(
        name='show',
        description='Display all your playlists and tracks.'
    )
    async def playlist_show(self, ctx: Context) -> None:
        """Display all your playlists and tracks."""
        playlists = await self.get_playlists(ctx.author.id)
        if not playlists:
            return await ctx.send_error(
                f'You don\'t have any playlists. You can create a playlist using `{ctx.prefix}playlist create`.')

        items = [playlist.field_tuple for playlist in playlists]

        fields = []
        for i in range(0, len(items), 12):
            fields.append(items[i:i + 12])

        embeds = []
        for index, field in enumerate(fields):
            embed = discord.Embed(
                title='Your Playlists',
                description='Here are your playlists, use the buttons and view to navigate',
                color=helpers.Colour.white())
            embed.set_author(name=ctx.author, icon_url=ctx.author.display_avatar.url)
            embed.set_footer(text=f'{pluralize(len(playlists)):playlist}')
            for name, value in field[index:index + 12]:
                embed.add_field(name=name, value=value, inline=False)
            embeds.append(embed)

        await PlaylistPaginator.start(
            ctx, entries=embeds, per_page=1, ephemeral=True, playlists=playlists, start_pages=embeds)

    @playlist.command(
        name='create',
        description='Create a new playlist.'
    )
    @app_commands.describe(name='The name of your new playlist.')
    async def playlist_create(self, ctx: Context, name: str) -> None:
        """Create a new playlist."""
        playlists = await self.get_playlists(ctx.author.id)

        if len(playlists) == 3 and not await self.bot.is_owner(ctx.author._user):
            return await ctx.send_error('You can only have `3` playlists at the same time.')

        if any(playlist.name == name for playlist in playlists):
            return await ctx.send_error('There is already a playlist with this name, please choose another name.')

        if len(name) > 100:
            return await ctx.send_error('The name of the playlist must be 100 characters or less.')

        query = "INSERT INTO playlist (user_id, name, created) VALUES ($1, $2, $3) RETURNING id;"
        playlist_id = await self.bot.db.fetchval(query, ctx.author.id, name, discord.utils.utcnow())
        self.get_playlists.invalidate(ctx.author.id)

        await ctx.send_success(f'Successfully created playlist **{name}** [`{playlist_id}`].')

    @playlist.command(
        name='play',
        description='Add the songs from you playlist to the plugins queue and play them.',
    )
    @app_commands.rename(name_or_id='name-or-id')
    @app_commands.describe(name_or_id='The name or id of your playlist to play.')
    @app_commands.autocomplete(name_or_id=playlist_autocomplete)  # type: ignore
    @checks.is_listen_together()
    @checks.is_author_connected()
    async def playlist_play(
            self,
            ctx: Context,
            *,
            name_or_id: Annotated[str | int, PlaylistNameOrID(lower=True, with_id=True)],  # type: ignore
    ) -> None:
        """Add the songs from you playlist to the plugins queue and play them."""
        playlist = await self.get_playlist(ctx, name_or_id)
        if playlist is None:
            return await ctx.send_error('There is no playlist with this id.')

        if len(playlist) == 0:
            return await ctx.send_error('There are no tracks in this playlist, please add some using `/playlist add`.')

        player: Player = cast(Player, ctx.voice_client)
        if not player:
            player = await Player.join(ctx)

        old_stamp = len(player.queue.all) if not None else 0
        wait_message = await ctx.send(f'*{Emojis.loading} adding tracks from your playlist to the queue... please wait...*')

        for track in playlist.tracks:
            resolved: Playable = await player.search(track.url, ctx=ctx)
            if not track:
                continue
            await player.queue.put_wait(resolved)

        new_queue = len(player.queue.all) - old_stamp
        succeeded = bool(new_queue == len(playlist.tracks))

        embed = discord.Embed(
            description=f'`🎶` Successfully added **{new_queue}/{len(playlist.tracks)}** tracks from your playlist to the queue.',
            color=helpers.Colour.teal())
        if not succeeded:
            embed.description += f'\n{Emojis.warning} *Some tracks may not have been added due to unexpected issues.*'
        embed.set_author(name=f'[{playlist.id}] • {playlist.name}', icon_url=ctx.author.display_avatar.url)
        embed.set_footer(text='Now Playing')
        await wait_message.delete()
        await ctx.send(embed=embed, delete_after=15)

        if not player.playing:
            player.autoplay = wavelink.AutoPlayMode.enabled
            await player.play(player.queue.get(), volume=70)
        else:
            await player.panel.update()

    @playlist.command(
        name='add',
        description='Adds the current playing track or a track via a direct-url to your playlist.'
    )
    @app_commands.rename(name_or_id='name-or-id')
    @app_commands.describe(
        query='The direct-url of the track/playlist/album you want to add to your playlist.',
        name_or_id='The id of your playlist.')
    @app_commands.autocomplete(name_or_id=playlist_autocomplete)
    async def playlist_add(
            self,
            ctx: Context,
            name_or_id: Annotated[str | int, PlaylistNameOrID(lower=True, with_id=True)],  # type: ignore
            *,
            query: str | None = None
    ) -> None:
        """Adds the current playing track or a track via a direct-url to your playlist."""
        if not query and not (ctx.voice_client and ctx.voice_client.channel):
            return await ctx.send_error('You have to provide either the `link` parameter or a current playing track.')

        playlist = await self.get_playlist(ctx, name_or_id)
        if playlist is None:
            return await ctx.send_error('There is no playlist with that name.')

        if not query and ctx.guild.voice_client:
            player: Player = cast(Player, ctx.voice_client)

            if not player.current:
                return await ctx.send_error(
                    'You have to provide either the `link` parameter or a current playing track.')

            await playlist.add_track(player.current)
            embed = discord.Embed(
                description=f'Added Track **[{player.current.title}]({player.current.uri})** to your playlist '
                            f'at Position **#{len(playlist.tracks)}**',
                color=helpers.Colour.teal()
            )
            embed.set_thumbnail(url=player.current.artwork)
            embed.set_author(name=ctx.author, icon_url=ctx.author.display_avatar.url)
            embed.set_footer(text=f'[{playlist.id}] • {playlist.name}')
            await ctx.send(embed=embed, ephemeral=True)
        else:
            result = await Player.search(query, ctx=ctx)
            if isinstance(result, SearchReturn):
                if result == SearchReturn.NO_RESULTS:
                    await ctx.send_error('Sorry! No results found matching your query.')
                elif result == SearchReturn.NO_YOUTUBE_ALLOWED:
                    await ctx.send_error('Sorry, you can\'t add YouTube tracks with this bot.')
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
                    description=f'Added **{success}**/**{len(result.tracks)}** Tracks from {result.name} **[{result.name}]({result.url})** to your playlist.\n'
                                f'Next Track at Position **#{len(playlist.tracks)}**',
                    color=helpers.Colour.teal())
                embed.set_thumbnail(url=result.artwork)
                embed.set_author(name=ctx.author, icon_url=ctx.author.display_avatar.url)
                embed.set_footer(text=f'[{playlist.id}] • {playlist.name}')
                await ctx.send(embed=embed, ephemeral=True)
            else:
                if result.uri in added:
                    return await ctx.send_error('This Track is already in your playlist.')
                await playlist.add_track(result)

                embed = discord.Embed(
                    description=f'Added Track **[{result.title}]({result.uri})** to your playlist.\n'
                                f'Track at Position **#{len(playlist.tracks)}**',
                    color=helpers.Colour.teal())
                embed.set_thumbnail(url=result.artwork)
                embed.set_author(name=ctx.author, icon_url=ctx.author.display_avatar.url)
                embed.set_footer(text=f'[{playlist.id}] • {playlist.name}')
                await ctx.send(embed=embed, ephemeral=True)

        self.get_playlists.invalidate(ctx.author.id)

    @playlist.command(
        name='delete',
        alias='del',
        description='Delete a playlist.'
    )
    @app_commands.rename(name_or_id='name-or-id')
    @app_commands.describe(name_or_id='The name or id of the playlist you want to delete.')
    @app_commands.autocomplete(name_or_id=playlist_autocomplete)
    async def playlist_delete(
            self,
            ctx: Context,
            *,
            name_or_id: Annotated[str | int, PlaylistNameOrID(lower=True, with_id=True)],  # type: ignore
    ) -> None:
        """Delete a playlist."""
        playlist = await self.get_playlist(ctx, name_or_id, pass_tracks=True)
        if playlist is None:
            return await ctx.send_error('No playlist was found matching your query.')

        if playlist.name == 'Liked Songs':
            return await ctx.send_error('You cannot delete the Liked Songs playlist.')

        await playlist.delete()
        await ctx.send_success(
            f'Successfully deleted playlist **{playlist.name}** [`{playlist.id}`] and all corresponding entries.',
            ephemeral=True)
        self.get_playlists.invalidate(ctx.author.id)

    @playlist.command(
        name='clear',
        aliases=['purge', 'clean'],
        description='Clear all Items in a playlist.',
        guild_only=True
    )
    @app_commands.rename(name_or_id='name-or-id')
    @app_commands.describe(name_or_id='The name or id of the playlist you want to clear.')
    @app_commands.autocomplete(name_or_id=playlist_autocomplete)
    async def playlist_clear(
            self,
            ctx: Context,
            *,
            name_or_id: Annotated[str | int, PlaylistNameOrID(lower=True, with_id=True)],  # type: ignore
    ) -> None:
        """Clear all Items in a playlist."""
        playlist = await self.get_playlist(ctx, name_or_id, pass_tracks=True)
        if playlist is None:
            return await ctx.send_error('No playlist was found matching your query.')

        await playlist.clear()
        await ctx.send_success(
            f'Successfully purged all corresponding entries of playlist **{playlist.name}** [`{playlist.id}`].',
            ephemeral=True)
        self.get_playlists.invalidate(ctx.author.id)

    @playlist.command(
        name='remove',
        alias='rm',
        description='Remove a track from your playlist.',
        guild_only=True
    )
    @app_commands.rename(name_or_id='name-or-id')
    @app_commands.describe(
        name_or_id='The playlist ID you want to remove a track from.',
        track_id='The ID of the track to remove.')
    @app_commands.autocomplete(name_or_id=playlist_autocomplete)
    async def playlist_remove(
            self,
            ctx: Context,
            name_or_id: Annotated[str | int, PlaylistNameOrID(lower=True, with_id=True)],  # type: ignore
            track_id: int
    ) -> None:
        """Remove a track from your playlist."""
        playlist = await self.get_playlist(ctx, name_or_id)
        if playlist is None:
            return await ctx.send_error('No playlist was found matching your query.')

        track = discord.utils.get(playlist.tracks, id=track_id)
        if not track:
            return await ctx.send_error('No track was found matching your query.')

        await playlist.remove_track(track)
        await ctx.send_success(
            f'Successfully removed track **{track.name}** [`{track.id}`] from playlist **{playlist.name}** [`{playlist.id}`].',
            ephemeral=True)
