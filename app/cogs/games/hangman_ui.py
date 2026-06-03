from __future__ import annotations

from typing import Final

import discord

from app.utils import helpers
from app.utils.helpers import HealthBarBuilder
from config import Emojis

__all__ = ("Hangman",)


class Hangman:
    """A class to represent a hangman game."""

    IMAGES: Final[list[str]] = [
        "https://klappstuhl.me/gallery/RaPNv.png",  # Hangman 0
        "https://klappstuhl.me/gallery/YsDeg.png",
        "https://klappstuhl.me/gallery/PnXlL.png",
        "https://klappstuhl.me/gallery/WstIp.png",
        "https://klappstuhl.me/gallery/FpYQR.png",
        "https://klappstuhl.me/gallery/ZTKmW.png",
        "https://klappstuhl.me/gallery/nJnEN.png",
    ]

    def __init__(self, player: discord.Member, word: str) -> None:
        self.player: discord.Member = player
        self.word: str = word

        self._tries: int = 6
        self.used: set[str] = set()
        self.letters: set[str] = set(self.word.lower())

        self.finished: bool = False
        self.health_bar: HealthBarBuilder = HealthBarBuilder(self._tries)

        self._last_input: str | None = "`\N{INFORMATION SOURCE} Type a letter or the word you want to guess.`"

    @property
    def tries(self) -> int:
        """:class:`int`: The amount of tries left."""
        return self._tries

    @tries.setter
    def tries(self, value: int) -> None:
        """Set the amount of tries left."""
        if not isinstance(value, int):
            raise TypeError("Tries must be an integer.")

        self._tries = value
        self.health_bar -= 1

    def build_embed(self, won: bool | None = None) -> discord.Embed:
        """Build the embed."""
        guess = "".join(letter.upper() if letter in self.used else "-" for letter in self.word)
        embed = discord.Embed(
            title="Hangman",
            description=f"**Progress:** {Emojis.empty} {self.health_bar}\n```prolog\n{guess}```",
        )

        if won is True:
            embed.colour = helpers.Colour.lime_green()
        elif won is False:
            embed.colour = helpers.Colour.light_red()
        else:
            embed.colour = helpers.Colour.white()

        if won is not None:
            embed.description += f"\nThe word was: **{self.word.upper()}**"
        else:
            embed.description += (
                f"\nUsed letters: **{', '.join(letter.upper() for letter in self.used) if self.used else '...'}**\n\n"
            )
            if self._last_input:
                embed.description += self._last_input

        embed.set_thumbnail(url=self.IMAGES[6 - self.tries])
        embed.set_footer(text=f'Player: {self.player} | Type "abort" to stop the game.')
        return embed
