from __future__ import annotations

import random
import time
from contextlib import suppress
from typing import TYPE_CHECKING

import discord

from app.cogs.games.engine.minesweeper import Board, MSField
from app.core.views import View
from app.utils import fnumb, helpers, humanize_duration
from config import Emojis

if TYPE_CHECKING:
    from app.core.models import Context
    from app.database.base import Balance

__all__ = ('Minesweeper', 'MinesweeperButton')


class Minesweeper(View):
    def __init__(self, ctx: Context | discord.Interaction, mines: int) -> None:
        super().__init__(timeout=250.0, members=ctx.author)

        self.ctx: Context | discord.Interaction = ctx
        self.moves: int = 0
        self.start = time.perf_counter()

        self.engine: Board = Board(mines)

        for x in range(Board.SIZE):
            for y in range(Board.SIZE):
                self.add_item(MinesweeperButton(self.engine.board[x][y], (x, y)))

    @property
    def board(self) -> list[list[MSField]]:
        return self.engine.board

    @property
    def mines(self) -> int:
        return self.engine.mines

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

        x, y = self.position
        field = self.view.board[x][y]

        if not self.view.engine.mark(field):
            return await self.view.end(interaction, field=field)

        if self.cell.value == 0:
            for button in self.view.children:
                if isinstance(button, MinesweeperButton) and not button.disabled:
                    button._update_labels()
        else:
            self._update_labels()

        if self.view.engine.is_won:
            return await self.view.end(interaction, True)

        await interaction.response.edit_message(embed=self.view.build_embed(), view=self.view)
