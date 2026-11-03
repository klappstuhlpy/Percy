from __future__ import annotations

import discord

from app.cogs.games.engine.wordle import MAX_TRIES, LetterState, is_solved, score_guess
from app.core.views import LayoutView
from app.utils import helpers
from config import Emojis

__all__ = ("Wordle",)

_EMPTY_ROW = "\N{WHITE LARGE SQUARE}" * 5


class Wordle(LayoutView):
    """Render-only state for a Wordle game; the cog drives the guess loop.

    Holds the answer and the scored guesses so far and rebuilds the board on every
    ``render`` call (mirroring the Hangman UI). Has no buttons — input arrives as
    chat messages handled by the cog.
    """

    def __init__(self, player: discord.Member, answer: str) -> None:
        super().__init__(timeout=None)
        self.player = player
        self.answer = answer
        self.guesses: list[tuple[str, list[LetterState]]] = []
        self.finished: bool = False
        self.won: bool = False
        self._note: str | None = None

    @property
    def tries_used(self) -> int:
        return len(self.guesses)

    @property
    def tries_left(self) -> int:
        return MAX_TRIES - len(self.guesses)

    def add_guess(self, guess: str) -> list[LetterState]:
        """Scores and records a guess, flipping ``finished``/``won`` as needed."""
        states = score_guess(guess, self.answer)
        self.guesses.append((guess.lower(), states))
        if is_solved(states):
            self.finished = self.won = True
        elif len(self.guesses) >= MAX_TRIES:
            self.finished = True
        return states

    def render(self, *, note: str | None = None, reveal: bool = False) -> Wordle:
        self._note = note
        self.clear_items()

        if self.finished:
            colour = helpers.Colour.lime_green() if self.won else helpers.Colour.light_red()
        else:
            colour = helpers.Colour.white()

        container = discord.ui.Container(accent_colour=colour)
        container.add_item(discord.ui.TextDisplay("## \N{LARGE GREEN SQUARE} Wordle"))
        container.add_item(discord.ui.TextDisplay(
            f"Guess the **5-letter** word in **{MAX_TRIES}** tries. Type your guess in chat."
        ))
        container.add_item(discord.ui.Separator())

        board_lines: list[str] = []
        for guess, states in self.guesses:
            squares = "".join(state.square for state in states)
            board_lines.append(f"{squares}  `{' '.join(guess.upper())}`")
        board_lines.extend([_EMPTY_ROW] * self.tries_left)
        container.add_item(discord.ui.TextDisplay("\n".join(board_lines)))

        if self._note:
            container.add_item(discord.ui.TextDisplay(self._note))

        if self.finished:
            container.add_item(discord.ui.Separator())
            if self.won:
                container.add_item(discord.ui.TextDisplay(
                    f"`\N{WHITE HEAVY CHECK MARK}` Solved in **{self.tries_used}**/{MAX_TRIES}!"
                ))
            else:
                container.add_item(discord.ui.TextDisplay(
                    f"`\N{CROSS MARK}` Out of tries — the word was **{self.answer.upper()}**."
                ))
        elif reveal:
            container.add_item(discord.ui.TextDisplay(f"-# {Emojis.warning} The word was **{self.answer.upper()}**."))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"-# Player: {self.player} • type \"abort\" to cancel"))
        self.add_item(container)
        return self
