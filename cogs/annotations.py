from __future__ import annotations

from typing import TYPE_CHECKING, Optional, List, Annotated

from discord import app_commands
import discord
import re

from .base import PH_GUILD_ID
from .utils.paginator import BasePaginator
from .utils import commands, render, helpers
from .utils.constants import HEX_REGEX, RGB_REGEX, CMYK_REGEX


if TYPE_CHECKING:
    from .utils.context import Context
    from bot import Percy


def cmyk_to_rgb(c, m, y, k):
    r = 255 * (1 - c / 100) * (1 - k / 100)
    g = 255 * (1 - m / 100) * (1 - k / 100)
    b = 255 * (1 - y / 100) * (1 - k / 100)
    return int(r), int(g), int(b)


class ColorParser(commands.clean_content):
    async def convert(self, ctx: Context, argument: str) -> discord.Colour:
        converted = await super().convert(ctx, argument)
        value = converted.strip()

        if not value:
            raise commands.BadArgument('Please enter a valid color format.')

        if match := re.match(HEX_REGEX, value):
            hex_value = match.group("hex")
            converted = discord.Colour(int(hex_value, 16))
        elif match := re.match(RGB_REGEX, value):
            rgb_values = tuple(map(int, match.group("red", "green", "blue")))
            converted = discord.Colour.from_rgb(*rgb_values)
        elif match := re.match(CMYK_REGEX, value):
            cmyk_values = tuple(map(int, match.group("c", "m", "y", "k")))
            rgb_values = cmyk_to_rgb(*cmyk_values)
            converted = discord.Colour.from_rgb(*rgb_values)
        elif re.match(r"^\w+$", value):
            converted = None

        if converted is None:
            raise commands.BadArgument(f'The Input {value!r} is not a valid format. Please use **HEX**, **RGB** or **CYMK**.')

        if isinstance(converted, str):
            raise commands.BadArgument(f'Could not convert {value!r} to a color.')

        return converted


class UrbanDictionaryPaginator(BasePaginator[dict]):
    BRACKETED = re.compile(r'(\[(.+?)\])')  # noqa

    @staticmethod
    def cleanup_definition(definition: str, *, regex=BRACKETED) -> str:
        def repl(m):
            word = m.group(2)
            return f'[{word}](http://{word.replace(" ", "-")}.urbanup.com)'  # noqa

        ret = regex.sub(repl, definition)
        if len(ret) >= 2048:
            return ret[0:2000] + ' [...]'
        return ret

    async def format_page(self, entries: List[dict], /) -> discord.Embed:
        entry = entries[0]

        embed = discord.Embed(
            title=f'"{entry["word"]}": {self.current_page} of {self.total_pages}',
            colour=helpers.Colour.mirage(),
            url=entry['permalink']
        )
        embed.set_thumbnail(url='https://images.klappstuhl.me/gallery/qLxbrWeWaR.png')
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

    def __init__(self, cog: Annotations) -> None:
        super().__init__()
        self.cog: Annotations = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        channel = self.cog.feedback_channel
        if channel is None:
            await interaction.response.send_message(
                '<:redTick:1079249771975413910> Could not submit your feedback, sorry about this',
                ephemeral=True)
            return

        embed = self.cog.get_feedback_embed(interaction, summary=str(self.summary), details=self.details.value)
        await channel.send(embed=embed)
        await interaction.response.send_message(
            '<:greenTick:1079249732364406854> Successfully submitted feedback', ephemeral=True)


class Annotations(commands.Cog):
    """Annotations that make you feel."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='bulbasaurrun', id=1102210564085797004, animated=True)

    @discord.utils.cached_property
    def feedback_channel(self) -> Optional[discord.TextChannel]:
        guild = self.bot.get_guild(PH_GUILD_ID)
        if guild is None:
            return None

        return guild.get_channel(1070028934638473266)

    @staticmethod
    def get_feedback_embed(
            obj: Context | discord.Interaction,
            *,
            summary: str,
            details: Optional[str] = None,
    ) -> discord.Embed:
        e = discord.Embed(title='Feedback', colour=helpers.Colour.white())

        if details is not None:
            e.description = details
            e.title = summary[:256]
        else:
            e.description = summary

        if obj.guild is not None:
            e.add_field(name='Server', value=f'{obj.guild.name} (ID: {obj.guild.id})', inline=False)

        if obj.channel is not None:
            e.add_field(name='Channel', value=f'{obj.channel} (ID: {obj.channel.id})', inline=False)

        if isinstance(obj, discord.Interaction):
            e.timestamp = obj.created_at
            user = obj.user
        else:
            e.timestamp = obj.message.created_at
            user = obj.author

        e.set_author(name=str(user), icon_url=user.display_avatar.url)
        e.set_footer(text=f'Author ID: {user.id}')
        return e

    @commands.command(
        commands.core_command,
        description='Sends feedback about the bot to the owner.',
        cooldown=commands.CooldownMap(rate=1, per=60.0, type=commands.BucketType.user)
    )
    async def feedback(self, ctx: Context, *, content: str):
        """Sends feedback about the bot to the owner.

        The Owner will communicate with you via PM to inform
        you about the status of your request if needed.

        You can only request feedback once a minute.
        """

        channel = self.feedback_channel
        if channel is None:
            return

        e = self.get_feedback_embed(ctx, summary=content)
        await channel.send(embed=e)
        await ctx.stick(False, 'Successfully sent feedback')

    @commands.command(app_commands.command, name='feedback', description='Sends feedback about the bot to the owner.')
    async def feedback_slash(self, interaction: discord.Interaction):
        """Sends feedback about the bot to the owner."""
        await interaction.response.send_modal(FeedbackModal(self))

    @commands.command(commands.core_command, hidden=True)
    @commands.is_owner()
    async def pm(self, ctx: Context, user_id: int, *, content: str):
        """Sends a DM to a user by ID."""
        user = self.bot.get_user(user_id) or (await self.bot.fetch_user(user_id))

        fmt = (content + '\n\n*This is a DM sent because you had previously requested feedback or I found a bug'
                         ' in a command you used, I do not monitor this DM.*')
        try:
            await user.send(fmt)
        except:  # noqa
            raise commands.CommandError(f'Could not send a DM to {user}.')
        else:
            await ctx.stick(True, 'PM successfully sent.')

    @commands.command(commands.core_command, name='urban', description="Searches the urban dictionary.")
    async def _urban(self, ctx: Context, *, word: str):
        """Searches urban dictionary."""

        url = 'http://api.urbandictionary.com/v0/define'  # noqa
        async with ctx.session.get(url, params={'term': word}) as resp:
            if resp.status != 200:
                raise commands.CommandError(f'An error occurred: {resp.status} {resp.reason}')

            js = await resp.json()
            data = js.get('list', [])
            if not data:
                raise commands.CommandError(f'No results found for {word!r}.')

        await UrbanDictionaryPaginator.start(ctx, entries=data, per_page=1)

    @commands.command(commands.hybrid_command, name='color', description="Shows information about a color.")
    @app_commands.describe(color='The color to show information about. Must be in hex format or autocompleted.')
    async def _color(self, ctx: Context, *, color: Annotated[discord.Colour, ColorParser]):
        """Shows information about a color."""
        if ctx.interaction:
            await ctx.defer()

        embed = discord.Embed(color=color)
        embed.set_footer(text=f'provided by thecolorapi.com')

        url = 'https://www.thecolorapi.com/id'
        async with self.bot.session.get(url, params={'hex': f'{color.value:0>6x}'}) as resp:
            if resp.status != 200:
                raise commands.CommandError(f'An error occurred: {resp.status} {resp.reason}')

            js = await resp.json()
            embed.url = f"{url}?hex={color.value:0>6x}"

            message = js['name']['value']
            api_img = render.generate_color_img((js['rgb']['r'], js['rgb']['g'], js['rgb']['b']), message)

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
                inline=False
            )

            embed.add_field(
                name='Color Data',
                value=f'**Hex:** {js["hex"]["value"]}\n'
                      f'**RGB:** `{js["rgb"]["value"]}`\n'
                      f'**HSL:** `{hsl}`\n'
                      f'**HSV:** `{hsv}`\n'
                      f'**CMYK:** `{cmyk}`\n'
                      f'**XYZ:** `{xyz}`',
                inline=False
            )

            embed.set_image(url='attachment://color.png')

            await ctx.send(embed=embed, file=discord.File(fp=api_img, filename='color.png'))


async def setup(bot: Percy):
    await bot.add_cog(Annotations(bot))
