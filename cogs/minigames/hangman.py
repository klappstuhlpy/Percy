import asyncio
import contextlib
import enum
from abc import ABC
from typing import Set, AsyncIterable

import discord

from bot import Percy
from cogs.utils import formats
from cogs.utils.context import Context

HANG_MAN = [
    (
        ""
    ), (
        """
          _______
         |/      |
         |      
         |      
         |       
         |      
         |
        _|___
        """
    ), (
        """
          _______
         |/      |
         |      (_)
         |      
         |       
         |      
         |
        _|___
        """
    ), (
        """
          _______
         |/      |
         |      (_)
         |       |
         |       |
         |      
         |
        _|___
        """
    ), (
        """
          _______
         |/      |
         |      (_)
         |      \\|/
         |       |
         |      
         |
        _|___
        """
    ), (
        """
          _______
         |/      |
         |      (_)
         |      \\|/
         |       |
         |      / \\
         |
        _|___
        """
    ), (
        """
          _______
         |/      |
         |      (x)
         |      \\|/
         |       |
         |      / \\
         |
        _|___
        """
    )
]


class Action(enum.Enum):
    """Enum for the hangman game."""

    GUESSED_WORD = 1
    GUESSED_LETTER = 2
    GUESSED_WRONG = 3
    GUESSED_ALREADY = 4
    GUESSED_INVALID = 5
    NO_REMAINING_TRIES = 6


class WaitforHangman(contextlib.AsyncContextDecorator, ABC):
    def __init__(self, bot: Percy, ctx: Context | discord.Interaction, word: str):
        self.bot: Percy = bot
        self.ctx: Context | discord.Interaction = ctx
        self.word: str = word

        self.guessed: Set[str] = set()
        self.fail_guessed: Set[str] = set()
        self.remaining_guesses: int = len(HANG_MAN) + 1
        self._current_colour: formats.Colour = formats.Colour.light_orange()

        self._current_state_index: int = 0
        self._current_state = HANG_MAN[self._current_state_index]

    async def __aenter__(self) -> 'WaitforHangman':
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def wait_for(self) -> AsyncIterable[tuple[Action, discord.Message]]:
        """Starts a guessing session, and yields the result state of every type."""
        while not self.bot.is_closed():
            try:
                message = await self.bot.wait_for('message', check=lambda m: m.author == self.ctx.user, timeout=300.0)
            except asyncio.TimeoutError as exc:
                self._current_colour = formats.Colour.red()
                self.update_state(-1)
                yield exc
                break
            else:
                content = message.content.lower()

                if content == self.word.lower():
                    self._current_colour = formats.Colour.lime_green()
                    yield Action.GUESSED_WORD, message
                    break

                elif content in self.guessed:
                    yield Action.GUESSED_ALREADY, message

                elif message.content.isdigit():
                    yield Action.GUESSED_INVALID, message

                elif len(content) > 1:
                    yield Action.GUESSED_INVALID, message

                elif content in self.word:
                    self.guessed.add(content)
                    yield Action.GUESSED_LETTER, message
                    self.update_remaining(content)

                else:
                    self.fail_guessed.add(content)
                    self.update_remaining(content)
                    self.update_state()
                    yield Action.GUESSED_WRONG, message

        yield None

    def build_hang_man(self):
        return (
            discord.Embed(
                title='Hangman',
                description=f"```\n{self._current_state}```",
                colour=self._current_colour,
            )
        )

    def build_embed(self):
        return (
            discord.Embed(
                title='Hangman',
                description='Guess the word by typing a letter. You have 5 minutes to guess the word.',
                colour=self._current_colour,
            )
            .add_field(name="Word", value=f"**{self.hidden_word}**", inline=False)
            .add_field(name="Guessed", value=f"**{self.gussed_letters}**", inline=False)
            .set_footer(text=f'You have {self.remaining_guesses} guesses remaining.')
        )

    @property
    def gussed_letters(self) -> str:
        """Returns the guessed letters."""
        return ' '.join(letter if letter in self.guessed else f'~~{letter}~~' for letter in (self.guessed | self.fail_guessed)) or '/'

    @property
    def hidden_word(self) -> str:
        """Returns the hidden word."""
        if self._current_colour == formats.Colour.red() or self._current_colour == formats.Colour.lime_green():
            return self.word
        return discord.utils.escape_markdown(
            ''.join(
                letter if letter.lower() in self.guessed else ' ' if letter.isspace() else '_' for letter in self.word)
        )

    def update_remaining(self, text: str):
        self.remaining_guesses -= len(text)
        self.remaining_guesses = max(self.remaining_guesses, 0)

    def update_state(self, index: int = None):
        if index:
            self._current_state_index = index
        else:
            self._current_state_index += 1
        self._current_state = HANG_MAN[self._current_state_index]
