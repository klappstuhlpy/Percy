from datetime import UTC, datetime, timedelta
from email.parser import HeaderParser
from io import StringIO
from typing import Optional

import discord.utils
from discord.ext import commands
from discord.ext.commands import Cog, Context

from bot import Percy
from cogs import command
from cogs.base import DPYHandlers
from cogs.utils import cache
from launcher import get_logger

log = get_logger(__name__)

ICON_URL = "https://www.python.org/static/opengraph-icon-200x200.png"
BASE_PEP_URL = "https://peps.python.org/pep-"
PEPS_LISTING_API_ENDPOINT = "/repos/python/peps/contents?ref=main"


class PythonEnhancementProposals(Cog):
    """Cog for displaying information about PEPs."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot
        self.peps: dict[int, str] = {}
        self.last_refreshed_peps: datetime = datetime.now(tz=UTC)

    async def cog_load(self) -> None:
        """Carry out cog asynchronous initialisation."""
        await self.refresh_peps_urls()

    async def refresh_peps_urls(self) -> None:
        """Refresh PEP URLs listing in every 3 hours."""
        await self.bot.wait_until_ready()
        log.trace("Started refreshing PEP URLs.")
        self.last_refreshed_peps = datetime.now(tz=UTC)

        cog: DPYHandlers = self.bot.get_cog('Exclusives')  # type: ignore
        listing = await cog.github_request('GET', PEPS_LISTING_API_ENDPOINT)

        for file in listing:
            name = file["name"]
            if name.startswith("pep-") and name.endswith((".rst", ".txt")):
                pep_number = name.replace("pep-", "").split(".")[0]
                self.peps[int(pep_number)] = file["download_url"]

        log.info("Successfully refreshed PEP URLs listing.")

    @staticmethod
    def get_pep_zero_embed() -> dict:
        """Get information embed about PEP 0."""
        embed = discord.Embed(
            title="**PEP 0 - Index of Python Enhancement Proposals (PEPs)**",
            url="https://peps.python.org/"
        )
        embed.set_thumbnail(url=ICON_URL)
        embed.add_field(name="Status", value="Active")
        embed.add_field(name="Created", value=discord.utils.format_dt(datetime(2000, 7, 13), "R"))
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
            return {"content": f"<:redTick:1079249771975413910> {pep_nr} is not a valid PEP number."}

        return None

    @staticmethod
    def generate_pep_embed(pep_header: dict, pep_nr: int) -> discord.Embed:
        """Generate PEP embed based on PEP headers data."""
        title = " ".join(pep_header["Title"].split())
        embed = discord.Embed(
            title=f"**PEP {pep_nr} - {title}**",
            description=f"[*Jump*]({BASE_PEP_URL}{pep_nr:04})",
        )

        embed.set_thumbnail(url=ICON_URL)

        fields_to_check = ("Status", "Python-Version", "Created", "Type")
        for field in fields_to_check:
            if pep_header.get(field, ""):
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
            return {"embed": self.generate_pep_embed(pep_header, pep_nr)}

        log.trace(
            f"The user requested PEP {pep_nr}, but the response had an unexpected status code: {response.status}."
        )
        return {"content": "<:redTick:1079249771975413910> An unexpected Error has occured."}

    @command(commands.command, name="pep", aliases=("get_pep", "p"), description="Fetches information about a PEP and sends it to the channel.")
    async def pep_command(self, ctx: Context, pep_number: int) -> None:
        """Fetches information about a PEP and sends it to the channel."""
        await ctx.typing()

        if pep_number == 0:
            sending = self.get_pep_zero_embed()
        else:
            if not (sending := await self.validate_pep_number(pep_number)):
                sending = await self.get_pep_embed(pep_number)

        await ctx.send(**sending)


async def setup(bot: Percy) -> None:
    await bot.add_cog(PythonEnhancementProposals(bot))
