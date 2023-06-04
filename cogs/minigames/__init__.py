from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from . import tictactoe, minesweeper, hangman
from .hangman import WaitforHangman
from .. import command
from ..utils import formats

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
            colour=formats.Colour.light_orange(),
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
    async def hangman(self, ctx: Context):
        """Play hangman with the bot."""

        async with self.bot.session.get('https://random-word-api.vercel.app/api?words=1') as resp:
            if resp.status != 200:
                return await ctx.send('Something went wrong while fetching the word.', ephemeral=True)
            data = await resp.json()
            word = data[0]

        async with WaitforHangman(self.bot, ctx, word) as builder:
            message = await ctx.send(embeds=[builder.build_hang_man(), builder.build_embed()])

            async for result in builder.wait_for():
                message = await message.edit(embeds=[builder.build_hang_man(), builder.build_embed()])

                if isinstance(result, asyncio.TimeoutError):
                    return await ctx.send('<:redTick:1079249771975413910> You took too long to guess the word.', ephemeral=True)
                elif result == hangman.Action.GUESSED_WORD:
                    embed = message.embeds[1].set_footer(text='You guessed the word!')
                    return await message.edit(embeds=[message.embeds[0], embed])
                elif result == hangman.Action.GUESSED_ALREADY:
                    await ctx.send('You already guessed that letter.', ephemeral=True)

                if builder.remaining_guesses == 0:  # coalcase because we already checked for wins
                    builder._current_colour = formats.Colour.red()
                    builder.guessed.update(builder.word)
                    e = builder.build_embed()
                    e.set_footer(text='You loose! You have no tries left.')
                    return await message.edit(embeds=[builder.build_hang_man(), e])


async def setup(bot: Percy):
    await bot.add_cog(Minigame(bot))
