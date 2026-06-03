from __future__ import annotations

import datetime
import inspect
import logging
from typing import Any
from urllib.parse import urljoin

import aiohttp
import discord
from discord import DiscordException, app_commands
from discord.app_commands import Choice
from discord.ext import commands
from discord.ext.commands import Range

from app.core import Bot, Cog, Context, Flags, describe, flag, group
from app.core.pagination import EmbedPaginator
from app.utils import fuzzy, helpers
from config import anilist

from .client import AniListClient
from .models import AniListExpiringCache
from .ui import ANILIST_ICON, ANILIST_LOGO, AniListEmbedBuilder, AniListLinkView, month_to_season

log = logging.getLogger(__name__)

GRANT_URL = 'https://anilist.co/api/v2/oauth/'
OAUTH_URL = GRANT_URL + 'authorize?client_id={client_id}&redirect_uri={redirect_uri}&response_type=code'


class AniListSearchFlags(Flags):
    limit: Range[int, 1, 30] = flag(description='The number of results to return', default=15)


class AniListRequestError(DiscordException):
    """A subclass Exception for failed AniList API requests."""

    def __init__(self, response: aiohttp.ClientResponse, data: dict[str, Any]) -> None:
        self.response: aiohttp.ClientResponse = response
        self.status: int = response.status

        reason = data.pop('message', None)
        error = data.pop('error', None)

        fmt = '{0.status} {0.reason} (type: {1})'
        if reason:
            fmt += ': {2}'
        super().__init__(fmt.format(self.response, error, reason))


def is_bearer_valid(reverse: bool = False) -> Check[Any]:
    """Check if the user has a valid bearer token.

    This check will fail if the user does not have a bearer token or if the token has expired.
    """

    def func(ctx: Context) -> bool:
        cog: AniList = ctx.cog  # type: ignore
        if reverse:
            return cog.access_tokens.get(ctx.author.id) is None
        return cog.access_tokens.get(ctx.author.id) is not None

    return commands.check(func)


class AniList(Cog):
    """Search for anime and manga on AniList."""

    emoji = '\N{TELEVISION}'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

        self.aniclient: AniListClient = AniListClient(self.bot.session)
        self._embed_builder: AniListEmbedBuilder = AniListEmbedBuilder(self.bot.session)
        self.config = anilist

        self.access_tokens: AniListExpiringCache = AniListExpiringCache()

        self._lookup_anime_table: list[str] = []
        self._lookup_manga_table: list[str] = []

    async def cog_load(self) -> None:
        try:
            data = await self.aniclient.media(page=1, perPage=50, type='ANIME', isAdult=False, sort='TRENDING_DESC')
            for item in data:
                title = item.get('title') or {}
                romaji = title.get('romaji')
                if romaji:
                    self._lookup_anime_table.append(str(romaji))

            data = await self.aniclient.media(page=1, perPage=50, type='MANGA', isAdult=False, sort='TRENDING_DESC')
            for item in data:
                title = item.get('title') or {}
                romaji = title.get('romaji')
                if romaji:
                    self._lookup_manga_table.append(str(romaji))
        except (AttributeError, KeyError, aiohttp.ClientError):
            log.exception('Failed to load AniList data')

    async def cog_command_error(self, ctx: Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CheckFailure):
            await ctx.send_error('You have not linked your AniList account to your Discord account or your Login has expired.\n'
                                 f'Use `{ctx.clean_prefix}anilist link` to link your account.')

    @property
    def grant_headers(self) -> dict:
        return {'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json'}

    def grant_params(self, code: str) -> dict:
        return {'grant_type': 'authorization_code',
                'client_id': self.config.client_id,
                'client_secret': self.config.client_secret,
                'redirect_uri': self.config.redirect_uri,
                'code': code.strip()}

    def bearer_headers(self, user_id: int) -> dict:
        return {'Authorization': f'Bearer {self.access_tokens.get(user_id)}',
                'Content-Type': 'application/json',
                'Accept': 'application/json'}

    @property
    def format_oauth_url(self) -> str:
        return OAUTH_URL.format(client_id=self.config.client_id, redirect_uri=self.config.redirect_uri)

    async def get_access_token(self, code: str) -> tuple[str, int] | None:
        async with self.bot.session.post(
                urljoin(GRANT_URL, 'token'),
                data=self.grant_params(code),
                headers=self.grant_headers
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                if data.get('error') == 'invalid_request':
                    return None

                raise AniListRequestError(resp, data)

            return data.get('access_token'), data.get('expires_in')

    async def media_prompt_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[Choice[str | int | float]]:
        table = self._lookup_anime_table if interaction.command.name == 'anime-search' else self._lookup_manga_table

        if not current:
            return [app_commands.Choice(name=m, value=m) for m in table][:15]

        matches = fuzzy.finder(current, table)
        return [app_commands.Choice(name=m, value=m) for m in matches][:15]

    @group(
        'anilist',
        description='Handles all AniList related commands',
        invoke_without_command=True,
        hybrid=True
    )
    async def _anilist(self, ctx: Context) -> None:
        """Handles all AniList related commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @_anilist.command(
        'link',
        description='Links your AniList account to your Discord account',
    )
    @is_bearer_valid(reverse=True)
    async def anilist_link(self, ctx: Context) -> None:
        """Links your AniList account to your Discord account."""
        await ctx.defer(ephemeral=True)

        if self.access_tokens.get(ctx.author.id):
            await ctx.send_error('You have already linked your AniList account to your Discord account.')
            return

        embed = discord.Embed(
            title='AniList Linking',
            description=inspect.cleandoc("""
                Click on the button below. It'll redirect you to the AniList website where you will then be prompted
                to authorize us. Don't worry, this process is 100% secure.

                You will then be redirected to our site. Copy the code our site gives you and use the second button below to pas
                it to us. Once you've done that, you're all set!
            """),
            color=helpers.Colour.white(),
            url='https://anilist.co/',
        )
        embed.set_thumbnail(url=ANILIST_ICON)
        embed.set_footer(text='Provided by AniList', icon_url=ANILIST_LOGO)

        view = AniListLinkView(ctx, self.format_oauth_url)
        await ctx.send(embed=embed, view=view, ephemeral=True)

    @_anilist.group(
        'profile',
        description='Displays information about the given AniList user',
        invoke_without_command=True,
        hybrid=True
    )
    @is_bearer_valid()
    async def anilist_profile(self, ctx: Context) -> None:
        """Displays information about the given AniList user."""
        await ctx.defer(typing=True)

        if data := await self.aniclient.user(headers=self.bearer_headers(ctx.author.id)):
            embed = self._embed_builder.user(data)
            await ctx.send(embed=embed)

    @_anilist.group(
        'anime',
        fallback='search',
        description='Searches for an anime with the given title and displays information about the search results',
        invoke_without_command=True,
        hybrid=True
    )
    @describe(prompt='The title of the anime to search for')
    @app_commands.autocomplete(prompt=media_prompt_autocomplete)  # type: ignore
    async def anime(self, ctx: Context, *, prompt: str, flags: AniListSearchFlags) -> None:
        """Searches for an anime with the given title and displays information about the search results."""
        await ctx.defer(typing=True)

        if data := await self.aniclient.media(page=1, perPage=flags.limit, type='ANIME', isAdult=False, search=prompt):
            embeds = [await self._embed_builder.media(anime) for anime in data]
            await EmbedPaginator.start(ctx, entries=embeds, per_page=1)
        else:
            raise commands.BadArgument(f'An anime with the title {prompt!r} could not be found.')

    @_anilist.group(
        'manga',
        fallback='search',
        description='Searches for a manga with the given title and displays information about the search results',
        invoke_without_command=True,
        hybrid=True
    )
    @describe(prompt='The title of the anime to search for')
    @app_commands.autocomplete(prompt=media_prompt_autocomplete)  # type: ignore
    async def manga(self, ctx: Context, *, prompt: str, flags: AniListSearchFlags) -> None:
        """Searches for a manga with the given title and displays information about the search results."""
        await ctx.defer(typing=True)

        if data := await self.aniclient.media(page=1, perPage=flags.limit, type='MANGA', isAdult=False, search=prompt):
            embeds = [await self._embed_builder.media(manga) for manga in data]
            await EmbedPaginator.start(ctx, entries=embeds, per_page=1)
        else:
            raise commands.BadArgument(f'A manga with the title {prompt!r} could not be found.')

    @_anilist.command(
        'character',
        aliases=['char'],
        description='Searches for a character with the given name and displays information about the search results',
    )
    @describe(prompt='The name of the character to search for')
    async def character(self, ctx: Context, *, prompt: str, flags: AniListSearchFlags) -> None:
        """Searches for a character with the given name and displays information about the search results."""
        await ctx.defer(typing=True)

        if data := await self.aniclient.character(page=1, perPage=flags.limit, search=prompt):
            embeds = [self._embed_builder.character(character) for character in data]
            await EmbedPaginator.start(ctx, entries=embeds, per_page=1)
        else:
            raise commands.BadArgument(f'A character with the name {prompt!r} could not be found.')

    async def trending(self, ctx: Context, media: str) -> None:
        """Displays the current trending anime or manga."""
        await ctx.defer(typing=True)

        data, embeds = (
            await self.aniclient.media(page=1, perPage=15, type=media.upper(), isAdult=False, sort='TRENDING_DESC'), []
        )

        for rank, anime in enumerate(data, start=1):
            embed = self._embed_builder.short_media(anime)
            embed.set_author(name=f'{embed.author.name} | #{rank} Trending {media}', icon_url=ANILIST_LOGO)
            embeds.append(embed)

        await EmbedPaginator.start(ctx, entries=embeds, per_page=1)

    @anime.command(
        'trending',
        description='Displays the current trending anime',
    )
    async def trending_anime(self, ctx: Context) -> None:
        """Displays the current trending anime."""
        await self.trending(ctx, 'anime')

    @manga.command(
        'trending',
        description='Displays the current trending manga',
    )
    async def trending_manga(self, ctx: Context) -> None:
        """Displays the current trending manga."""
        await self.trending(ctx, 'manga')

    @anime.command(
        'seasonal',
        description='Displays the currently airing anime'
    )
    async def seasonal(self, ctx: Context) -> None:
        """Displays the currently airing anime."""
        await ctx.defer(typing=True)

        date = datetime.datetime.now()
        season = month_to_season(date.month)
        year = date.year

        data, embeds = (
            await self.aniclient.media(
                season=season, seasonYear=year, page=1, type='ANIME', isAdult=False, sort='POPULARITY_DESC'
            ), []
        )

        for anime in data:
            embed = self._embed_builder.short_media(anime)
            embed.set_author(name=f'{embed.author.name} | {season} {year}', icon_url=ANILIST_LOGO)
            embeds.append(embed)

        await EmbedPaginator.start(ctx, entries=embeds[:3], per_page=1)


async def setup(bot: Bot) -> None:
    await bot.add_cog(AniList(bot))
