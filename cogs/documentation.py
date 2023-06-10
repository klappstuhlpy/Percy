from datetime import datetime, UTC, timedelta
from email.parser import HeaderParser
from io import StringIO
from typing import Dict, Annotated, Optional

import discord
from dateutil.parser import parse
from discord import app_commands
from discord.ext import commands, tasks

from bot import Percy
from cogs import command
from cogs.utils.docs import DocumentationView
from launcher import get_logger
from .base import Base
from .dstatus import DISCORD_ICON_URL
from .utils import cache
from .utils.context import Context
from cogs.utils.scraper.sphinx import SphinxScraper
from .utils.formats import format_date

log = get_logger(__name__)

ICON_URL = "https://www.python.org/static/opengraph-icon-200x200.png"
BASE_PEP_URL = "https://peps.python.org/pep-"


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

        self.peps: dict[int, str] = {}
        self.last_refreshed_peps: datetime = datetime.now(tz=UTC)

    async def cog_load(self) -> None:
        """Carry out cog asynchronous initialisation."""
        self.refresh_peps_urls.start()

    @tasks.loop(hours=3)
    async def refresh_peps_urls(self) -> None:
        """Refresh PEP URLs listing in every 3 hours."""
        await self.bot.wait_until_ready()
        log.trace("Started refreshing PEP URLs.")
        self.last_refreshed_peps = datetime.now(tz=UTC)

        cog: Base = self.bot.get_cog('Exclusives')  # type: ignore
        listing = await cog.github_request(
            'GET', "repos/python/peps/contents", headers={"Accept": "application/vnd.github+json"})

        for file in listing:
            name = file["name"]
            if name.startswith("pep-") and name.endswith((".rst", ".txt")):
                pep_number = name.replace("pep-", "").split(".")[0]
                self.peps[int(pep_number)] = file["download_url"]

        log.debug("Successfully refreshed PEP URLs listing.")

    @staticmethod
    def get_pep_zero_embed() -> dict:
        """Get information embed about PEP 0."""
        embed = discord.Embed(
            title="**PEP 0 - Index of Python Enhancement Proposals**",
            description=f"[*Jump to PEP*](https://peps.python.org/)"
        )
        embed.set_thumbnail(url=ICON_URL)
        embed.add_field(name="Status", value="Active")
        embed.add_field(name="Created", value=format_date(datetime(2000, 7, 13), 'd'))
        embed.add_field(name="Type", value="Informational")

        return {"embed": embed}

    async def validate_pep_number(self, pep_nr: int) -> Optional[dict]:
        """Validate is PEP number valid. When it isn't, return error embed, otherwise None."""
        if (
                pep_nr not in self.peps
                and (self.last_refreshed_peps + timedelta(minutes=30)) <= datetime.now(tz=UTC)
                and len(str(pep_nr)) < 5
        ):
            await self.refresh_peps_urls()

        if pep_nr not in self.peps:
            return {"content": f"<:redTick:1079249771975413910> `{pep_nr}` is not a valid PEP number."}

        return None

    @staticmethod
    def generate_pep_embed(pep_header: dict, pep_nr: int) -> discord.Embed:
        """Generate PEP embed based on PEP headers data."""
        title = " ".join(pep_header["Title"].split())
        embed = discord.Embed(
            title=f"**PEP {pep_nr} - {title}**",
            description=f"[*Jump to PEP*]({BASE_PEP_URL}{pep_nr:04})",
        )

        embed.set_thumbnail(url=ICON_URL)

        fields_to_check = ("Status", "Python-Version", "Created", "Type")
        for field in fields_to_check:
            if pep_header.get(field, ""):
                if field == "Created":
                    embed.add_field(name=field, value=format_date(parse(pep_header[field]), 'd'))
                else:
                    embed.add_field(name=field, value=pep_header[field])

        return embed

    @cache.cache()
    async def get_pep_embed(self, pep_nr: int) -> dict:
        """Fetch, generate and return PEP embed. Second item of return tuple show does getting success."""
        response = await self.bot.session.get(self.peps[pep_nr])

        if response.status == 200:
            log.trace(f"PEP {pep_nr} found")
            pep_content = await response.text()

            pep_header = HeaderParser().parse(StringIO(pep_content))
            return {"embed": self.generate_pep_embed(pep_header, pep_nr)}  # type: ignore

        log.trace(
            f"The user requested PEP {pep_nr}, but the response had an unexpected status code: {response.status}."
        )
        return {"content": "<:redTick:1079249771975413910> An unexpected Error has occured."}

    @command(commands.command, name="pep", aliases=["p"],
             description="Fetches information about a PEP and sends it to the channel.")
    async def pep_command(self, ctx: Context, pep_number: int) -> None:
        """Fetches information about a PEP and sends it to the channel."""
        await ctx.typing()

        if pep_number == 0:
            sending = self.get_pep_zero_embed()
        else:
            if not (sending := await self.validate_pep_number(pep_number)):
                sending = await self.get_pep_embed(pep_number)

        await ctx.send(**sending)

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
