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
        "https://klappstuhl.me/gallery/raw/RaPNv.png",  # Hangman 0
        "https://klappstuhl.me/gallery/raw/YsDeg.png",
        "https://klappstuhl.me/gallery/raw/PnXlL.png",
        "https://klappstuhl.me/gallery/raw/WstIp.png",
        "https://klappstuhl.me/gallery/raw/FpYQR.png",
        "https://klappstuhl.me/gallery/raw/ZTKmW.png",
        "https://klappstuhl.me/gallery/raw/nJnEN.png",
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

    def build_container(
            self,
            won: bool | None = None,
    ) -> discord.ui.Container:
        """Build the Components V2 card for the hangman table."""
        guess = "".join(letter.upper() if letter in self.used else "_" for letter in self.word)

        container = discord.ui.Container()

        if won:
            container.accent_colour = helpers.Colour.lime_green()
        elif won is False:
            container.accent_colour = helpers.Colour.light_red()
        else:
            container.accent_colour = helpers.Colour.white()

        container.add_item(
            discord.ui.Section(
                f"## Hangman\n"
                f"**Progress:** {Emojis.empty} {self.health_bar}\n"
                f"```prolog\n"
                f"{guess}```",
                accessory=discord.ui.Thumbnail(self.IMAGES[6 - self.tries]),
            )
        )

        container.add_item(discord.ui.Separator())

        if won is not None:
            container.add_item(discord.ui.TextDisplay(f"**The word was:** {self.word.upper()}"))

        container.add_item(discord.ui.TextDisplay(
            f"**Used letters:** {', '.join(letter.upper() for letter in self.used) if self.used else '...'}"))

        if self._last_input:
            container.add_item(discord.ui.TextDisplay(self._last_input))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f'-# Player: {self.player} | Type "abort" to stop the game.'))

        return container

    def render(self, won: bool | None = None) -> discord.ui.LayoutView:
        """Render the hangman game as an embed."""
        container = self.build_container(won)
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(container)
        return view
