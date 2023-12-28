from __future__ import annotations

import random
import re
from datetime import datetime

from typing_extensions import Annotated
from typing import TYPE_CHECKING, Optional

from .utils import fuzzy, commands_ext
from .utils.constants import LANGUAGES
from .utils.translation import translate

from discord.ext import commands
import discord

if TYPE_CHECKING:
    from bot import Percy
    from .utils.context import Context


class Random(commands.Cog):
    """Some random fun purpose commands."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='newbie', id=1103422874171232319)

    @staticmethod
    def extract_translation_info(content: str) -> tuple[Optional[str], Optional[str]]:
        """Extracts the translation info from a message.

        Returns a tuple of the target language and the text to translate.

        Possible Contexts:
        - `to <target_language>:? <text>`
        - `in <target_language>:? <text>`
        - `on <target_language>:? <text>`
        - `<text> in <target_language>`
        - ...
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

    @commands_ext.command(
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

        # ex. output of dest: -> ('en', 'English')
        # we want the language code, not the Name,
        # so we get the first item in the tuple

        try:
            dest = dest[0]
        except IndexError:
            return await ctx.stick(False, 'Invalid language provided. Please provide a valid language.')

        if message is None:
            reply = ctx.replied_message
            if reply is not None:
                message = reply.content
            else:
                return await ctx.stick(False, 'Please provide a message to translate.')

        try:
            result = await translate(message, dest=dest, session=self.bot.session)
        except Exception as e:
            return await ctx.stick(False, 'An error occurred: {e.__class__.__name__}: {e}')

        embed = discord.Embed(title='Translator', colour=self.bot.colour.darker_red())
        embed.add_field(name=f'Original: {result.source_language} (Auto)', value=result.original, inline=False)
        embed.add_field(name=f'Translated: {result.target_language}', value=result.translated, inline=False)
        await ctx.send(embed=embed)

    @commands_ext.command(
        commands.command,
        name='meme',
        description='Shows you a random reddit meme.',
    )
    async def meme(self, ctx: Context):
        """Shows you a random reddit meme."""
        async with self.bot.session.get('https://www.reddit.com/r/dankmemes/new.json?sort=hot') as r:
            if r.status != 200:
                return await ctx.stick(False, 'Could not fetch memes :(')
            res = await r.json()
        random_meme = res['data']['children'][random.randint(0, len(res['data']['children']) - 1)]['data']
        embed = discord.Embed(title=random_meme['title'], url=random_meme['url'],
                              timestamp=await ctx.timestamp(),
                              colour=0x2b2d31)
        embed.set_image(url=random_meme['url'])
        embed.add_field(name='Rating', value=f'\N{THUMBS UP SIGN} **{random_meme["ups"]}** \N{SPEECH BALLOON} **{random_meme["num_comments"]}**', inline=False)
        await ctx.send(embed=embed)

    @commands_ext.command(
        commands.hybrid_command,
        name='fact',
        description="Shows you a random fact."
    )
    async def fact(self, ctx: Context):
        """Shows you a random fact."""
        async with self.bot.session.get('https://uselessfacts.jsph.pl/random.json?language=en') as r:
            if r.status != 200:
                return await ctx.stick(False, 'Could not fetch fact :(')
            res = await r.json()
        embed = discord.Embed(title='Random Fact', description=res['text'], colour=0x2b2d31)
        await ctx.send(embed=embed)


async def setup(bot: Percy):
    await bot.add_cog(Random(bot))
