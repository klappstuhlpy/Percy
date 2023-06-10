from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands

from . import tictactoe, minesweeper, hangman
from .hangman import WaitforHangman
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
        return discord.PartialEmoji(name='\N{VIDEO GAME}', id=None)

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

        prompt = tictactoe.Prompt(ctx.author._user, other._user)
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

        ms = minesweeper.Minesweeper(ctx, mines=mines)
        await ctx.send(embed=ms.build_embed(), view=ms)

    @command(
        commands.hybrid_command,
        name='hangman',
        description='Play a Hangman game.',
    )
    @app_commands.choices(
        language=[
            app_commands.Choice(name='English', value='en'),
            app_commands.Choice(name='German', value='de'),
        ]
    )
    async def hangman(self, ctx: Context, language: Literal["de", "en"] = "en"):
        """Play hangman with the bot."""

        """async with self.bot.session.get('https://random-word-api.vercel.app/api?words=1') as resp:
            if resp.status != 200:
                return await ctx.send('Something went wrong while fetching the word.', ephemeral=True)
            data = await resp.json()
            word = data[0]"""

        GER_WORDS_URL = 'https://raw.githubusercontent.com/enz/german-wordlist/master/words'
        ENG_WORDS_URL = 'https://raw.githubusercontent.com/mjmcloughlin10/hangman-words/main/words.txt'

        async with self.bot.session.get(ENG_WORDS_URL if language == 'en' else GER_WORDS_URL) as resp:
            if resp != 200:
                return await ctx.send(f'{ctx.tick(False)} Something went wrong while fetching the word.', ephemeral=True)

            data = await resp.text()
            word = data.split('\n')[random.randint(0, len(data.split('\n')) - 1)]

        async with WaitforHangman(self.bot, ctx, word) as builder:
            message = await ctx.send(embeds=[builder.build_hang_man(), builder.build_embed()])

            async for action in builder.wait_for():
                message = await message.edit(embeds=[builder.build_hang_man(), builder.build_embed()])

                if isinstance(action, asyncio.TimeoutError):
                    await ctx.send('<:redTick:1079249771975413910> You took too long to guess the word.', ephemeral=True)
                elif action == hangman.Action.GUESSED_WORD or action == hangman.Action.GUESSED_ALL:
                    pass
                elif action == hangman.Action.GUESSED_ALREADY:
                    await ctx.send('<:redTick:1079249771975413910> You already guessed that letter.', ephemeral=True)
                elif action == hangman.Action.GUESSED_INVALID:
                    await ctx.send('<:redTick:1079249771975413910> Invalid guess. Please enter a single letter.',
                                   ephemeral=True)

                if builder.errors == 6:  # coalcase because we already checked for wins
                    builder.finished = -1
                    builder._current_colour = helpers.Colour.red()
                    builder.guessed.update(builder.word)
                    message = await message.edit(embeds=[builder.build_hang_man(), builder.build_embed()])


async def setup(bot: Percy):
    await bot.add_cog(Minigame(bot))
