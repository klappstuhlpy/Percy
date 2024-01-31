from __future__ import annotations

import datetime
import enum
import time
import traceback
from typing import Optional

import discord

from cogs.economy import cash_emoji
from cogs.utils import helpers, commands
from cogs.utils.context import Context, tick
from cogs.utils.formats import plural

SOWCASE_SPACES = [
    'Single Numbers', '1st', '2nd', '3rd', '1-12', '13-24', '25-36', '1-18', '19-36', 'Red', 'Black', 'Even', 'Odd'
]


class SpaceConverter(commands.Converter):
    """Convert a space."""

    async def convert(self, ctx: Context, argument: str) -> Space:
        """Convert the space."""
        if argument.capitalize() == 'Single Numbers':
            # Can't use this because it's a placeholder
            raise commands.BadArgument('Invalid space.')

        try:
            _space = Space(argument.capitalize())
        except ValueError:
            raise commands.BadArgument('Invalid space.')
        else:
            return _space


def validate_space(space: str) -> Optional[Space]:
    """Validate a space."""
    if space.capitalize() == 'Single Numbers':
        # Can't use this because it's a placeholder
        return None

    try:
        _space = Space(int(space))
    except ValueError:
        try:
            _space = Space(space.capitalize())
        except ValueError:
            return None
        else:
            return _space
    else:
        return _space


class Payout(enum.Enum):
    """Represents the payout for each space."""
    SINGLE_NUMBER = 36
    DOZEN = 3
    COLUMN = 3
    HALF = 2
    COLOR = 2
    ODD_EVEN = 2

    @classmethod
    def by_space(cls, space: Space) -> int:
        """Get the payout for a space."""
        if space.value in ('1st', '2nd', '3rd'):
            return cls.COLUMN.value
        elif space.value in ('1-12', '13-24', '25-36'):
            return cls.DOZEN.value
        elif space.value in ('1-18', '19-36'):
            return cls.HALF.value
        elif space.value in ('Red', 'Black'):
            return cls.COLOR.value
        elif space.value in ('Even', 'Odd'):
            return cls.ODD_EVEN.value
        else:
            return cls.SINGLE_NUMBER.value


class Space(enum.Enum):
    """Represents a space on the roulette table."""
    SINGLE_NUMBERS = 'Single Numbers'  # placeholder

    SINGLE_0 = 0
    SINGLE_1 = 1
    SINGLE_2 = 2
    SINGLE_3 = 3
    SINGLE_4 = 4
    SINGLE_5 = 5
    SINGLE_6 = 6
    SINGLE_7 = 7
    SINGLE_8 = 8
    SINGLE_9 = 9
    SINGLE_10 = 10
    SINGLE_11 = 11
    SINGLE_12 = 12
    SINGLE_13 = 13
    SINGLE_14 = 14
    SINGLE_15 = 15
    SINGLE_16 = 16
    SINGLE_17 = 17
    SINGLE_18 = 18
    SINGLE_19 = 19
    SINGLE_20 = 20
    SINGLE_21 = 21
    SINGLE_22 = 22
    SINGLE_23 = 23
    SINGLE_24 = 24
    SINGLE_25 = 25
    SINGLE_26 = 26
    SINGLE_27 = 27
    SINGLE_28 = 28
    SINGLE_29 = 29
    SINGLE_30 = 30
    SINGLE_31 = 31
    SINGLE_32 = 32
    SINGLE_33 = 33
    SINGLE_34 = 34
    SINGLE_35 = 35
    SINGLE_36 = 36

    COLUMN_FIRST = '1st'
    COLUMN_SECOND = '2nd'
    COLUMN_THIRD = '3rd'

    DOZEN_FIRST = '1-12'
    DOZEN_SECOND = '13-24'
    DOZEN_THIRD = '25-36'

    HALF_FIRST = '1-18'
    HALF_SECOND = '19-36'

    RED = 'Red'
    BLACK = 'Black'

    EVEN = 'Even'
    ODD = 'Odd'

    @property
    def real_value(self) -> list:
        if self.name.startswith('SINGLE'):
            return [int(self.value)]
        elif self.name == 'COLUMN_FIRST':
            return list(range(1, 37, 3))
        elif self.name == 'COLUMN_SECOND':
            return list(range(2, 37, 3))
        elif self.name == 'COLUMN_THIRD':
            return list(range(3, 37, 3))
        elif self.name == 'DOZEN_FIRST':
            return list(range(1, 13))
        elif self.name == 'DOZEN_SECOND':
            return list(range(13, 25))
        elif self.name == 'DOZEN_THIRD':
            return list(range(25, 37))
        elif self.name == 'HALF_FIRST':
            return list(range(1, 19))
        elif self.name == 'HALF_SECOND':
            return list(range(19, 37))
        elif self.name == 'RED':
            return [1, 3, 5, 7, 9, 12, 14, 16, 18, 21,
                    23, 25, 27, 30, 32, 34, 36]
        elif self.name == 'BLACK':
            return [2, 4, 6, 8, 10, 11, 13, 15, 17, 20,
                    22, 24, 26, 28, 29, 31, 33, 35]
        elif self.name == 'EVEN':
            return list(range(2, 37, 2))
        elif self.name == 'ODD':
            return list(range(1, 37, 2))
        else:
            return []

    @property
    def placeholder_field(self) -> int:
        if self.name in ('HALF_SECOND', 'BLACK', 'ODD'):
            return 1
        else:
            return 0


class Bet:
    def __init__(self, placed_by: discord.Member, space: Space, amount: int):
        self.placed_by: discord.Member = placed_by
        self.space: Space = space
        self.amount: int = amount

    def __repr__(self) -> str:
        return f"<Bet placed_by={self.placed_by} space={self.space} amount={self.amount}>"


class Table:
    """Represents a roulette table with all spaces."""

    def __init__(self, ctx: Context):
        self.ctx: Context = ctx

        self.start_time: time = time.time()

        self.message: Optional[discord.Message] = None
        self.bets: list[Bet] = []
        self.view: RouletteView = RouletteView(self)

        self.open: bool = True

    def __repr__(self) -> str:
        return f"<RouletteTable spaces={len(self.bets)}>"

    def close(self):
        """Close the roulette table."""
        self.open = False

        for item in self.view.children:
            item.disabled = True

    @staticmethod
    def get_winning_spaces(result: int) -> list[Space]:
        """Get the winning spaces from a result."""
        spaces = []
        for space in Space:
            if space == Space.SINGLE_NUMBERS:
                continue
            if Space.SINGLE_0 in spaces:
                # 0 is green, so all bets lose
                break
            if result in space.real_value:
                spaces.append(space)
        return spaces

    def place(self, bet: Bet) -> None:
        """Place a bet on the table."""
        self.bets.append(bet)

    def build_embed(self, winning_spaces: list[Space] = [], image_url: str = None, result: int = None) -> discord.Embed:  # noqa
        """Build the embed for the roulette table."""
        embed = discord.Embed(title='Roulette Table', color=helpers.Colour.darker_red())
        embed.set_image(url='https://i.imgur.com/n4QHQmv.png')
        embed.set_footer(text=f'Total of {plural(len(self.bets)):bet} placed.')

        if self.open:
            time_left = datetime.timedelta(seconds=60 - (time.time() - self.start_time))
            embed.description = f'*Bets are closing {discord.utils.format_dt(datetime.datetime.now() + time_left, style='R')}*'
        else:
            if Space.RED in winning_spaces:
                embed.colour = helpers.Colour.red()
            elif Space.BLACK in winning_spaces:
                embed.colour = helpers.Colour.black()
            elif Space.SINGLE_0 in winning_spaces:
                embed.colour = helpers.Colour.green()

            embed.description = f'`⚪` The ball has landed on **{result}**.'

        if image_url:
            embed.description = '*Spinning the wheel...*\n\nBets are closed. **Rien ne va plus!**'
            embed.colour = helpers.Colour.lighter_grey()
            embed.set_image(url=image_url)

        for space in SOWCASE_SPACES:
            is_numbered_space = space == 'Single Numbers'
            space: Space = Space(space)

            bets = [bet for bet in self.bets if (
                bet.space == space if not space == Space.SINGLE_NUMBERS else (bet.space.name.startswith('SINGLE')))]

            value = []
            for bet in bets:
                space_value_part = f'On **{bet.space.value}** • ' if space.name.startswith('SINGLE') else ''
                mention_part = f'{bet.placed_by.mention} • {cash_emoji} **{bet.amount:,}**'
                result_part = (
                    ' • **WON**' if bet.space in winning_spaces else ' • **LOSE**'
                ) if not self.open and not image_url else ''

                value.append(space_value_part + mention_part + result_part)

            if not value:
                value = ['*Not bets placed.*']

            embed.add_field(name=space.value, value='\n'.join(value), inline=True if not is_numbered_space else False)
            for _ in range(space.placeholder_field):
                embed.add_field(
                    name='\u200b',
                    value='\u200b'
                )

        return embed


class PlaceBetModal(discord.ui.Modal, title='Place Bet'):
    space = discord.ui.TextInput(label='Space', style=discord.TextStyle.short, placeholder='Space on a roulette table, e.g. 1, 2nd, Red')
    bet_amount = discord.ui.TextInput(label='Bet Amount', style=discord.TextStyle.short, placeholder='Amount to bet, e.g. 100')

    async def on_submit(self, interaction: discord.Interaction):
        self.interaction = interaction  # noqa
        self.stop()

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await interaction.response.send_message(f'{tick(False)} Something broke!', ephemeral=True)
        traceback.print_tb(error.__traceback__)


class RouletteView(discord.ui.View):
    """Represents the view for the roulette table."""

    def __init__(self, table: Table):
        super().__init__(timeout=None)
        self.table: Table = table

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if not self.table.open:
            await interaction.response.send_message('*Rien ne va plus!*', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Place Bet', style=discord.ButtonStyle.green)
    async def place_bet(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        """Place a bet on the roulette table."""
        modal = PlaceBetModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        interaction = modal.interaction

        try:
            bet = int(modal.bet_amount.value)
        except ValueError:
            return await interaction.response.send_message(
                f'{tick(False)} Invalid bet amount. Please provide a valid number.', ephemeral=True)

        if (space := validate_space(modal.space.value)) is None:
            return await interaction.response.send_message(
                f'{tick(False)} Could not determine space, please use a space from above.', ephemeral=True)

        self.table.place(Bet(interaction.user, space, bet))
        await interaction.response.send_message(f'{tick(True)} You have placed a bet on **{space.value}** with {cash_emoji} **{bet:,}**.', ephemeral=True)
        await self.table.message.edit(embed=self.table.build_embed())

    @discord.ui.button(label='Help', style=discord.ButtonStyle.grey, emoji='\N{WHITE QUESTION MARK ORNAMENT}')
    async def help(self, interaction: discord.Interaction, button: discord.Button):  # noqa
        """Show the help menu."""
        embed = discord.Embed(title='Roulette Help', color=discord.Color.blurple())
        embed.set_thumbnail(url='https://i.giphy.com/26uflBhaGt5lQsaCA.gif')
        embed.description = (
            'Roulette is a game where you bet on a space on the table. '
            'The dealer will spin the wheel, and if the ball lands on your space, you win!\n\n'
            'If the ball lands on **0** (green), all other bets lose.'
        )

        embed.add_field(name='Single Numbers', value=f'Bet on a single number. Payout: **{Payout.SINGLE_NUMBER}x**')
        embed.add_field(name='Dozen', value=f'Bet on a dozen. Payout: **{Payout.DOZEN}x**')
        embed.add_field(name='Column', value=f'Bet on a column. Payout: **{Payout.COLUMN}x**')
        embed.add_field(name='Half', value=f'Bet on a half. Payout: **{Payout.HALF}x**')
        embed.add_field(name='Color', value=f'Bet on a color. Payout: **{Payout.COLOR}x**')
        embed.add_field(name='Odd/Even', value=f'Bet on odd or even. Payout: **{Payout.ODD_EVEN}x**')

        embed.set_footer(text='You have 60 seconds to place your bets.')

        await interaction.response.send_message(embed=embed, ephemeral=True)
