from __future__ import annotations

import asyncio
import enum
import inspect
import random
from typing import TYPE_CHECKING, ClassVar

import discord
import numpy as np

from app.cogs.emoji import EMOJI_REGEX
from app.cogs.games.models import Game, GameResult
from app.core.views import LayoutView
from app.utils import find_word, fnumb, helpers
from config import Emojis

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


class Fruits(enum.Enum):
    """Enum class representing the fruits in the slot machine."""

    MELON = "🍈"
    BANANA = "🍌"
    APPLE = "🍎"
    TANGERINE = "🍊"
    PEACH = "🍑"
    WATERMELON = "🍉"
    CHERRY = "🍒"
    LEMON = "🍋"
    STRAWBERRY = "🍓"
    PEAR = "🍐"
    PINEAPPLE = "🍍"
    GRAPE = "🍇"

    COOL = "🆒"


class SlotMachine(LayoutView):
    """Represents a slot machine with fruits representing each slot.

    This class uses numpy arrays to store slot values and perform calculations on winning etc.
    """

    PLACEHOLDER: ClassVar[str] = "<a:slot:1322359593073905725>"
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
        super().__init__(members=[player])
        self.player: discord.Member = player
        self.bet: int = bet

        self.rows: int = rows
        self.columns: int = columns

        # creates a rows x columns 2D array of random fruits
        self.slots: np.ndarray | None = None
        self.finished: bool = False

        self._start = discord.ui.Button(label="Start", style=discord.ButtonStyle.blurple)
        self._start.callback = self._on_start  # type: ignore[assignment]
        self._reset = discord.ui.Button(label="Reset", style=discord.ButtonStyle.green)
        self._reset.callback = self._on_reset  # type: ignore[assignment]
        self._reset.disabled = True

        self._compose()

    def __str__(self) -> str:
        return self.build()

    def build_container(self, display: str | None = None, status: str | None = None, *, buttons: bool = True) -> discord.ui.Container:
        """Builds the Components V2 slot-machine card.

        ``display`` overrides the reel art (used during the reveal animation); ``status``
        is the optional win/loss line shown after a spin resolves.
        """
        container = discord.ui.Container(accent_colour=helpers.Colour.white())
        reels = display if display is not None else self.build()

        container.add_item(discord.ui.TextDisplay(f"## 🎰 Slot Machine\n{self.DESC_TITLE}"))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(reels))
        container.add_item(discord.ui.Separator())

        container.add_item(discord.ui.TextDisplay(f"Bet: **{fnumb(self.bet)}** {Emojis.Economy.cash}"))

        if status:
            container.add_item(discord.ui.TextDisplay(status))

        if buttons:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.ActionRow(self._start, self._reset))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"-# Player: {self.player}"))

        return container

    def roll(self) -> None:
        """Roll the slot machine."""
        self.slots = np.array(
            [[random.choice(list(Fruits.__members__.values())) for _ in range(self.columns)] for _ in range(self.rows)]
        )

    def build(self, reveal_to_row: int | None = None) -> str:
        """Create a 2D numpy array with the emojis in their positions."""
        if self.slots is None:
            return self._format_build(np.full((self.rows, self.columns), self.PLACEHOLDER))

        if reveal_to_row:
            return self._format_build(
                np.array(
                    [
                        [slot.value if i < reveal_to_row else self.PLACEHOLDER for i, slot in enumerate(row)]
                        for row in self.slots
                    ]
                )
            )

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
        one = "\N{DIGIT ONE}"
        two = "\N{DIGIT TWO}"
        three = "\N{DIGIT THREE}"

        val_arr = ["═" * (self.columns * self.rows)] * self.rows
        start = Emojis.empty * 6
        sep = Emojis.empty + " `║` " + Emojis.empty

        parts = [
            start + "`╔" + "╦".join(val_arr) + "╗`" + Emojis.empty,
            "\n".join(start + "`║` " + Emojis.empty + sep.join(row) + sep for row in arr.tolist()),
            start + "`╚" + "╩".join(val_arr) + "╝`" + Emojis.empty,
        ]

        cl_text = EMOJI_REGEX.sub("x", parts[2])
        _, _, end = find_word(cl_text, "╩")
        middle = (end - ((self.columns * self.rows) / 2)) - 1
        parts.append(f"{start}`{one:^{middle}}{two:^{middle - self.columns - 1}}{three:^{middle - 1}}`{Emojis.empty}")
        return "\n".join(parts)

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
            await interaction.response.send_message(f"{Emojis.error} This isn't your game.", ephemeral=True)
            return False
        return True

    def _compose(self, display: str | None = None, status: str | None = None, *, buttons: bool = True) -> None:
        """Recompose the layout: the slot card plus the control row (omitted while rolling)."""
        self.clear_items()
        self.add_item(self.build_container(display, status, buttons=buttons))

    async def _on_start(self, interaction: discord.Interaction) -> None:
        """Spins the slot machine, revealing one row at a time."""
        self._start.disabled = True
        self._reset.disabled = False

        self._compose(buttons=False)
        await interaction.response.edit_message(view=self)

        async for build in self.walk_build():
            self._compose(display=build, buttons=False)
            await interaction.edit_original_response(view=self)

        self.finished = True

        win, multiplier = self.get_winning(self.bet)
        if win:
            balance = await interaction.client.db.get_user_balance(self.player.id, interaction.guild_id)
            await balance.add(cash=win)
            status = f"`\N{WHITE HEAVY CHECK MARK} You won {multiplier}x your bet!`"
        else:
            status = "`\N{CROSS MARK} Better luck next time!`"

        await interaction.client.db.game_stats.record_result(
            interaction.guild_id,
            self.player.id,
            Game.SLOTS,
            GameResult.WIN if win else GameResult.LOSS,
            wagered=self.bet,
            profit=win - self.bet,
        )

        self._compose(status=status)
        await interaction.edit_original_response(view=self)

    async def _on_reset(self, interaction: discord.Interaction) -> None:
        """Reset and re-bet for another spin."""
        self.reset()

        self._start.disabled = False
        self._reset.disabled = True

        balance = await interaction.client.db.get_user_balance(self.player.id, interaction.guild_id)
        if self.bet > balance.cash:
            await interaction.response.send_message(
                f"{Emojis.error} You do not have enough money to bet that amount.\n"
                f"You currently have {Emojis.Economy.cash} **{fnumb(balance.cash)}** in **cash**.",
                ephemeral=True,
            )
            return

        await balance.remove(cash=self.bet)

        self._compose()
        await interaction.response.edit_message(view=self)
