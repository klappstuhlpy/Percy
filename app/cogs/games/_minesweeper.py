from __future__ import annotations

import random
import time
from contextlib import suppress
from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING, Final

import discord

from app.core.models import Context
from app.core.views import View
from app.utils import helpers, humanize_duration, fnumb
from config import Emojis

if TYPE_CHECKING:
    from app.database.base import Balance


neighbors: Final[list[tuple[int, int]]] = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


@dataclass
class MSField:
    x: int
    y: int
    value: int = 0
    revealed: bool = False
    mine: bool = False

    def __eq__(self, other: MSField) -> bool:
        return self.x == other.x and self.y == other.y


class Minesweeper(View):
    def __init__(self, ctx: Context | discord.Interaction, mines: int) -> None:
        super().__init__(timeout=250.0, members=ctx.author)

        self.ctx: Context | discord.Interaction = ctx
        self.mines = mines
        self.moves: int = 0
        self.start = time.perf_counter()

        self.board = [[MSField(x=x, y=y) for x in range(5)] for y in range(5)]
        self.place_mines()

        for x in range(5):
            for y in range(5):
                self.add_item(MinesweeperButton(self.board[x][y], (x, y)))

    async def end(
        self, interaction: discord.Interaction | None = None, won: bool = False, *, field: MSField | None = None
    ) -> None:
        """End the game."""
        duration = time.perf_counter() - self.start

        for item in self.children:
            if isinstance(item, MinesweeperButton):
                item.disabled = True

                if item.cell.mine:
                    if item.cell == field:
                        item.label = '\N{COLLISION SYMBOL}'
                    else:
                        item.label = '\N{TRIANGULAR FLAG ON POST}' if won else '\N{BOMB}'
                    item.style = discord.ButtonStyle.green if won else discord.ButtonStyle.red
                else:
                    item.style = discord.ButtonStyle.gray
                    item.label = str(item.cell.value) if item.cell.value != 0 else '‎'  # Zero width space

        embed = discord.Embed(
            title='Minesweeper',
            description=(
                f'You {'found all' if won else 'exploded by'} **{self.mines}** mines in **{self.moves}** moves.\n'
                f'Time: {humanize_duration(duration)}'
            ),
            colour=helpers.Colour.lime_green() if won else helpers.Colour.light_red()
        )
        embed.set_footer(text=f'Player: {self.ctx.author}')

        if won:
            user_balance: Balance = await interaction.client.db.get_user_balance(interaction.user.id, interaction.guild.id)
            amount: int = random.randint(25, 100)
            await user_balance.add(cash=amount)
            embed.description += f'\nEarned: {Emojis.Economy.cash} **{fnumb(amount)}**'

        with suppress(discord.NotFound, discord.HTTPException):
            if interaction:
                await interaction.response.edit_message(embed=embed, view=self)

        self.stop()

    def build_embed(self) -> discord.Embed:
        """Builds the base embed for the game."""
        embed = discord.Embed(
            title='Minesweeper',
            description=f'Moves: **{self.moves}**\n'
                        f'Mines: **{self.mines}**',
            colour=helpers.Colour.white(),
        )
        embed.set_footer(text=f'Player: {self.ctx.author}')
        return embed

    def place_mines(self) -> None:
        """Place the mines on the board.

        This is done by setting the `mine` attribute of a MSField to `true`
        and incrementing the value of the surrounding cells.
        """
        for field in random.sample(list(chain.from_iterable(self.board)), self.mines):
            field.mine = True

            for j, i in self.get_neighbours(field):
                if not self.board[j][i].mine:
                    self.board[j][i].value += 1

    @staticmethod
    def get_neighbours(field: MSField) -> list[tuple[int, int]]:
        """Get the neighbours of a cell"""
        return [(field.x + i, field.y + j) for i, j in neighbors if (0 <= field.x + i < 5) and (0 <= field.y + j < 5)]

    def mark(self, field: MSField) -> bool:
        """Mark a cell as selected."""
        from_board = self.board[field.x][field.y]
        from_board.revealed = True

        if from_board.mine:
            return False
        elif from_board.value == 0:
            for i, j in self.get_neighbours(field):
                neighbour = self.board[i][j]
                if not neighbour.revealed:
                    self.mark(neighbour)

        return True


class MinesweeperButton(discord.ui.Button['Minesweeper']):
    def __init__(self, field: MSField, position: tuple[int, int]) -> None:
        self.position: tuple[int, int] = position
        self.cell: MSField = field
        super().__init__()

        self._update_labels()

    def _update_labels(self) -> None:
        if self.view is not None:
            self.cell = self.view.board[self.position[0]][self.position[1]]

        if self.cell.revealed:
            self.style = discord.ButtonStyle.secondary
            self.label = str(self.cell.value) if self.cell.value != 0 else '‎'
        else:
            self.label = '‎'
            self.style = discord.ButtonStyle.blurple

        self.disabled = self.cell.revealed

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None

        self.view.moves += 1
        self.cell = self.view.board[self.position[0]][self.position[1]]

        if not self.view.mark(*self.position):
            return await self.view.end(interaction, field=self.cell)

        if self.cell.value == 0:
            for button in self.view.children:
                if isinstance(button, MinesweeperButton) and not button.disabled:
                    button._update_labels()
        else:
            self._update_labels()

        if all(all(kind.revealed for kind in row if not kind.mine) for row in self.view.board):
            return await self.view.end(interaction, True)

        await interaction.response.edit_message(embed=self.view.build_embed(), view=self.view)
