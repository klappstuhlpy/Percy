from typing import Dict, Annotated

import discord
from discord import app_commands
from discord.ext import commands

from bot import Percy
from cogs import command
from cogs.utils.docs import DocumentationView
from .dstatus import DISCORD_ICON_URL
from .utils.context import Context
from cogs.utils.scraper.sphinx import SphinxScraper


def format_lib_list(iter: Dict[str, str]) -> str:  # noqa
    def fmt(n: str, u: str) -> str: return f'[{n}]({u})'
    for name, url in iter.items():
        yield fmt(name, url)


class Library(commands.clean_content):
    def __init__(self, *, docs: bool = False):
        self.docs: bool = docs
        super().__init__()

    async def convert(self, ctx: Context, argument: str) -> str:
        converted = await super().convert(ctx, argument)
        lib = converted.lower().strip()
        scraper: SphinxScraper = ctx.client.get_cog('Documentation').scraper  # type: ignore
        if not scraper:
            raise commands.BadArgument('<:redTick:1079249771975413910> Sphinx Scraper is not ready yet.')

        _current = scraper.RTFM_PAGE_TYPES if not self.docs else scraper.DOCS_PAGE_TYPES

        if not lib or (lib and lib not in _current):
            page_list = format_lib_list(_current)
            raise commands.BadArgument('<:redTick:1079249771975413910> Please enter a valid library.\n' +
                                       '\n'.join(page_list))

        return lib


class Documentation(commands.Cog):
    """Documentation commands for Sphinx Docs."""

    def __init__(self, bot: Percy):
        super().__init__()
        self.bot: Percy = bot

        self.bot.loop.create_task(self._startup_cache())

    async def _startup_cache(self) -> None:
        await self.bot.wait_until_ready()
        async with SphinxScraper(self.bot) as scraper:
            self.scraper: SphinxScraper = scraper

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name="early_dev", id=1103421868943351968, animated=True)

    async def docs_slash_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if not current:
            return []

        if len(current) < 3:
            return [app_commands.Choice(name=current, value=current)]

        library = interaction.namespace.library or "discord.py"

        matches = await self.scraper.search(current, library, limit=12)
        return [app_commands.Choice(name=m.name, value=m.name) for m in matches.results]

    async def rtfm_library_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [app_commands.Choice(name=m, value=m) for m in self.scraper.RTFM_PAGE_TYPES.keys()]

    async def docs_library_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [app_commands.Choice(name=m, value=m) for m in self.scraper.DOCS_PAGE_TYPES.keys()]

    async def ddocs_query_autcomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if not current:
            return []

        if len(current) < 3:
            return [app_commands.Choice(name=current, value=current)]

        matches = await self.scraper.search(current, 'ddocs', limit=12)
        return [app_commands.Choice(name=m.name, value=m.name) for m in matches.results]

    @command(
        commands.command,
        aliases=["rcd"],
        description="Recache the the documentations.",
        hidden=True,
    )
    @commands.is_owner()
    async def recachedocs(self, ctx: Context):
        """Recache cached items"""
        embed = discord.Embed(title="Recaching Sphinx Inventory...", color=self.bot.colour.darker_red())
        embed.set_footer(text="This may take a while...")
        message = await ctx.send(embed=embed)

        caching_tasks = [self.scraper.build_docs_lookup_cache, self.scraper.build_rtfm_lookup_table]
        for task in caching_tasks:
            if task.__name__ in self.scraper.completed_tasks:
                self.scraper.completed_tasks.remove(task.__name__)
            try:
                await self.scraper.run_task(task)
            except:
                msg = f"<:redTick:1079249771975413910> Failed to recache {task.__name__}"
            else:
                msg = f"<:greenTick:1079249732364406854> Successfully recached {task.__name__}"

            embed = message.embeds[0]
            if not embed.description:
                embed.description = msg
            else:
                embed.description += f"\n\n{msg}"
            await message.edit(embed=embed)

        await message.add_reaction("<:greenTick:1079249732364406854>")

    # Documentation commands

    @command(
        commands.hybrid_command,
        name='discorddocs',
        aliases=['ddocs'],
        description="Search the documentation of the Discord API."
    )
    @app_commands.describe(query="The query you want to search for in the given documentation.")
    @app_commands.autocomplete(query=ddocs_query_autcomplete)  # type: ignore
    async def discord_docs(self, ctx: Context, query: str):
        """Search the documentation of the Discord API."""
        if ctx.interaction:
            await ctx.defer()

        items = await self.scraper.search(query, 'ddocs', limit=1)
        if not items:
            return await ctx.send(f"<:redTick:1079249771975413910> No results found for {query!r}.")

        item = items.results[0]
        embed = discord.Embed(title=f"{query}", url=item.url, colour=discord.Colour.blurple())
        embed.set_author(name="Discord Developers", url="https://discord.com/developers/docs",
                         icon_url=DISCORD_ICON_URL)
        embed.set_footer(text='Click the title to view the documentation.')
        await ctx.send(embed=embed)

    @command(
        commands.hybrid_command,
        aliases=["rtfd"],
        description="Search the documentation for a module.",
    )
    @app_commands.describe(query="The query you want to search for in the given documentation.",
                           library="The library you want to search in.")
    @app_commands.autocomplete(query=docs_slash_autocomplete, library=rtfm_library_autocomplete)  # type: ignore
    async def rtfm(self, ctx: Context, library: Annotated[str, Library], *, query: str):
        """Search the documentation of for a module."""
        if ctx.interaction:
            await ctx.defer()

        await self.scraper.do_rtfm(ctx, query, library=library, limit=8)

    @command(
        commands.hybrid_command,
        aliases=["docs"],
        description="Get more detailed documentation of an attribute, class or function.",
    )
    @app_commands.describe(query="The query you want to search for in the given documentation.",
                           library="The library you want to search in.")
    @app_commands.autocomplete(query=docs_slash_autocomplete, library=docs_library_autocomplete)  # type: ignore
    async def documentation(
            self, ctx: Context, library: Annotated[str, Library(docs=True)], *, query: str  # type: ignore
    ):
        """Get documentation of an attribute, class or function"""
        if ctx.interaction:
            await ctx.defer()

        await DocumentationView.start(ctx, bot=self.bot, scraper=self.scraper, query=query, library=library)


async def setup(bot: Percy):
    await bot.add_cog(Documentation(bot))
