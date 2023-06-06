from __future__ import annotations

import random
import re
from datetime import datetime

import yarl
from playwright.async_api import Page
from typing_extensions import Annotated
from typing import TYPE_CHECKING, Optional

from . import command
from .utils import fuzzy
from .utils.timer import TimeMesh
from .utils.translation import translate, LANGUAGES
from playwright._impl._api_types import TimeoutError as PlaywrightTimeoutError  # noqa

from discord.ext import commands
import discord
import io

if TYPE_CHECKING:
    from bot import Percy
    from .utils.context import Context

GUILD_ID = 1066703165669515264
VOICE_ROOM_ID = 1077008868187578469
GENERAL_VOICE_ID = 1079788410220322826


class TranslateFlags(commands.FlagConverter, delimiter=" ", prefix="--"):
    to: str = commands.flag(description="The language to translate to. Default: EN", default="en")
    from_: str = commands.flag(name="from", description="The language to translate from. Default: AUTO", default="auto")


class ScreenshotFlags(commands.FlagConverter, delimiter=" ", prefix="--"):
    """Flags for the screenshot command."""
    wait_for: bool = commands.Flag(
        default=False, aliases=["wf"], description="Wait for a specific amount of seconds before taking the screenshot"
    )
    use_proxy: bool = commands.Flag(
        default=False, aliases=["up"], description="Use a proxy"
    )
    full_page: bool = commands.Flag(
        default=False, aliases=["fp"], description="Take a full page screenshot"
    )
    use_adblock: bool = commands.Flag(
        default=False, aliases=["ua"], description="Use adblock"
    )
    bypass_captcha: bool = commands.Flag(
        default=False, aliases=["bc"], description="Bypass captcha"
    )
    ignore_nsfw_filter: bool = commands.Flag(
        default=False, aliases=["inf"], description="Ignore NSFW filter"
    )


class Random(commands.Cog):
    """Some random fun purpose commands."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='newbie', id=1103422874171232319)

    @command(
        commands.command,
        name='screenshot',
        description='Takes a screenshot of a website.',
        usage='<url> [flags...]',
    )
    async def screenshot(self, ctx: Context, url: str, *, flags: ScreenshotFlags):
        """Takes a screenshot of a website.
        This command uses a flag syntax to indicate what options you want to use.
        The following flags are valid.
        `--wait_for` (`-wf`): Wait for a specific load state to be reached before taking the screenshot.
        `--use_proxy` (`-up`): Use a proxy.
        `--full_page` (`-fp`): Take a full page screenshot.
        `--use_adblock` (`-ua`): Use adblock.
        `--bypass_captcha` (`-bc`): Bypass captcha.
        `--ignore_nsfw_filter` (`-inf`): Ignore NSFW filter.
        """

        url = url.strip("<>")
        if not yarl.URL(url):
            raise commands.BadArgument(f'{ctx.tick(False)} Invalid URL provided.')

        async with ctx.channel.typing():
            applied_flags = []
            with TimeMesh() as timer:
                try:
                    if flags.use_proxy:
                        applied_flags.append('`--use_proxy`')

                        browser = await self.bot.playwright.chromium.launch(
                            proxy={'server': 'socks5://184.178.172.17:4145'}
                        )
                    else:
                        browser = await self.bot.browser
                    bcontext = await browser.new_context()
                    page: Page = await bcontext.new_page()

                    await page.goto(url)

                    if flags.wait_for:
                        applied_flags.append('`--wait_for`')
                        try:
                            await page.wait_for_load_state("networkidle", timeout=0)
                        except PlaywrightTimeoutError:
                            pass

                    if flags.use_adblock:
                        applied_flags.append('`--use_adblock`')
                        await page.route('**/*', lambda route, request: route.abort())

                    if flags.ignore_nsfw_filter:
                        applied_flags.append('`--ignore_nsfw_filter`')
                        await page.set_content(await page.content() + '<meta name="robots" content="noindex">')

                    if flags.bypass_captcha:
                        applied_flags.append('`--bypass_captcha`')
                        await page.evaluate(
                            '() => { Object.defineProperties(navigator,{ webdriver:{ get: () => false } }) }')

                    if flags.full_page:
                        applied_flags.append('`--full_page`')
                        screenshot_bytes = await page.screenshot(full_page=True)
                    else:
                        screenshot_bytes = await page.screenshot()

                    await page.close()
                except Exception as exc:
                    return await ctx.send(f'{ctx.tick(False)} Failed to take screenshot:\n```py\n{exc}```')

        embed = discord.Embed(title=url, url=url, color=discord.Color.blurple())
        embed.set_image(url='attachment://screenshot.png')
        if applied_flags:
            embed.add_field(name='Flags', value=', '.join(applied_flags), inline=False)
        embed.set_footer(text=f'Took {timer.time:.2f}ms', icon_url=ctx.author.avatar.url)

        await ctx.send(embed=embed, file=discord.File(io.BytesIO(screenshot_bytes), filename='screenshot.png'))

    @staticmethod
    def extract_translation_info(content: str) -> tuple[Optional[str], Optional[str]]:
        """Extracts the translation info from a message.

        Returns a tuple of the target language and the text to translate.

        Possible Contexts:
        - `to <target_language>:? <text>`
        - `in <target_language>:? <text>`
        - `on <target_language>:? <text>`
        - `<text> in <target_language>`
        """

        CONTEXT_PATTERN = re.compile(
            r"(?:(?P<text>.+)\s+)?(?:to|in|on)\s+(?P<target_language>\w+)\b:\s*(?P<text2>.+)|(?P<text3>.+)\s+(?:to|in|on)\s+(?P<target_language2>\w+)\b",
            re.IGNORECASE
        )

        match = CONTEXT_PATTERN.search(content)
        if match:
            target_language = match.group("target_language") or match.group("target_language2")
            text = match.group("text") or match.group("text2") or match.group("text3")

            return target_language.lower(), text.strip()

        return "en", content

    @command(
        commands.command,
        name='translate',
        description='Translates a message to English using Google Translate.',
        usage='[to] <message>',
        examples=['to <target_language> <text>', 'in <target_language> <text>', '<text> to <target_language>']
    )
    async def translate(self, ctx: Context, *, message: Annotated[Optional[str], commands.clean_content] = None):
        """Translates a message to English using Google Translate."""

        dest, message = self.extract_translation_info(message)

        dest = fuzzy.find(dest, LANGUAGES.items(), key=lambda x: x[1])
        if not dest:
            dest = fuzzy.find(dest, LANGUAGES.items(), key=lambda x: x[0])

        try:
            dest = dest[0]
        except IndexError:
            return await ctx.send(f'{ctx.tick(False)} Invalid language provided. Please provide a valid language.')

        if message is None:
            reply = ctx.replied_message
            if reply is not None:
                message = reply.content
            else:
                return await ctx.send(f'{ctx.tick(False)} Please provide a message to translate.')

        try:
            result = await translate(message, dest=dest, session=self.bot.session)
        except Exception as e:
            return await ctx.send(f'{ctx.tick(False)} An error occurred: {e.__class__.__name__}: {e}')

        embed = discord.Embed(title='Translator', colour=self.bot.colour.darker_red())
        embed.add_field(name=f'Original: {result.source_language} (Auto)', value=result.original, inline=False)
        embed.add_field(name=f'Translated: {result.target_language}', value=result.translated, inline=False)
        await ctx.send(embed=embed)

    @command(
        commands.command,
        name='meme',
        description='Shows you a random reddit meme.',
    )
    async def meme(self, ctx: Context):
        """Shows you a random reddit meme."""
        async with self.bot.session.get('https://www.reddit.com/r/dankmemes/new.json?sort=hot') as r:
            if r.status != 200:
                return await ctx.send('Could not fetch memes :(')
            res = await r.json()
        random_meme = res['data']['children'][random.randint(0, len(res['data']['children']) - 1)]['data']
        embed = discord.Embed(title=random_meme['title'], url=random_meme['url'],
                              timestamp=datetime.utcnow(),
                              colour=0x2b2d31)
        embed.set_image(url=random_meme['url'])
        embed.add_field(name='Rating', value=f'\N{THUMBS UP SIGN} **{random_meme["ups"]}** \N{SPEECH BALLOON} **{random_meme["num_comments"]}**', inline=False)
        await ctx.send(embed=embed)


async def setup(bot: Percy):
    await bot.add_cog(Random(bot))
