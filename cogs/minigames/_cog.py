from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands

from . import _tictactoe, _minesweeper, _hangman
from ._hangman import WaitforHangman
from .. import command
from ..utils import helpers

if TYPE_CHECKING:
    from bot import Percy
    from ..utils.context import GuildContext, Context


class Minigame(commands.GroupCog):
    """Simple mini-games to play with others"""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='\N{VIDEO GAME}')

    def __repr__(self) -> str:
        return '<cogs.Minigame>'

    @command(
        commands.hybrid_command,
        name='tictactoe',
        description='Play a TicTacToe party with another user.',
        aliases=['ttt'],
    )
    @commands.guild_only()
    @app_commands.guild_only()
    @app_commands.rename(other='with')
    @app_commands.describe(other='The opponent to play with')
    async def tictactoe(self, ctx: GuildContext, *, other: discord.Member):
        """Play a TicTacToe party with another user."""
        if other.bot:
            return await ctx.send('You cannot play against a bot', ephemeral=True)

        prompt = _tictactoe.Prompt(ctx.author._user, other._user)  # noqa
        embed = discord.Embed(
            title="TicTacToe",
            description=f'{other.mention} has been challenged to a TicTacToe party by {ctx.author.mention}.\n'
                        f'Do you accept this party, {other.mention}?',
            colour=helpers.Colour.light_orange(),
        )
        msg = await ctx.send(embed=embed,  view=prompt)

        await prompt.wait()
        await msg.delete(delay=1)

    @command(
        commands.hybrid_command,
        name='minesweeper',
        aliases=['ms'],
        description='Play a Minesweeper game.',
    )
    @app_commands.describe(mines='The amount of mines to play with')
    async def minesweeper(self, ctx: Context, *, mines: int = 3):
        """Play a Minesweeper Game."""
        if mines < 3 or mines > 25:
            return await ctx.send('The amount of mines must be greater than equal to 3 and less than 25.',
                                  ephemeral=True)

        ms = _minesweeper.Minesweeper(ctx, mines=mines)
        await ctx.send(embed=ms.build_embed(), view=ms)

    @command(
        commands.hybrid_command,
        name='_hangman',
        description='Play a Hangman game.',
    )
    @app_commands.choices(
        language=[
            app_commands.Choice(name='English', value='en'),
            app_commands.Choice(name='German', value='de'),
        ]
    )
    @app_commands.describe(language='The language to play with.')
    async def _hangman(self, ctx: Context, language: Literal["de", "en"] = "en"):
        """Play _hangman with the bot."""

        GER_WORDS_URL = 'https://raw.githubusercontent.com/enz/german-wordlist/master/words'
        ENG_WORDS_URL = 'https://raw.githubusercontent.com/mjmcloughlin10/_hangman-words/main/words.txt'

        async with self.bot.session.get(ENG_WORDS_URL if language == 'en' else GER_WORDS_URL) as resp:
            data = await resp.text()
            word = data.split('\n')[random.randint(0, len(data.split('\n')) - 1)]

        async with WaitforHangman(self.bot, ctx, word) as builder:
            message = await ctx.send(f"*If you want to stop the game, type `?abort`.*", embed=builder.build_embed())

            async for action in builder.wait_for():
                message = await message.edit(embed=builder.build_embed())

                if isinstance(action, asyncio.TimeoutError):
                    await ctx.send('<:redTick:1079249771975413910> You took too long to guess the word.', delete_after=10)
                elif action == _hangman.Action.ABORTED:
                    await ctx.send('<:redTick:1079249771975413910> You aborted the game.', delete_after=5)
                elif action == _hangman.Action.GUESSED_WORD or action == _hangman.Action.GUESSED_ALL:
                    await ctx.send('<:greenTick:1079249732364406854> You\'ve guessed the word. Congratulations!')
                elif action == _hangman.Action.GUESSED_ALREADY:
                    await ctx.send('<:redTick:1079249771975413910> You already guessed that letter.', delete_after=5)
                elif action == _hangman.Action.GUESSED_INVALID:
                    await ctx.send('<:redTick:1079249771975413910> Invalid guess. Please enter a single letter.',
                                   delete_after=5)
                elif action == _hangman.Action.NO_REMAINING_TRIES or builder.errors == 6:
                    builder.finished = -1
                    builder._current_colour = helpers.Colour.red()
                    await message.edit(embed=builder.build_embed())
                    await ctx.send(f"<:redTick:1079249771975413910> You've lost. The word was **`{builder.word}`**.")


async def setup(bot: Percy):
    await bot.add_cog(Minigame(bot))
