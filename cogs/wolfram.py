import asyncio
import logging
from collections.abc import Callable
from io import BytesIO
from typing import Any, Literal

import aiohttp
import arrow
import discord
import yarl
from discord.ext import commands

from bot import Percy
from cogs import command
from cogs.utils import helpers
from cogs.utils.context import Context
from cogs.utils.paginator import EmbedPaginator

log = logging.getLogger(__name__)

WOLF_IMAGE = "https://www.symbols.com/gi.php?type=1&id=2886&i=1"

MAX_PODS = 20

usercd = commands.CooldownMapping.from_cooldown(10, 60 * 60 * 24, commands.BucketType.user)
guildcd = commands.CooldownMapping.from_cooldown(60, 60 * 60 * 24, commands.BucketType.guild)


def fmt_embed(text: str) -> discord.Embed:
    embed = discord.Embed(colour=helpers.Colour.darker_red(), description=text)
    embed.set_author(
        name="Wolfram Alpha",
        icon_url=WOLF_IMAGE,
        url="https://www.wolframalpha.com/"
    )
    return embed


def custom_cooldown(*ignore: int) -> Callable:
    """
    Implement per-user and per-guild cooldowns for requests to the Wolfram API.

    A list of roles may be provided to ignore the per-user cooldown.
    """

    async def predicate(ctx: Context) -> bool:
        if ctx.invoked_with == "help":
            guild_cooldown = guildcd.get_bucket(ctx.message).get_tokens() != 0
            if ctx.guild and not any(r.id in ignore for r in ctx.author.roles):
                return guild_cooldown and usercd.get_bucket(ctx.message).get_tokens() != 0
            return guild_cooldown

        user_bucket = usercd.get_bucket(ctx.message)

        if all(role.id not in ignore for role in ctx.author.roles):
            user_rate = user_bucket.update_rate_limit()

            if user_rate:
                cooldown = arrow.utcnow().shift(seconds=int(user_rate)).humanize(only_distance=True)
                message = (
                    "You've used up your limit for Wolfram|Alpha requests.\n"
                    f"Cooldown: {cooldown}"
                )
                await ctx.send(embed=fmt_embed(message))
                return False

        guild_bucket = guildcd.get_bucket(ctx.message)
        guild_rate = guild_bucket.update_rate_limit()

        log.debug(guild_bucket)

        if guild_rate:
            message = (
                "The max limit of requests for the server has been reached for today.\n"
                f"Cooldown: {int(guild_rate)}"
            )
            await ctx.send(embed=fmt_embed(message))
            return False

        return True

    return commands.check(predicate)


class WolframError(commands.CommandError):
    """Base exception class for Wolfram-related errors."""

    def __init__(self, response: aiohttp.ClientResponse):
        self.response: aiohttp.ClientResponse = response

        if response.status == 501:
            message = "Failed to get response."
        elif response.status == 400:
            message = "No input found."
        elif response.status == 403:
            message = "Wolfram API key is invalid or missing."
        else:
            message = f"Unexpected status code from Wolfram API: {response.status}"

        super().__init__(message)


class Wolfram(commands.Cog):
    """Commands for interacting with the Wolfram|Alpha API."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

        self._req_lock: asyncio.Lock = asyncio.Lock()

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name="wolfram", id=1118615618418114711)

    async def cog_command_error(self, ctx: Context, error: Exception) -> None:
        if isinstance(error, WolframError):
            await ctx.send(str(error))

    async def get_pod_pages(self, ctx: Context, query: str) -> list[tuple[str, str]] | None:
        """Get the Wolfram API pod pages for the provided query."""

        async with ctx.typing():
            json = await self.wolfram_request(
                "GET",
                'query',
                query=query,
                params={
                    "output": 'JSON',
                    "format": "image,plaintext",
                }
            )

            result = json["queryresult"]
            if result["error"]:
                if result["error"]["msg"] == "Invalid appid":
                    message = "Wolfram API key is invalid or missing."
                    log.warning(
                        "API key seems to be missing, or invalid when "
                        f"processing a wolfram request, Response: {json}"
                    )
                    await ctx.send(embed=fmt_embed(message))
                    return None

                message = "Something went wrong internally with your request, please notify staff!"
                log.warning(f"Something went wrong getting a response from wolfram, Response: {json}")
                await ctx.send(embed=fmt_embed(message))
                return None

            if not result["success"]:
                message = f"I couldn't find anything for {query}."
                await ctx.send(embed=fmt_embed(message))
                return None

            if not result["numpods"]:
                message = "Could not find any results."
                await ctx.send(embed=fmt_embed(message))
                return None

            pods = result["pods"]
            pages = []
            for pod in pods[:MAX_PODS]:
                subs = pod.get("subpods")

                for sub in subs:
                    title = sub.get("title") or sub.get("plaintext") or sub.get("id", "")
                    img = sub["img"]["src"]
                    pages.append((title, img))
            return pages

    async def wolfram_request(
            self,
            method: str,
            url: str,
            *,
            query: str = None,
            response_type: Literal["json", "bytes", "text"] = "text",
            params: dict[str, Any] = None
    ) -> Any:
        prms = {
            "input": query,
            "appid": self.bot.config.wolfram_api_key,
            "location": "the moon",
            "latlong": "0.0,0.0",
            "ip": "1.1.1.1"
        }

        req_url = yarl.URL('http://api.wolframalpha.com/v2/') / url

        if params:
            prms.update(params)

        async with self._req_lock:
            async with self.bot.session.request(method, req_url, params=prms) as r:
                if 300 > r.status >= 200:
                    if response_type == "json":
                        return await r.json()
                    elif response_type == "bytes":
                        return await r.read()
                    else:
                        return await r.text()
                else:
                    raise WolframError(r)

    @command(commands.hybrid_group, name="wolfram", aliases=("wolf", "wa"), invoke_without_command=True,
             description="Requests all answers on a single image, sends an image of all related pods.")
    async def wolfram(self, ctx: Context, *, query: str) -> None:
        """Requests all answers on a single image, sends an image of all related pods."""
        async with ctx.typing():
            image_bytes = await self.wolfram_request("GET", 'simple', query=query, response_type="bytes")

        f = discord.File(BytesIO(image_bytes), filename="image.png")
        image_url = "attachment://image.png"

        embed = discord.Embed(colour=helpers.Colour.darker_red())
        embed.set_author(
            name="Wolfram Alpha",
            icon_url=WOLF_IMAGE,
            url="https://www.wolframalpha.com/"
        )
        embed.set_image(url=image_url)
        embed.set_footer(text="View original for a bigger picture.")
        await ctx.send(embed=embed, file=f)

    @command(wolfram.command, name="page", aliases=("pa", "p"), description="Requests a drawn image of given query.")
    async def wolfram_page(self, ctx: Context, *, query: str) -> None:
        """Requests a drawn image of given query.

        Keywords worth noting are, "like curve", "curve", "graph", "pokemon", etc.
        """
        pages = await self.get_pod_pages(ctx, query)

        if not pages:
            return

        embed = discord.Embed(colour=helpers.Colour.darker_red())
        embed.set_author(
            name="Wolfram Alpha",
            icon_url=WOLF_IMAGE,
            url="https://www.wolframalpha.com/"
        )
        embeds = []
        for page in pages:
            embed.description = page[0]
            embed.set_image(url=page[1])
            embeds.append(embed)

        await EmbedPaginator.start(ctx, entries=embeds)

    @command(wolfram.command, name="cut", aliases=("c",), description="Requests a drawn image of given query.")
    async def wolfram_cut(self, ctx: Context, *, query: str) -> None:
        """Requests a drawn image of given query.

        Keywords worth noting are, "like curve", "curve", "graph", "pokemon", etc.
        """
        pages = await self.get_pod_pages(ctx, query)

        if not pages:
            return

        page = pages[1] if len(pages) >= 2 else pages[0]

        embed = discord.Embed(colour=helpers.Colour.darker_red(), description=page[0])
        embed.set_author(
            name="Wolfram Alpha",
            icon_url=WOLF_IMAGE,
            url="https://www.wolframalpha.com/"
        )
        embed.set_image(url=page[1])
        await ctx.send(embed=embed)

    @command(wolfram.command, name="short", aliases=("sh", "s"), description="Requests an answer to a simple question.")
    async def wolfram_short(self, ctx: Context, *, query: str) -> None:
        """Requests an answer to a simple question."""
        async with ctx.typing():
            text = await self.wolfram_request("GET", 'result', query=query, response_type="text")
            await ctx.send(embed=fmt_embed(text))


async def setup(bot: Percy) -> None:
    """Load the Wolfram cog."""
    await bot.add_cog(Wolfram(bot))
