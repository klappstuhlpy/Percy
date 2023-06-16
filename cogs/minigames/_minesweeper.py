from __future__ import annotations

import random
import time
from contextlib import suppress

import discord
from discord import Interaction

from cogs.utils import helpers
from cogs.utils.context import Context
from cogs.utils.formats import readable_time


# Credits for this Source Code https://github.com/MrArkon/MAGPB/blob/master/bot/plugins/fun/views.py#L25-L165


neighbors: list[tuple[int, int]] = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


class BoardKind:
    def __init__(self) -> None:
        self.value: int = 0
        self.selected: bool = False


class Minesweeper(discord.ui.View):
    def __init__(self, ctx: Context | discord.Interaction, mines: int):
        super().__init__(timeout=250.0)

        self.ctx: Context | discord.Interaction = ctx
        self.mines = mines
        self.moves: int = 0
        self.start = time.perf_counter()

        self.board = [[BoardKind() for _ in range(5)] for _ in range(5)]
        self.place_mines()

        for x in range(5):
            for y in range(5):
                self.add_item(MinesweeperButton(self.board[x][y], (x, y)))

    async def interaction_check(self, interaction: Interaction, /) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("This isn't your game.", ephemeral=True)
            return False
        return True

    async def terminate(
        self, interaction: discord.Interaction | None = None, won: bool = False, *, position: tuple[int, int] | None = None
    ) -> None:
        duration = time.perf_counter() - self.start

        for item in self.children:
            if isinstance(item, MinesweeperButton):
                item.disabled = True

                if item.cell.value == -1:
                    if item.position == position:
                        item.label = "\N{COLLISION SYMBOL}"
                    else:
                        item.label = "\N{TRIANGULAR FLAG ON POST}" if won else "\N{BOMB}"
                    item.style = discord.ButtonStyle.green if won else discord.ButtonStyle.red
                else:
                    item.style = discord.ButtonStyle.gray
                    item.label = str(item.cell.value) if item.cell.value != 0 else "‎"

        embed = discord.Embed(
            title="Minesweeper",
            description=f"You {'found all' if won else 'exploded by'} "
            f"**{self.mines}** mines in **{self.moves}** moves • Time: {readable_time(duration, short=True)}",
            colour=helpers.Colour.lime_green() if won else helpers.Colour.light_red()
        ).set_footer(text=f"Player: {self.ctx.author}")

        with suppress(discord.NotFound, discord.HTTPException):
            if interaction:
                await interaction.response.edit_message(embed=embed, view=self)

        self.stop()

    def build_embed(self) -> discord.Embed:
        return discord.Embed(
            title="Minesweeper",
            description=f"Moves: **{self.moves}** • Mines: **{self.mines}**",
            colour=helpers.Colour.light_orange(),
        ).set_footer(text=f"Player: {self.ctx.author}")

    def place_mines(self) -> None:
        previous = set()
        for _ in range(self.mines):
            x, y = random.randint(0, 4), random.randint(0, 4)

            while (x, y) in previous:
                x, y = random.randint(0, 4), random.randint(0, 4)

            self.board[y][x].value = -1

            for j, i in self.get_neighbours(y, x):
                if self.board[j][i].value != -1:
                    self.board[j][i].value += 1

            previous.add((x, y))

    @staticmethod
    def get_neighbours(x: int, y: int) -> list[tuple[int, int]]:
        """Get the neighbours of a cell"""
        return [(x + i, y + j) for i, j in neighbors if (0 <= x + i < 5) and (0 <= y + j < 5)]

    def mark(self, x: int, y: int) -> bool:
        """Mark a cell as selected"""
        self.board[x][y].selected = True

        if self.board[x][y].value == -1:
            return False
        elif self.board[x][y].value == 0:
            for i, j in self.get_neighbours(x, y):
                if not self.board[i][j].selected:
                    self.mark(i, j)

        return True


class MinesweeperButton(discord.ui.Button["Minesweeper"]):
    def __init__(self, kind: BoardKind, position: tuple[int, int]):
        self.position: tuple[int, int] = position
        self.kind: BoardKind = kind

        super().__init__()
        self._update_labels()

    def _update_labels(self) -> None:
        if self.view is not None:
            self.cell = self.view.board[self.position[0]][self.position[1]]

        if self.cell.selected:
            self.style = discord.ButtonStyle.secondary
            self.label = str(self.cell.value) if self.cell.value != 0 else "‎"
        else:
            self.label = "‎"
            self.style = discord.ButtonStyle.blurple

        self.disabled = self.cell.selected

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None

        self.view.moves += 1
        self.kind = self.view.board[self.position[0]][self.position[1]]

        if not self.view.mark(*self.position):
            return await self.view.terminate(interaction, position=self.position)

        if self.cell.value == 0:
            for button in self.view.children:
                if isinstance(button, MinesweeperButton):
                    if not button.disabled:
                        button._update_labels()
        else:
            self._update_labels()

        if all(all(kind.selected for kind in row if kind.value != -1) for row in self.view.board):
            return await self.view.terminate(interaction, True)

        await interaction.response.edit_message(embed=self.view.build_embed(), view=self.view)
