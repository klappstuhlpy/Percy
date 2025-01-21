import asyncio
import enum
import inspect
import random
from collections.abc import AsyncGenerator
from typing import ClassVar

import discord
import numpy as np

from app.cogs.emoji import EMOJI_REGEX
from app.core.views import View
from app.utils import find_word, helpers, fnumb
from config import Emojis


class Fruits(enum.Enum):
    """Enum class representing the fruits in the slot machine."""
    MELON = '🍈'
    BANANA = '🍌'
    APPLE = '🍎'
    TANGERINE = '🍊'
    PEACH = '🍑'
    WATERMELON = '🍉'
    CHERRY = '🍒'
    LEMON = '🍋'
    STRAWBERRY = '🍓'
    PEAR = '🍐'
    PINEAPPLE = '🍍'
    GRAPE = '🍇'

    COOL = '🆒'


class SlotMachine(View):
    """Represents a slot machine with fruits representing each slot.

    This class uses numpy arrays to store slot values and perform calculations on winning etc.
    """

    PLACEHOLDER: ClassVar[str] = '<a:slot:1322359593073905725>'
    DESC_TITLE: ClassVar[str] = inspect.cleandoc(
        r"""
        **
        ░█▀▀░█░░░█▀█░▀█▀░░░█▄█░█▀█░█▀▀░█░█░▀█▀░█▀█░█▀▀
        ░▀▀█░█░░░█░█░░█░░░░█░█░█▀█░█░░░█▀█░░█░░█░█░█▀▀
        ░▀▀▀░▀▀▀░▀▀▀░░▀░░░░▀░▀░▀░▀░▀▀▀░▀░▀░▀▀▀░▀░▀░▀▀▀
        **
        """
    )

    def __init__(self, player: discord.Member, bet: int, *, rows: int = 3, columns: int = 3) -> None:
        super().__init__()
        self.player: discord.Member = player
        self.bet: int = bet

        self.rows: int = rows
        self.columns: int = columns

        # creates a rows x columns 2D array of random fruits
        self.slots: np.ndarray | None = None
        self.finished: bool = False

        self.update_buttons()

    def __str__(self) -> str:
        return self.build()

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title='🎰 Slot Machine',
            description=self.DESC_TITLE + '\n' + self.build(),
            colour=helpers.Colour.white()
        )
        embed.set_footer(text=f'Player: {self.player}')

        embed.add_field(
            name='\u200b',
            value=f'Bet: **{fnumb(self.bet)}** {Emojis.Economy.cash}',
        )
        return embed

    def roll(self) -> None:
        """Roll the slot machine."""
        self.slots = np.array(
            [[random.choice(list(Fruits.__members__.values())) for _ in range(self.columns)] for _ in range(self.rows)])

    def build(self, reveal_to_row: int | None = None) -> str:
        """Create a 2D numpy array with the emojis in their positions."""
        if self.slots is None:
            return self._format_build(np.full((self.rows, self.columns), self.PLACEHOLDER))

        if reveal_to_row:
            return self._format_build(
                np.array([[slot.value if i < reveal_to_row else self.PLACEHOLDER for i, slot in enumerate(row)]
                          for row in self.slots]))

        return self._format_build(np.array([[slot.value for slot in row] for row in self.slots]))

    def _format_build(self, arr: np.ndarray) -> str:
        """Format the 2D numpy array into the desired output with the frame.

        Example
        -------
        ╔═══╦═══╦═══╗
        ║ X ║ X ║ X ║
        ║ X ║ X ║ X ║
        ║ X ║ X ║ X ║
        ╚═══╩═══╩═══╝
          1   2   3
        """
        one = '\N{DIGIT ONE}'
        two = '\N{DIGIT TWO}'
        three = '\N{DIGIT THREE}'

        val_arr = ['═' * (self.columns * self.rows)] * self.rows
        start = Emojis.empty * 6
        sep = Emojis.empty + '`║`' + Emojis.empty

        parts = [
            start + '`╔' + '╦'.join(val_arr) + '╗`' + Emojis.empty,
            '\n'.join(start + '`║`' + Emojis.empty + sep.join(row) + sep for row in arr.tolist()),
            start + '`╚' + '╩'.join(val_arr) + '╝`' + Emojis.empty
        ]

        cl_text = EMOJI_REGEX.sub('x', parts[2])
        _, _, end = find_word(cl_text, '╩')
        middle = (end - ((self.columns * self.rows) / 2)) - 1
        parts.append(
            f'{start}`{one:^{middle}}{two:^{middle - self.columns - 1}}{three:^{middle - 1}}`{Emojis.empty}')
        return '\n'.join(parts)

    async def walk_build(self) -> AsyncGenerator[str, None]:
        """Dynamically returns the next column of the slot machine with the actual emojis and not placeholders."""
        if self.slots is None:
            self.roll()

        for i in range(1, self.rows + 1):
            await asyncio.sleep(2)
            yield self.build(i)

    def check_winning(self) -> int:
        """Check if the slot machine has a winning combination.

        Multipliers:
        - 3 of the same fruit: 3x
        - 5 of the same fruit: 5x

        If fruit is COOL, it is considered a wild card and can be used to substitute any other fruit.
        """
        if self.slots is None:
            return 0

        # check rows
        for row in self.slots:
            if len(set(row)) == 1:
                return 5 if row[0] == Fruits.COOL else 3

        # check columns
        for col in self.slots.T:
            if len(set(col)) == 1:
                return 5 if col[0] == Fruits.COOL else 3

        # check diagonals
        diagonal = self.slots.diagonal()
        if len(set(diagonal)) == 1:
            return 5 if diagonal[0] == Fruits.COOL else 3

        diagonal = np.fliplr(self.slots).diagonal()
        if len(set(diagonal)) == 1:
            return 5 if diagonal[0] == Fruits.COOL else 3

        return 0

    def get_winning(self, bet: int) -> tuple[int, int]:
        """Calculate the winning amount."""
        multiplier = self.check_winning()
        return bet * multiplier, multiplier

    def reset(self) -> None:
        self.slots = None
        self.finished = False

    # View

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.user.id != self.player.id:
            await interaction.response.send_message(f'{Emojis.error} This isn\'t your game.', ephemeral=True)
            return False
        return True

    def update_buttons(self) -> None:
        self.clear_items()
        if self.finished:
            self.add_item(self._roll)
        else:
            self.add_item(self._start)

    @discord.ui.button(label='Start', style=discord.ButtonStyle.blurple)
    async def _start(self, interaction: discord.Interaction, _) -> None:
        """Stops the rolling of the slot machine."""
        self.clear_items()
        await interaction.response.edit_message(view=self)

        embed = self.build_embed()
        async for build in self.walk_build():
            embed.description = self.DESC_TITLE + '\n' + build
            await interaction.edit_original_response(embed=embed)

        self.finished = True

        win, multiplier = self.get_winning(self.bet)

        if win:
            balance = await interaction.client.db.get_user_balance(self.player.id, interaction.guild_id)
            await balance.add(cash=win)
            embed.add_field(name='\u200b', value=f'`✅ You won {multiplier}x your bet!`', inline=False)
        else:
            embed.add_field(name='\u200b', value='`❌ Better luck next time!`', inline=False)

        self.update_buttons()
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label='Roll', style=discord.ButtonStyle.green)
    async def _roll(self, interaction: discord.Interaction, _) -> None:
        """Roll the slot machine."""
        self.reset()

        balance = await interaction.client.db.get_user_balance(self.player.id, interaction.guild_id)
        if self.bet > balance.cash:
            return await interaction.response.send_message(
                f'{Emojis.error} You do not have enough money to bet that amount.\n'
                f'You currently have {Emojis.Economy.cash} **{fnumb(balance.cash)}** in **cash**.', ephemeral=True)

        await balance.remove(cash=self.bet)

        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
