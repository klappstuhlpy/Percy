from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

import discord

from app.cogs.games.models import Game, GameResult
from app.core.views import LayoutView
from app.utils import fnumb, helpers
from config import Emojis

if TYPE_CHECKING:
    from app.cogs.games.engine.trivia import TriviaRound

__all__ = ("TRIVIA_REWARD", "TriviaView")

TRIVIA_REWARD: int = 200
_LETTERS = ("A", "B", "C", "D")


class AnswerButton(discord.ui.Button["TriviaView"]):
    def __init__(self, index: int, label: str) -> None:
        self.index = index
        super().__init__(style=discord.ButtonStyle.blurple, label=f"{_LETTERS[index]}. {label[:70]}")

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.on_answer(interaction, self.index)


class TriviaView(LayoutView):
    """Multiplayer trivia — first member to click the correct answer wins the reward.

    A wrong click eliminates that member from the round (and records a loss); the
    round ends on the first correct answer or on timeout (answer revealed, no winner).
    Open to everyone, so it is intentionally *not* member-gated.
    """

    def __init__(self, trivia: TriviaRound, *, reward: int = TRIVIA_REWARD, timeout: float = 25.0) -> None:
        super().__init__(timeout=timeout)
        self.trivia = trivia
        self.reward = reward
        self.winner: discord.Member | discord.User | None = None
        self.finished: bool = False
        self._eliminated: set[int] = set()

        self.buttons = [AnswerButton(i, opt) for i, opt in enumerate(trivia.options)]
        self._compose()

    # -- rendering --------------------------------------------------------

    def _compose(self) -> None:
        self.clear_items()

        colour = helpers.Colour.white()
        if self.finished:
            colour = helpers.Colour.lime_green() if self.winner else helpers.Colour.light_red()

        container = discord.ui.Container(accent_colour=colour)
        container.add_item(discord.ui.TextDisplay(
            f"## \N{BLACK QUESTION MARK ORNAMENT} Trivia • {self.trivia.category}"
        ))
        container.add_item(discord.ui.TextDisplay(f"**{self.trivia.question}**"))
        container.add_item(discord.ui.Separator())

        if self.finished:
            correct = f"{_LETTERS[self.trivia.correct_index]}. {self.trivia.correct}"
            if self.winner:
                container.add_item(discord.ui.TextDisplay(
                    f"`\N{WHITE HEAVY CHECK MARK}` {self.winner.mention} got it first: **{correct}**\n"
                    f"Reward: {Emojis.Economy.cash} **{fnumb(self.reward)}**"
                ))
            else:
                container.add_item(discord.ui.TextDisplay(
                    f"`\N{ALARM CLOCK}` Time's up! The answer was **{correct}**."
                ))
        else:
            for button in self.buttons:
                button.disabled = False
                container.add_item(discord.ui.ActionRow(button))
            container.add_item(discord.ui.TextDisplay(
                f"-# First correct answer wins {Emojis.Economy.cash} **{fnumb(self.reward)}**."
            ))

        self.add_item(container)

    def _disable(self) -> None:
        for button in self.buttons:
            button.disabled = True
            if button.index == self.trivia.correct_index:
                button.style = discord.ButtonStyle.green

    # -- outcomes ---------------------------------------------------------

    async def on_answer(self, interaction: discord.Interaction, index: int) -> None:
        if self.finished:
            await interaction.response.send_message(f"{Emojis.error} This round is already over.", ephemeral=True)
            return
        if interaction.user.id in self._eliminated:
            await interaction.response.send_message(f"{Emojis.error} You already answered this round.", ephemeral=True)
            return

        assert interaction.guild is not None
        game_stats = interaction.client.db.game_stats

        if index == self.trivia.correct_index:
            self.finished = True
            self.winner = interaction.user
            balance = await interaction.client.db.get_user_balance(interaction.user.id, interaction.guild.id)
            await balance.add(cash=self.reward)
            self._disable()
            self._compose()
            await interaction.response.edit_message(view=self)
            await game_stats.record_result(
                interaction.guild.id, interaction.user.id, Game.TRIVIA, GameResult.WIN, profit=self.reward
            )
            self.stop()
        else:
            self._eliminated.add(interaction.user.id)
            await interaction.response.send_message(
                f"{Emojis.error} Wrong answer — you're out for this round.", ephemeral=True
            )
            await game_stats.record_result(
                interaction.guild.id, interaction.user.id, Game.TRIVIA, GameResult.LOSS
            )

    async def on_timeout(self) -> None:
        if self.finished:
            return
        self.finished = True
        self._disable()
        self._compose()
        if self.message:
            with suppress(discord.HTTPException):
                await self.message.edit(view=self)
