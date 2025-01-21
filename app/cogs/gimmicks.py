from __future__ import annotations

import random
import re
from typing import Annotated, ClassVar, Final

import discord
from discord.ext import commands

from app.core import Cog, Context
from app.core.models import HybridContext, command, cooldown, describe
from app.rendering import ColorImage
from app.utils import helpers
from app.utils.pagination import BasePaginator
from config import Emojis, main_guild_id


def cmyk_to_rgb(c: int, m: int, y: int, k: int) -> tuple[int, int, int]:
    r = 255 * (1 - c / 100) * (1 - k / 100)
    g = 255 * (1 - m / 100) * (1 - k / 100)
    b = 255 * (1 - y / 100) * (1 - k / 100)
    return int(r), int(g), int(b)


class ColourConverter(commands.clean_content):
    """Converts a string to a discord.Colour."""

    CMYK_REGEX: Final[ClassVar[re.Pattern]] = re.compile(
        r'^\(?(?P<c>[0-9]{1,3})%?\s*,?\s*(?P<m>[0-9]{1,3})%?\s*,?\s*(?P<y>[0-9]{1,3})%?\s*,?\s*(?P<k>[0-9]{1,3})%?\)?$')
    HEX_REGEX: Final[ClassVar[re.Pattern]] = re.compile(r'^(#|0x)?(?P<hex>[a-fA-F0-9]{6})$')
    RGB_REGEX: Final[ClassVar[re.Pattern]] = re.compile(r'^\(?(?P<red>[0-9]+),?\s*(?P<green>[0-9]+),?\s*(?P<blue>[0-9]+)\)?$')

    async def convert(self, ctx: Context, argument: str) -> discord.Colour:
        converted = await super().convert(ctx, argument)
        value = converted.strip()

        if not value:
            raise commands.BadArgument('Please enter a valid color format.')

        if match := re.match(self.HEX_REGEX, value):
            hex_value = match.group('hex')
            converted = discord.Colour(int(hex_value, 16))
        elif match := re.match(self.RGB_REGEX, value):
            rgb_values = tuple(map(int, match.group('red', 'green', 'blue')))
            converted = discord.Colour.from_rgb(*rgb_values)
        elif match := re.match(self.CMYK_REGEX, value):
            cmyk_values = tuple(map(int, match.group('c', 'm', 'y', 'k')))
            rgb_values = cmyk_to_rgb(*cmyk_values)
            converted = discord.Colour.from_rgb(*rgb_values)
        elif re.match(r"^\w+$", value):
            converted = None

        if converted is None:
            raise commands.BadArgument(f'The Input {value!r} is not a valid format. Please use HEX, RGB or CYMK.')

        if isinstance(converted, str):
            raise commands.BadArgument(f'Could not convert {value!r} to a color.')

        return converted


class UrbanDictionaryPaginator(BasePaginator[dict]):
    """A paginator for the urban dictionary."""

    BRACKETED = re.compile(r'(\[(.+?)])')

    @staticmethod
    def cleanup_definition(definition: str, *, regex: re.Pattern = BRACKETED) -> str:
        def repl(m: re.Match) -> str:
            word = m.group(2)
            return f'[{word}](http://{word.replace(" ", "-")}.urbanup.com)'

        ret = regex.sub(repl, definition)
        if len(ret) >= 2048:
            return ret[0:2000] + ' [...]'
        return ret

    async def format_page(self, entries: list[dict], /) -> discord.Embed:
        entry = entries[0]

        embed = discord.Embed(
            title=f'"{entry["word"]}": {self.current_page} of {self.total_pages}',
            colour=helpers.Colour.mirage(),
            url=entry['permalink']
        )
        embed.set_thumbnail(url='https://klappstuhl.me/gallery/nhKejnQTxd.png')
        embed.set_footer(text=f'by {entry["author"]}')
        embed.description = self.cleanup_definition(entry['definition'])

        try:
            example = entry['example']
        except KeyError:
            pass
        else:
            embed.add_field(name='Example', value=self.cleanup_definition(example), inline=False)

        try:
            up, down = entry['thumbs_up'], entry['thumbs_down']
        except KeyError:
            pass
        else:
            embed.add_field(name='Rating', value=f'\N{THUMBS UP SIGN} **{up}** \N{THUMBS DOWN SIGN} **{down}**',
                            inline=False)

        try:
            date = discord.utils.parse_time(entry['written_on'][0:-1])
        except (ValueError, KeyError):
            pass
        else:
            embed.timestamp = date

        return embed


class FeedbackModal(discord.ui.Modal, title='Submit Feedback'):
    summary = discord.ui.TextInput(label='Summary', placeholder='A brief explanation of what you want')
    details = discord.ui.TextInput(label='Details', style=discord.TextStyle.long, required=False)

    def __init__(self, cog: Gimmicks) -> None:
        super().__init__()
        self.cog: Gimmicks = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        channel = self.cog.feedback_channel
        if channel is None:
            await interaction.response.send_message(
                f'{Emojis.error} Could not submit your feedback, sorry about this', ephemeral=True)
            return

        embed = self.cog.get_feedback_embed(interaction, summary=str(self.summary), details=self.details.value)
        await channel.send(embed=embed)
        await interaction.response.send_message(f'{Emojis.success} Thank you for your feedback!', ephemeral=True)


class Gimmicks(Cog):
    """Annotations that make you feel."""

    emoji = '<a:bulbasaurrun:1322366217658568704>'

    @discord.utils.cached_property
    def feedback_channel(self) -> discord.TextChannel | None:
        guild = self.bot.get_guild(main_guild_id)
        if guild is None:
            return None

        return guild.get_channel(1070028934638473266)

    @staticmethod
    def get_feedback_embed(
            obj: Context | discord.Interaction,
            *,
            summary: str,
            details: str | None = None,
    ) -> discord.Embed:
        embed = discord.Embed(title='Feedback', colour=helpers.Colour.white())

        if details is not None:
            embed.description = details
            embed.title = summary[:256]
        else:
            embed.description = summary

        if obj.guild is not None:
            embed.add_field(name='Server', value=f'{obj.guild.name} (ID: {obj.guild.id})', inline=False)

        if obj.channel is not None:
            embed.add_field(name='Channel', value=f'{obj.channel} (ID: {obj.channel.id})', inline=False)

        if isinstance(obj, discord.Interaction):
            embed.timestamp = obj.created_at
            user = obj.user
        else:
            embed.timestamp = obj.message.created_at
            user = obj.author

        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.set_footer(text=f'Author ID: {user.id}')
        return embed

    @command(
        description='Sends feedback about the bot to the owner.',
        hybrid=True,
        with_app_command=False,
    )
    @describe(content='The feedback you want to send.')
    @cooldown(1, 60, commands.BucketType.user)
    async def feedback(self, ctx: Context, *, content: str) -> None:
        """Sends feedback about the bot to the owner.

        The Owner will communicate with you via PM to inform
        you about the status of your request if needed.

        You can only request feedback once a minute.
        """
        channel = self.feedback_channel
        if channel is None:
            return

        embed = self.get_feedback_embed(ctx, summary=content)
        await channel.send(embed=embed)
        await ctx.send_success('Thank you for your feedback!', ephemeral=True)

    @feedback.define_app_command()
    async def feedback_slash(self, ctx: HybridContext) -> None:
        """Sends feedback about the bot to the owner."""
        await ctx.interaction.response.send_modal(FeedbackModal(self))

    @command(
        description='Searches the urban dictionary.',
        hybrid=True
    )
    @describe(word='The word to search for.')
    async def urban(self, ctx: Context, *, word: str) -> None:
        """Searches urban dictionary."""
        url = 'http://api.urbandictionary.com/v0/define'
        async with ctx.session.get(url, params={'term': word}) as resp:
            if resp.status != 200:
                raise commands.BadArgument(f'An error occurred: {resp.status} {resp.reason}')

            js = await resp.json()
            data = js.get('list', [])
            if not data:
                raise commands.BadArgument(f'No results found for {word!r}.')

        await UrbanDictionaryPaginator.start(ctx, entries=data, per_page=1)

    @command(description='Shows information about a color.', hybrid=True)
    @describe(color='The color to show information about. Must be in hex format or autocompleted.')
    async def color(self, ctx: Context, *, color: Annotated[discord.Colour, ColourConverter]) -> None:
        """Shows information about a color."""
        await ctx.defer()

        embed = discord.Embed(color=color)
        embed.set_footer(text='provided by thecolorapi.com')

        url = 'https://www.thecolorapi.com/id'
        async with self.bot.session.get(url, params={'hex': f'{color.value:0>6x}'}) as resp:
            if resp.status != 200:
                raise commands.BadArgument(f'An error occurred: {resp.status} {resp.reason}')

            js = await resp.json()
            embed.url = f"{url}?hex={color.value:0>6x}"

            message = js['name']['value']
            color_image = ColorImage((js['rgb']['r'], js['rgb']['g'], js['rgb']['b']), message)
            image = color_image.create()

            hsl = js["hsl"]["value"]
            hsv = js["hsv"]["value"]
            cmyk = js["cmyk"]["value"]
            xyz = js["XYZ"]["value"]

            embed.title = message

            embed.add_field(
                name='Information',
                value=f'**Closest Named Hex:** {js["name"]["closest_named_hex"]}\n'
                      f'**Exact Match Name:** `{js["name"]["exact_match_name"]}`\n'
                      f'**Distance:** `{js["name"]["distance"]}`',
                inline=False)
            embed.add_field(
                name='Color Data',
                value=f'**Hex:** {js["hex"]["value"]}\n'
                      f'**RGB:** `{js["rgb"]["value"]}`\n'
                      f'**HSL:** `{hsl}`\n'
                      f'**HSV:** `{hsv}`\n'
                      f'**CMYK:** `{cmyk}`\n'
                      f'**XYZ:** `{xyz}`',
                inline=False)

            embed.set_image(url='attachment://color.png')

            await ctx.send(embed=embed, file=image)

    @command(
        name='meme',
        description='Shows you a random reddit meme.',
    )
    async def meme(self, ctx: Context) -> None:
        """Shows you a random reddit meme."""
        async with self.bot.session.get('https://www.reddit.com/r/dankmemes/new.json?sort=hot') as r:
            if r.status != 200:
                await ctx.send_error('Could not fetch memes :(')
                return
            res = await r.json()
        random_meme = res['data']['children'][random.randint(0, len(res['data']['children']) - 1)]['data']
        embed = discord.Embed(title=random_meme['title'], url=random_meme['url'],
                              timestamp=ctx.utcnow(),
                              colour=helpers.Colour.white())
        embed.set_image(url=random_meme['url'])
        embed.add_field(name='Rating',
                        value=f'\N{THUMBS UP SIGN} **{random_meme["ups"]}** \N{SPEECH BALLOON} **{random_meme["num_comments"]}**',
                        inline=False)
        await ctx.send(embed=embed)

    @command(
        name='fact',
        description='Shows you a random fact.',
        hybrid=True
    )
    async def fact(self, ctx: Context) -> None:
        """Shows you a random fact."""
        async with self.bot.session.get('https://uselessfacts.jsph.pl/random.json?language=en') as r:
            if r.status != 200:
                await ctx.send_error('Could not fetch fact :(')
                return
            res = await r.json()
        embed = discord.Embed(title='Random Fact', description=res['text'], colour=helpers.Colour.white())
        await ctx.send(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(Gimmicks(bot))
