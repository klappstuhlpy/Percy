from __future__ import annotations

import datetime
import logging
from typing import Any

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands, tasks
from discord.ext.commands import Range  # noqa: TC002 -- flag/command param annotations are evaluated at runtime

from app.clients import AniListClient
from app.core import Bot, Cog, Context, Flags, describe, flag, group
from app.utils import fuzzy

from .ui import AniListBrowser, Kind, media_title, month_to_season

log = logging.getLogger(__name__)

SEASON_CHOICES = [Choice(name=s.title(), value=s) for s in ('WINTER', 'SPRING', 'SUMMER', 'FALL')]


class AniListSearchFlags(Flags):
    limit: Range[int, 1, 30] = flag(description='The number of results to return', default=15)


class AniList(Cog):
    """Search for anime, manga, characters, staff and studios on AniList."""

    emoji = '\N{TELEVISION}'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self.aniclient: AniListClient = AniListClient(self.bot.session)
        self._lookup: dict[str, list[str]] = {'ANIME': [], 'MANGA': []}

    async def cog_load(self) -> None:
        self.refresh_lookup.start()

    async def cog_unload(self) -> None:
        self.refresh_lookup.cancel()

    @tasks.loop(hours=6)
    async def refresh_lookup(self) -> None:
        """Autocomplete suggestions: what is trending now plus the all-time favourites, per media type."""
        for media_type in self._lookup:
            titles: list[str] = []
            try:
                for sort in ('TRENDING_DESC', 'POPULARITY_DESC'):
                    rows = await self.aniclient.search_media(page=1, perPage=50, type=media_type, sort=[sort])
                    titles.extend(title for row in rows if (title := media_title(row)) != '?')
            except Exception as exc:  # a task loop that raises stops for good
                log.warning('Failed to refresh the AniList %s lookup (%s); keeping the old one.', media_type, exc)
                continue
            self._lookup[media_type] = list(dict.fromkeys(titles))

    def _suggest(self, media_type: str, current: str) -> list[Choice[str]]:
        table = self._lookup[media_type]
        matches = fuzzy.finder(current, table) if current else table
        return [Choice(name=m[:100], value=m[:100]) for m in matches][:25]

    async def anime_autocomplete(self, _: discord.Interaction, current: str) -> list[Choice[str]]:
        return self._suggest('ANIME', current)

    async def manga_autocomplete(self, _: discord.Interaction, current: str) -> list[Choice[str]]:
        return self._suggest('MANGA', current)

    async def _browse(self, ctx: Context, kind: Kind, heading: str, entries: list[dict[str, Any]], *, missing: str) -> None:
        if not entries:
            raise commands.BadArgument(missing)
        await AniListBrowser.browse(ctx, client=self.aniclient, kind=kind, heading=heading, entries=entries)

    async def _search(self, ctx: Context, media_type: str, prompt: str, limit: int) -> None:
        await ctx.defer(typing=True)
        rows = await self.aniclient.search_media(page=1, perPage=limit, type=media_type, search=prompt)
        label = media_type.title()
        await self._browse(ctx, 'media', f'{label} results for “{prompt}”', rows,
                           missing=f'No {label.lower()} matches {prompt!r}.')

    async def _trending(self, ctx: Context, media_type: str) -> None:
        await ctx.defer(typing=True)
        rows = await self.aniclient.search_media(page=1, perPage=30, type=media_type, sort=['TRENDING_DESC'])
        await self._browse(ctx, 'media', f'Trending {media_type.title()}', rows,
                           missing='AniList returned no trending titles right now.')

    # ─── Commands ─────────────────────────────────────────────────────────────

    @group('anilist', description='Look things up on AniList', invoke_without_command=True, hybrid=True)
    async def _anilist(self, ctx: Context) -> None:
        """Look things up on AniList."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @_anilist.group(
        'anime', fallback='search', description='Search for an anime', invoke_without_command=True, hybrid=True,
    )
    @describe(prompt='The title of the anime to search for')
    @app_commands.autocomplete(prompt=anime_autocomplete)  # type: ignore[arg-type]
    async def anime(self, ctx: Context, *, prompt: str, flags: AniListSearchFlags) -> None:
        """Search for an anime and open its card."""
        await self._search(ctx, 'ANIME', prompt, flags.limit)

    @_anilist.group(
        'manga', fallback='search', description='Search for a manga', invoke_without_command=True, hybrid=True,
    )
    @describe(prompt='The title of the manga to search for')
    @app_commands.autocomplete(prompt=manga_autocomplete)  # type: ignore[arg-type]
    async def manga(self, ctx: Context, *, prompt: str, flags: AniListSearchFlags) -> None:
        """Search for a manga and open its card."""
        await self._search(ctx, 'MANGA', prompt, flags.limit)

    @anime.command('trending', description='The anime trending on AniList right now')
    async def anime_trending(self, ctx: Context) -> None:
        """The anime trending on AniList right now."""
        await self._trending(ctx, 'ANIME')

    @manga.command('trending', description='The manga trending on AniList right now')
    async def manga_trending(self, ctx: Context) -> None:
        """The manga trending on AniList right now."""
        await self._trending(ctx, 'MANGA')

    @anime.command('seasonal', description='The most popular anime of a season')
    @describe(season='The season (defaults to the current one)', year='The year (defaults to this year)')
    @app_commands.choices(season=SEASON_CHOICES)
    async def anime_seasonal(
        self, ctx: Context, season: str | None = None, year: Range[int, 1940, 2100] | None = None,
    ) -> None:
        """The most popular anime of a season, the current one by default."""
        await ctx.defer(typing=True)
        now = datetime.datetime.now(datetime.UTC)
        season = (season or month_to_season(now.month)).upper()
        if season not in ('WINTER', 'SPRING', 'SUMMER', 'FALL'):
            raise commands.BadArgument('Season must be one of winter, spring, summer or fall.')
        year = year or now.year

        rows = await self.aniclient.search_media(
            page=1, perPage=50, type='ANIME', season=season, seasonYear=year, sort=['POPULARITY_DESC'],
        )
        await self._browse(ctx, 'media', f'{season.title()} {year}', rows,
                           missing=f'AniList lists no anime for {season.title()} {year}.')

    @anime.command('airing', description='Anime episodes airing soon')
    @describe(days='How many days ahead to look (default 1)')
    async def anime_airing(self, ctx: Context, days: Range[int, 1, 7] = 1) -> None:
        """Anime episodes airing in the next few days, soonest first."""
        await ctx.defer(typing=True)
        now = int(discord.utils.utcnow().timestamp())
        rows = await self.aniclient.airing(now, now + days * 86400)
        heading = 'Airing in the next 24 hours' if days == 1 else f'Airing in the next {days} days'
        await self._browse(ctx, 'airing', heading, rows, missing='Nothing is scheduled to air in that window.')

    @_anilist.command('character', aliases=['char'], description='Search for a character')
    @describe(prompt='The name of the character to search for')
    async def character(self, ctx: Context, *, prompt: str, flags: AniListSearchFlags) -> None:
        """Search for a character and open their card."""
        await ctx.defer(typing=True)
        rows = await self.aniclient.characters(prompt, limit=flags.limit)
        await self._browse(ctx, 'character', f'Characters matching “{prompt}”', rows,
                           missing=f'No character matches {prompt!r}.')

    @_anilist.command('staff', aliases=['person'], description='Search for a voice actor, director or other staff')
    @describe(prompt='The name of the person to search for')
    async def staff(self, ctx: Context, *, prompt: str, flags: AniListSearchFlags) -> None:
        """Search for a voice actor, director, author or other staff member."""
        await ctx.defer(typing=True)
        rows = await self.aniclient.staff(prompt, limit=flags.limit)
        await self._browse(ctx, 'staff', f'Staff matching “{prompt}”', rows,
                           missing=f'No staff member matches {prompt!r}.')

    @_anilist.command('studio', description='Search for an animation studio')
    @describe(prompt='The name of the studio to search for')
    async def studio(self, ctx: Context, *, prompt: str, flags: AniListSearchFlags) -> None:
        """Search for a studio and see what it is known for."""
        await ctx.defer(typing=True)
        rows = await self.aniclient.studios(prompt, limit=flags.limit)
        await self._browse(ctx, 'studio', f'Studios matching “{prompt}”', rows,
                           missing=f'No studio matches {prompt!r}.')


async def setup(bot: Bot) -> None:
    await bot.add_cog(AniList(bot))
