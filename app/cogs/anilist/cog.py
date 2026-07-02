from __future__ import annotations

import datetime
import logging
from typing import Any
from urllib.parse import urljoin

import aiohttp
import discord
from discord import DiscordException, app_commands
from discord.app_commands import Choice
from discord.ext import commands
from discord.ext.commands import Range  # noqa: TC002 -- flag/command param annotations are evaluated at runtime

from app.core import Bot, Cog, Context, Flags, LayoutView, describe, flag, group
from app.core.pagination import EmbedPaginator
from app.utils import fuzzy
from config import anilist

from .client import AniListClient
from .ui import (
    ANILIST_LOGO,
    AniListCardBuilder,
    AniListEmbedBuilder,
    AniListLinkView,
    MediaCardView,
    month_to_season,
)

log = logging.getLogger(__name__)

GRANT_URL = 'https://anilist.co/api/v2/oauth/'
OAUTH_URL = GRANT_URL + 'authorize?client_id={client_id}&redirect_uri={redirect_uri}&response_type=code'

LIST_STATUS_CHOICES = [
    Choice(name='Watching / Reading', value='CURRENT'),
    Choice(name='Completed', value='COMPLETED'),
    Choice(name='Paused', value='PAUSED'),
    Choice(name='Dropped', value='DROPPED'),
    Choice(name='Planning', value='PLANNING'),
]


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


def is_bearer_valid(reverse: bool = False) -> Any:
    """Check if the user has a valid bearer token stored in the database."""

    async def func(ctx: Context) -> bool:
        cog: AniList = ctx.cog  # type: ignore
        has_token = await cog.has_valid_token(ctx.author.id)
        if reverse:
            return not has_token
        return has_token

    return commands.check(func)


class AniList(Cog):
    """Search for anime and manga on AniList."""

    emoji = '\N{TELEVISION}'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

        self.aniclient: AniListClient = AniListClient(self.bot.session)
        self._embed_builder: AniListEmbedBuilder = AniListEmbedBuilder(self.bot.session)
        self._card_builder: AniListCardBuilder = AniListCardBuilder(self.bot.session)
        self.config = anilist

        # In-memory cache of user_id -> (token, expires_at) to avoid DB hits on every command
        self._token_cache: dict[int, tuple[str, datetime.datetime]] = {}

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
            await ctx.send_error('You have not linked your AniList account or your login has expired.\n'
                                 f'Use `{ctx.clean_prefix}anilist link` to link your account.')

    # ─── Token Management (Phase 1) ──────────────────────────────────────────

    async def has_valid_token(self, user_id: int) -> bool:
        """Check if a user has a non-expired token."""
        if cached := self._token_cache.get(user_id):
            _, expires_at = cached
            if expires_at > datetime.datetime.now(datetime.UTC):
                return True
            del self._token_cache[user_id]

        row = await self.bot.db.anilist.get_token(user_id)
        if row is None:
            return False

        token, expires_at = row
        if expires_at <= datetime.datetime.now(datetime.UTC):
            await self.bot.db.anilist.delete_token(user_id)
            return False

        self._token_cache[user_id] = (token, expires_at)
        return True

    def is_user_linked(self, user_id: int) -> bool:
        """Fast check for UI rendering — uses only the in-memory cache."""
        if cached := self._token_cache.get(user_id):
            _, expires_at = cached
            return expires_at > datetime.datetime.now(datetime.UTC)
        return False

    async def store_token(self, user_id: int, access_token: str, expires_in: int) -> None:
        """Store an OAuth token in both DB and local cache."""
        expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=expires_in)
        await self.bot.db.anilist.upsert_token(user_id, access_token, expires_at)
        self._token_cache[user_id] = (access_token, expires_at)

    async def bearer_headers(self, user_id: int) -> dict[str, str] | None:
        """Get bearer headers for authenticated requests. Returns None if expired."""
        if not await self.has_valid_token(user_id):
            return None

        token = self._token_cache[user_id][0]
        return {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    # ─── OAuth Flow ───────────────────────────────────────────────────────────

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

    # ─── Commands ─────────────────────────────────────────────────────────────

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

        if await self.has_valid_token(ctx.author.id):
            await ctx.send_error('You have already linked your AniList account to your Discord account.')
            return

        view = AniListLinkView(
            ctx, self.format_oauth_url,
            content=(
                "## AniList Linking\n"
                "Click on the button below. It'll redirect you to the AniList website where you will "
                "then be prompted to authorize us. Don't worry, this process is 100% secure.\n\n"
                "You will then be redirected to our site. Copy the code our site gives you and use the "
                "second button below to pass it to us. Once you've done that, you're all set!\n\n"
                "-# Provided by AniList"
            ),
        )
        await ctx.send(view=view, ephemeral=True)

    @_anilist.command(
        'unlink',
        aliases=['logout'],
        description='Unlinks your AniList account from your Discord account',
    )
    @is_bearer_valid()
    async def anilist_unlink(self, ctx: Context) -> None:
        """Removes the stored AniList token."""
        await self.bot.db.anilist.delete_token(ctx.author.id)
        self._token_cache.pop(ctx.author.id, None)
        await ctx.send_success('Your AniList account has been unlinked.')

    # ─── Profile & Favourites (Phase 3) ──────────────────────────────────────

    @_anilist.group(
        'profile',
        description='Displays information about your AniList profile',
        invoke_without_command=True,
        hybrid=True
    )
    @is_bearer_valid()
    async def anilist_profile(self, ctx: Context) -> None:
        """Displays information about your linked AniList profile."""
        await ctx.defer(typing=True)

        headers = await self.bearer_headers(ctx.author.id)
        if not headers:
            raise commands.CheckFailure()

        if data := await self.aniclient.user(headers=headers):
            container = self._card_builder.user_card(data)
            view = LayoutView(timeout=None)
            view.add_item(container)
            if site_url := data.get('siteUrl'):
                action_row = discord.ui.ActionRow()
                action_row.add_item(discord.ui.Button(label='View on AniList', style=discord.ButtonStyle.link, url=site_url))
                view.add_item(action_row)
            await ctx.send(view=view)

    @anilist_profile.command(
        'favourites',
        description='Displays your AniList favourites',
    )
    @is_bearer_valid()
    async def anilist_favourites(self, ctx: Context) -> None:
        """Displays your favourite anime, manga, and characters."""
        await ctx.defer(typing=True)

        headers = await self.bearer_headers(ctx.author.id)
        if not headers:
            raise commands.CheckFailure()

        if data := await self.aniclient.user_favourites(headers=headers):
            container = self._card_builder.favourites_card(data)
            view = LayoutView(timeout=None)
            view.add_item(container)
            await ctx.send(view=view)

    @_anilist.command(
        'list',
        description='Displays your AniList watch/read list',
    )
    @describe(status='Filter by list status', media_type='Anime or Manga')
    @app_commands.choices(status=LIST_STATUS_CHOICES)
    @is_bearer_valid()
    async def anilist_list(
        self, ctx: Context, *,
        media_type: str = 'ANIME',
        status: str = 'CURRENT',
    ) -> None:
        """Displays entries from your AniList."""
        await ctx.defer(typing=True)

        headers = await self.bearer_headers(ctx.author.id)
        if not headers:
            raise commands.CheckFailure()

        # Get user ID from the Viewer query
        user_data = await self.aniclient.user(headers=headers)
        if not user_data:
            await ctx.send_error('Could not fetch your AniList profile.')
            return

        user_id = user_data.get('id')
        resolved_type = media_type.upper()
        if resolved_type not in ('ANIME', 'MANGA'):
            resolved_type = 'ANIME'

        entries = await self.aniclient.media_list(
            userId=user_id, type=resolved_type, status=status, headers=headers,
        )

        type_label = 'Anime' if resolved_type == 'ANIME' else 'Manga'
        container = self._card_builder.media_list_card(entries, list_type=type_label, status=status)
        view = LayoutView(timeout=None)
        view.add_item(container)
        await ctx.send(view=view)

    # ─── Search Commands ──────────────────────────────────────────────────────

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
            if len(data) == 1:
                # Single result: show rich CV2 card with interactive buttons
                container = self._card_builder.media_card(data[0])
                view = MediaCardView(container, media_data=data[0], cog=self, user_id=ctx.author.id)
                await ctx.send(view=view)
            else:
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
    @describe(prompt='The title of the manga to search for')
    @app_commands.autocomplete(prompt=media_prompt_autocomplete)  # type: ignore
    async def manga(self, ctx: Context, *, prompt: str, flags: AniListSearchFlags) -> None:
        """Searches for a manga with the given title and displays information about the search results."""
        await ctx.defer(typing=True)

        if data := await self.aniclient.media(page=1, perPage=flags.limit, type='MANGA', isAdult=False, search=prompt):
            if len(data) == 1:
                container = self._card_builder.media_card(data[0])
                view = MediaCardView(container, media_data=data[0], cog=self, user_id=ctx.author.id)
                await ctx.send(view=view)
            else:
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
            if len(data) == 1:
                container = self._card_builder.character_card(data[0])
                view = LayoutView(timeout=None)
                view.add_item(container)
                if site_url := data[0].get('siteUrl'):
                    action_row = discord.ui.ActionRow()
                    action_row.add_item(discord.ui.Button(
                        label='View on AniList', style=discord.ButtonStyle.link, url=site_url,
                    ))
                    view.add_item(action_row)
                await ctx.send(view=view)
            else:
                embeds = [self._embed_builder.character(character) for character in data]
                await EmbedPaginator.start(ctx, entries=embeds, per_page=1)
        else:
            raise commands.BadArgument(f'A character with the name {prompt!r} could not be found.')

    # ─── Trending & Seasonal ──────────────────────────────────────────────────

    async def trending(self, ctx: Context, media: str) -> None:
        """Displays the current trending anime or manga."""
        await ctx.defer(typing=True)

        data = await self.aniclient.media(page=1, perPage=15, type=media.upper(), isAdult=False, sort='TRENDING_DESC')
        embeds = []

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

        date = datetime.datetime.now(datetime.UTC)
        season = month_to_season(date.month)
        year = date.year

        data = await self.aniclient.media(
            season=season, seasonYear=year, page=1, type='ANIME', isAdult=False, sort='POPULARITY_DESC'
        )
        embeds = []

        for anime in data:
            embed = self._embed_builder.short_media(anime)
            embed.set_author(name=f'{embed.author.name} | {season} {year}', icon_url=ANILIST_LOGO)
            embeds.append(embed)

        await EmbedPaginator.start(ctx, entries=embeds[:3], per_page=1)

    # ─── Write Operations (Phase 4) ──────────────────────────────────────────

    @_anilist.command(
        'update',
        description='Update a media entry on your AniList',
    )
    @describe(
        title='The title of the anime/manga to update',
        status='New status for this entry',
        progress='Set episode/chapter progress',
        score='Set score (1-10)',
    )
    @app_commands.choices(status=LIST_STATUS_CHOICES)
    @app_commands.autocomplete(title=media_prompt_autocomplete)  # type: ignore
    @is_bearer_valid()
    async def anilist_update(
        self, ctx: Context, *,
        title: str,
        status: str | None = None,
        progress: int | None = None,
        score: Range[float, 1, 10] | None = None,
    ) -> None:
        """Update a media entry on your AniList list."""
        await ctx.defer(typing=True)

        # Search for the media to get its ID
        results = await self.aniclient.media(page=1, perPage=1, search=title, isAdult=False)
        if not results:
            raise commands.BadArgument(f'Could not find a title matching {title!r}.')

        media_id = results[0].get('id')
        if not media_id:
            raise commands.BadArgument(f'Could not resolve media ID for {title!r}.')
        media_id = int(media_id)

        headers = await self.bearer_headers(ctx.author.id)
        if not headers:
            raise commands.CheckFailure()

        result = await self.aniclient.save_media_list_entry(
            media_id=media_id, status=status, progress=progress, score=score, headers=headers,
        )

        if result:
            media_title = results[0].get('title', {}).get('romaji', title)
            parts = []
            if status:
                parts.append(f'status → **{status.replace("_", " ").title()}**')
            if progress is not None:
                parts.append(f'progress → **{result.get("progress", progress)}**')
            if score is not None:
                parts.append(f'score → **{score}/10**')

            update_text = ', '.join(parts) if parts else 'entry saved'
            await ctx.send_success(f'Updated **{media_title}**: {update_text}')
        else:
            await ctx.send_error('Failed to update your AniList entry.')


async def setup(bot: Bot) -> None:
    await bot.add_cog(AniList(bot))
