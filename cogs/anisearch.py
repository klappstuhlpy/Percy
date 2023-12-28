from __future__ import annotations
import datetime
import inspect
import time
from typing import Dict, Any, Optional, List, MutableMapping
from urllib.parse import urljoin

import aiohttp
import discord
from discord import app_commands, DiscordException
from discord.ext import commands, tasks
from lru import LRU

from bot import Percy
from .utils import fuzzy, commands_ext, errors
from .utils.anisearch import _formatter
from .utils.context import Context
from .utils.anisearch._client import AniListClient
from .utils.anisearch._formatter import month_to_season, ANILIST_ICON, ANILIST_LOGO
from .utils.paginator import EmbedPaginator

GRANT_URL = "https://anilist.co/api/v2/oauth/"
OAUTH_URL = GRANT_URL + "authorize?client_id={client_id}&redirect_uri={redirect_uri}&response_type=code"


class AniListRequestError(DiscordException):
    """A subclass Exception for failed AniList API requests."""

    def __init__(self, response: aiohttp.ClientResponse, message: Dict[str, Any]) -> None:
        self.response: aiohttp.ClientResponse = response
        self.status: int = response.status

        reason = message.get('message', '')
        error = message.get('error', '')

        fmt = '{0.status} {0.reason} (type: {1})'
        if reason:
            fmt += ': {2}'
        super().__init__(fmt.format(self.response, error, reason))


class EnterCodeModal(discord.ui.Modal, title='Enter AniList Code'):
    code = discord.ui.TextInput(label='Code', placeholder='e.g. def50202deda18...',
                                max_length=4000, min_length=1, style=discord.TextStyle.long,
                                required=True)

    def __init__(self, bot: Percy) -> None:
        self.bot: Percy = bot
        super().__init__(timeout=90.0)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        anilist: AniListSearch = self.bot.get_cog("Media")  # type: ignore
        if not anilist:
            return

        grant, expires = await anilist.get_access_token(self.code.value)
        if (grant, expires) == (None, None):
            return await interaction.response.send_message(
                "<:redTick:1079249771975413910> Invalid code provided.", ephemeral=True
            )

        anilist.access_tokens[interaction.user.id] = (grant, time.time() + (int(expires) - 10))

        await interaction.response.send_message("<:greenTick:1079249732364406854> Successfully linked profile.",
                                                ephemeral=True)
        try:
            await interaction.message.delete()
        except:
            pass
        self.stop()


class AniListLinkView(discord.ui.View):
    def __init__(self, ctx: Context | discord.Interaction, url: str):
        super().__init__(timeout=100.0)
        self.ctx: Context | discord.Interaction = ctx

        self.add_item(discord.ui.Button(label="Link AniList", style=discord.ButtonStyle.link, url=url))

    @discord.ui.button(label="Enter Code", style=discord.ButtonStyle.green)
    async def enter_code(self, button: discord.ui.Button, interaction: discord.Interaction):  # noqa
        await interaction.response.send_modal(EnterCodeModal(self.ctx.bot))


def is_bearer_valid(reverse=False):
    """Check if the user has a valid bearer token.

    This check will fail if the user does not have a bearer token or if the token has expired.
    """

    def func(ctx: Context):
        cog: AniListSearch = ctx.cog  # type: ignore
        if reverse:
            return ctx.author.id not in cog.access_tokens or cog.access_tokens.get(ctx.author.id)[1] < time.time()

        if ctx.author.id not in cog.access_tokens:
            return False
        return cog.access_tokens.get(ctx.author.id)[1] > time.time()

    return commands.check(func)


class AniListSearch(commands.Cog, name="Media"):
    """Search for anime and manga on AniList."""

    def __init__(self, bot: Percy) -> None:
        self.bot: Percy = bot

        self.anilistcls: AniListClient = AniListClient(self.bot.session)
        self.config = self.bot.config.anilist
        self._embed_builder = _formatter.AniListEmbedBuilder(self.bot)

        self.access_tokens: MutableMapping[int, (str, int)] = LRU(1000)  # type: ignore

        self._lookup_anime_table: List[str] = []
        self._lookup_manga_table: List[str] = []

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name="\N{TELEVISION}")

    async def cog_load(self) -> None:
        self.clear_token_cache.start()

        data = await self.anilistcls.media(page=1, perPage=50, type="ANIME", isAdult=False, sort='TRENDING_DESC')
        for item in data:
            self._lookup_anime_table.append(item.get('title').get('romaji'))

        data = await self.anilistcls.media(page=1, perPage=50, type="MANGA", isAdult=False, sort='TRENDING_DESC')
        for item in data:
            self._lookup_manga_table.append(item.get('title').get('romaji'))

    async def cog_unload(self) -> None:
        self.clear_token_cache.cancel()

    async def cog_command_error(self, ctx: Context, error: commands.CommandError):
        if isinstance(error, commands.CheckFailure):
            if ctx.command.name == "link":
                await ctx.stick(False, "You have already linked your AniList account to your Discord account."
                )
            else:
                await ctx.stick(False, "You have not linked your AniList account to your Discord account or your Login has expired.\n"
                    f"Use `{ctx.clean_prefix}anilist link` to link your account."
                )

    @tasks.loop(hours=4)
    async def clear_token_cache(self):
        for user_id, (_, expires) in self.access_tokens.items():
            if expires < time.time():
                del self.access_tokens[user_id]

    @property
    def grant_headers(self) -> dict:
        return {'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json'}

    def grant_params(self, code: str) -> dict:
        return {"grant_type": "authorization_code",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "redirect_uri": self.config.redirect_uri,
                "code": code.strip()}

    def bearer_headers(self, user_id: int) -> dict:
        return {'Authorization': f'Bearer {self.access_tokens[user_id][0]}',
                'Content-Type': 'application/json',
                'Accept': 'application/json'}

    @property
    def format_oauth_url(self) -> str:
        return OAUTH_URL.format(client_id=self.config.client_id, redirect_uri=self.config.redirect_uri)

    async def get_access_token(self, code: str) -> (str, int):
        async with self.bot.session.post(urljoin(GRANT_URL, 'token'), data=self.grant_params(code),
                                         headers=self.grant_headers) as resp:
            data = await resp.json()
            if resp.status != 200:
                if data.get('error') == 'invalid_request':
                    return None, None

                raise AniListRequestError(resp, data)

            return data.get('access_token'), data.get('expires_in')

    async def media_prompt_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.command.name == "anime-search":
            table = self._lookup_anime_table
        else:
            table = self._lookup_manga_table

        if not current:
            return [app_commands.Choice(name=m, value=m) for m in table][:15]

        matches = fuzzy.finder(current, table, key=lambda x: x)
        return [app_commands.Choice(name=m, value=m) for m in matches][:15]

    @commands_ext.command(
        commands.hybrid_group,
        name="anilist",
        description="Handles all AniList related commands",
        invoke_without_command=True,
    )
    async def anilist(self, ctx: Context):
        """Handles all AniList related commands."""
        await ctx.send_help(ctx.command)

    @commands_ext.command(
        anilist.command,
        name="link",
        description="Links your AniList account to your Discord account",
    )
    @is_bearer_valid(reverse=True)
    async def anilist_link(self, ctx: Context):
        """Links your AniList account to your Discord account."""
        if ctx.interaction:
            await ctx.defer(ephemeral=True)

        embed = discord.Embed(
            title="AniList Linking",
            description=inspect.cleandoc("""
                Click on the button below. It'll redirect you to the AniList website where you will then be prompted
                to authorize us. Don't worry, this process is 100% secure.
                
                You will then be redirected to our site. Copy the code our site gives you and use the second button below to pas
                it to us. Once you've done that, you're all set!
            """),
            color=self.bot.colour.darker_red(),
            url="https://anilist.co/",
        )
        embed.set_thumbnail(url=ANILIST_ICON)
        embed.set_footer(text='Provided by AniList', icon_url=ANILIST_LOGO)

        view = AniListLinkView(ctx, self.format_oauth_url)
        await ctx.send(embed=embed, view=view, ephemeral=True)

    @commands_ext.command(
        anilist.group,
        name='profile',
        description='Displays information about the given AniList user',
        invoke_without_command=True,
    )
    @is_bearer_valid()
    async def anilist_profile(self, ctx: Context):
        """Displays information about the given AniList user."""
        await ctx.channel.typing()

        if data := await self.anilistcls.user(headers=self.bearer_headers(ctx.author.id)):
            embed = self._embed_builder.user(data)
            await ctx.send(embed=embed)

    @commands_ext.command(
        commands.hybrid_group,
        name='anime',
        description='Searches for an anime with the given title and displays information about the search results',
        invoke_without_command=True,
    )
    async def anime(self, ctx: Context, *, prompt: str):
        """Searches for an anime with the given title and displays information about the search results."""
        await ctx.invoke(self.anime_search, ctx, prompt=prompt)  # type: ignore

    @commands_ext.command(
        anime.command,
        name='search',
        description='Searches for an anime with the given title and displays information about the search results',
    )
    @app_commands.describe(prompt='The title of the anime to search for', limit='The number of results to return')
    @app_commands.autocomplete(prompt=media_prompt_autocomplete)  # type: ignore
    async def anime_search(
            self, ctx: Context, *, prompt: str, limit: Optional[app_commands.Range[int, 1, 30]] = 15
    ):
        """Searches for an anime with the given title and displays information about the search results."""
        if ctx.interaction:
            await ctx.defer()
        else:
            await ctx.channel.typing()

        if data := await self.anilistcls.media(page=1, perPage=limit, type='ANIME', isAdult=False, search=prompt):
            embeds = [await self._embed_builder.media(anime) for anime in data]
            await EmbedPaginator.start(ctx, entries=embeds, per_page=1)
        else:
            raise errors.BadArgument(f'An anime with the title {prompt!r} could not be found.')

    @commands_ext.command(
        commands.hybrid_group,
        name='manga',
        description='Searches for a manga with the given title and displays information about the search results',
        invoke_without_command=True,
    )
    async def manga(self, ctx: Context, *, prompt: str):
        """Searches for a manga with the given title and displays information about the search results."""
        await ctx.invoke(self.manga_search, ctx, prompt=prompt)  # type: ignore

    @commands_ext.command(
        manga.command,
        name='search',
        description='Searches for a manga with the given title and displays information about the search results',
    )
    @app_commands.describe(prompt='The title of the manga to search for', limit='The number of results to return')
    @app_commands.autocomplete(prompt=media_prompt_autocomplete)  # type: ignore
    async def manga_search(
            self, ctx: Context, *, prompt: str, limit: Optional[app_commands.Range[int, 1, 30]] = 15
    ):
        """Searches for a manga with the given title and displays information about the search results."""
        if ctx.interaction:
            await ctx.defer()
        else:
            await ctx.channel.typing()

        if data := await self.anilistcls.media(page=1, perPage=limit, type='MANGA', isAdult=False, search=prompt):
            embeds = [await self._embed_builder.media(manga) for manga in data]
            await EmbedPaginator.start(ctx, entries=embeds, per_page=1)
        else:
            raise errors.BadArgument(f'A manga with the title {prompt!r} could not be found.')

    @commands_ext.command(
        commands.hybrid_command,
        name='character-search',
        description='Searches for a character with the given name and displays information about the search results',
    )
    @app_commands.describe(prompt='The name of the character to search for', limit='The number of results to return')
    async def character(
            self, ctx: Context, *, prompt: str, limit: Optional[app_commands.Range[int, 1, 30]] = 15
    ):
        """Searches for a character with the given name and displays information about the search results."""
        if ctx.interaction:
            await ctx.defer()
        else:
            await ctx.channel.typing()

        if data := await self.anilistcls.character(page=1, perPage=limit, search=prompt):
            embeds = [self._embed_builder.character(character) for character in data]
            await EmbedPaginator.start(ctx, entries=embeds, per_page=1)
        else:
            raise errors.BadArgument(f'A character with the name {prompt!r} could not be found.')

    async def trending(self, ctx: Context, media: str):
        """Displays the current trending anime or manga."""
        if ctx.interaction:
            await ctx.defer()
        else:
            await ctx.channel.typing()

        data, embeds = (
            await self.anilistcls.media(page=1, perPage=15, type=media.upper(), isAdult=False, sort='TRENDING_DESC'), []
        )

        for rank, anime in enumerate(data, start=1):
            embed = self._embed_builder.short_media(anime)
            embed.set_author(name=f'{embed.author.name} | #{rank} Trending {media}', icon_url=ANILIST_LOGO)
            embeds.append(embed)

        await EmbedPaginator.start(ctx, entries=embeds, per_page=1)

    @commands_ext.command(
        anime.command,
        name='trending',
        description='Displays the current trending anime',
    )
    async def trending_anime(self, ctx: Context):
        """Displays the current trending anime."""
        await self.trending(ctx, 'anime')

    @commands_ext.command(
        manga.command,
        name='trending',
        description='Displays the current trending manga',
    )
    async def trending_manga(self, ctx: Context):
        """Displays the current trending manga."""
        await self.trending(ctx, 'manga')

    @commands_ext.command(
        anime.command,
        name='seasonal',
        description='Displays the currently airing anime'
    )
    async def seasonal(self, ctx: Context):
        """Displays the currently airing anime."""
        if ctx.interaction:
            await ctx.defer()
        else:
            await ctx.channel.typing()

        date = datetime.datetime.now()
        season = month_to_season(date.month)
        year = date.year

        data, embeds = (
            await self.anilistcls.media(
                season=season, seasonYear=year, page=1, type='ANIME', isAdult=False, sort='POPULARITY_DESC'
            ), []
        )

        for anime in data:
            embed = self._embed_builder.short_media(anime)
            embed.set_author(name=f'{embed.author.name} | {season} {year}', icon_url=ANILIST_LOGO)
            embeds.append(embed)

        await EmbedPaginator.start(ctx, entries=embeds[:3], per_page=1)


async def setup(bot: Percy) -> None:
    await bot.add_cog(AniListSearch(bot))
