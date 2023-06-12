import asyncio
import contextlib
import enum
from abc import ABC
from typing import Set, AsyncIterable, Literal

import discord

from bot import Percy
from cogs.utils import helpers
from cogs.utils.context import Context
from cogs.utils.constants import HANG_MAN


class Action(enum.Enum):
    """Enum for the hangman game."""

    GUESSED_WORD = 1
    GUESSED_LETTER = 2
    GUESSED_WRONG = 3
    GUESSED_ALREADY = 4
    GUESSED_INVALID = 5
    NO_REMAINING_TRIES = 6
    GUESSED_ALL = 7


class WaitforHangman(contextlib.AsyncContextDecorator, ABC):
    def __init__(self, bot: Percy, ctx: Context | discord.Interaction, word: str):
        self.bot: Percy = bot
        self.ctx: Context | discord.Interaction = ctx
        self.word: str = word

        self.guessed: Set[str] = set()
        self.fail_guessed: Set[str] = set()
        self.errors: int = 0
        self._current_colour: helpers.Colour = helpers.Colour.light_grey()

        self._current_state_index: int = 0
        self._current_state: str = HANG_MAN[0]
        self.finished: Literal[1, 0, -1] = 0  # 1 = won, 0 = not finished, -1 = lost

    async def __aenter__(self) -> 'WaitforHangman':
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def wait_for(self) -> AsyncIterable[Action | Exception]:
        """Starts a guessing session, and yields the result state of every type."""
        while not self.bot.is_closed():
            try:
                message = await self.bot.wait_for('message', check=lambda m: m.author == self.ctx.user, timeout=300.0)
            except asyncio.TimeoutError as exc:
                if self.finished not in (1, -1):
                    self._current_colour = helpers.Colour.red()
                yield exc
                break
            else:
                content = message.content.lower()

                if content == self.word.lower():
                    self.finished = 1
                    self._current_colour = helpers.Colour.lime_green()
                    yield Action.GUESSED_WORD
                    break

                elif content in self.guessed or content in self.fail_guessed:
                    yield Action.GUESSED_ALREADY

                elif content.isdigit() or len(content) > 1:
                    yield Action.GUESSED_INVALID

                elif content in self.word:
                    self.guessed.add(content)
                    yield Action.GUESSED_LETTER

                else:
                    self.fail_guessed.add(content)
                    self.errors += 1
                    self.update_state()
                    yield Action.GUESSED_WRONG

                if set(self.word).issubset(self.guessed):
                    self.finished = 1
                    self._current_colour = helpers.Colour.lime_green()
                    yield Action.GUESSED_ALL
                    break

                if self.errors == 6:
                    yield Action.NO_REMAINING_TRIES
                    break

        yield None

    def build_embed(self):
        state = " guessed the word with " if self.finished == 1 else " lost with " if self.finished == -1 else " "
        text = f'You have{state}{self.errors}/6 errors.'

        return (
            discord.Embed(
                title='Hangman',
                description=f'**You\'ve guessed `{self.hidden_word}` so far.**\n'
                            'Guess the word by typing a letter. You have 5 minutes to guess the word.',
                colour=self._current_colour,
            )
            .set_image(url=self._current_state)
            .add_field(name="Tried Words", value=f"**`{self.gussed_letters}`**", inline=False)
            .set_footer(text=text)
        )

    @property
    def gussed_letters(self) -> str:
        """Returns the guessed letters."""
        return ' '.join(
            letter if letter in self.guessed else f'~~{letter}~~' for letter in (self.guessed | self.fail_guessed)
        ) or '\u200b'

    @property
    def hidden_word(self) -> str:
        """Returns the hidden word."""
        return discord.utils.escape_markdown(
            ' '.join(
                letter if letter.lower() in self.guessed else ' ' if letter.isspace() else '_' for letter in self.word)
        )

    def update_state(self):
        self._current_state_index += 1
        self._current_state = HANG_MAN[self._current_state_index]
