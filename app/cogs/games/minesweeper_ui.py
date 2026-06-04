from __future__ import annotations

import random
import time
from contextlib import suppress
from typing import TYPE_CHECKING

import discord

from app.cogs.games.engine.minesweeper import Board, MSField
from app.core.views import LayoutView
from app.utils import fnumb, helpers, humanize_duration
from config import Emojis

if TYPE_CHECKING:
    from app.core.models import Context
    from app.database.base import Balance

__all__ = ("Minesweeper", "MinesweeperButton")


class Minesweeper(LayoutView):
    def __init__(self, ctx: Context | discord.Interaction, mines: int) -> None:
        super().__init__(timeout=250.0, members=[ctx.author])

        self.ctx: Context | discord.Interaction = ctx
        self.moves: int = 0
        self.start = time.perf_counter()

        self.engine: Board = Board(mines)

        self.items: list[MinesweeperButton] = []

        for x in range(Board.SIZE):
            for y in range(Board.SIZE):
                self.items.append(MinesweeperButton(self.engine.board[x][y], (x, y)))

        self.container: discord.ui.Container = discord.ui.Container(id=1)

        self.refresh_container()

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
        for item in self.container.walk_children():
            if isinstance(item, MinesweeperButton):
                item.disabled = True

                if item.cell.mine:
                    if not won and item.cell == field:
                        item.label = "\N{COLLISION SYMBOL}"
                    else:
                        item.label = "\N{TRIANGULAR FLAG ON POST}" if won else "\N{BOMB}"
                    item.style = discord.ButtonStyle.green if won else discord.ButtonStyle.red
                else:
                    item.style = discord.ButtonStyle.gray
                    item.label = str(item.cell.value) if item.cell.value != 0 else "‎"  # Zero width space

        amount: int = 0
        if won:
            user_balance: Balance = await interaction.client.db.get_user_balance(interaction.user.id, interaction.guild.id)  # type: ignore
            amount: int = random.randint(25, 100)
            await user_balance.add(cash=amount)

        self.refresh_container(won, fnumb(amount) if won else None)

        with suppress(discord.NotFound, discord.HTTPException):
            if interaction:
                await interaction.response.edit_message(view=self)

        self.stop()

    def refresh_container(self, won: bool | None = None, amount: str | None = None) -> None:
        """Builds the container for the game."""
        self.clear_items()

        container = discord.ui.Container(
            accent_color=helpers.Colour.white()
            if won is None
            else (helpers.Colour.lime_green() if won else helpers.Colour.light_red()),
            id=1,
        )

        duration = time.perf_counter() - self.start
        description = "## Minesweeper\n"

        if won is not None:
            description += (
                f"You {'found all' if won else 'exploded by'} **{self.mines}** mines in **{self.moves}** moves.\n"
                f"Time: {humanize_duration(duration)}"
            )
        else:
            description += f"**{self.mines}** mines to be found in **{self.moves}** moves."

        if amount:
            description += f"\nEarned: {Emojis.Economy.cash} **{amount}**"

        container.add_item(discord.ui.TextDisplay(description))
        container.add_item(discord.ui.Separator())

        # acutal buttons
        for x in range(Board.SIZE):
            row = discord.ui.ActionRow()
            for y in range(Board.SIZE):
                row.add_item(self.items[x * Board.SIZE + y])
            container.add_item(row)

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"-# Player: {self.ctx.author}"))

        self.container = container
        self.add_item(container)


class MinesweeperButton(discord.ui.Button["Minesweeper"]):
    """A button for the minesweeper game."""

    view: Minesweeper

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
            self.label = str(self.cell.value) if self.cell.value != 0 else "‎"
        else:
            self.label = "‎"
            self.style = discord.ButtonStyle.blurple

        self.disabled = self.cell.revealed

    async def callback(self: MinesweeperButton, interaction: discord.Interaction) -> None:
        assert self.view is not None

        self.view.moves += 1
        self.cell = self.view.board[self.position[0]][self.position[1]]

        x, y = self.position
        field = self.view.board[x][y]

        if not self.view.engine.mark(field):
            await self.view.end(interaction, field=field)
            return

        if self.cell.value == 0:
            for button in self.view.container.walk_children():
                if isinstance(button, MinesweeperButton) and not button.disabled:
                    button._update_labels()
        else:
            self._update_labels()

        if self.view.engine.is_won:
            await self.view.end(interaction, True)
            return

        self.view.refresh_container()
        await interaction.response.edit_message(view=self.view)
