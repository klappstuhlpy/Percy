from __future__ import annotations

import asyncio
import enum
import random
from typing import TYPE_CHECKING, Literal, Annotated, Dict

import discord
from discord import app_commands
from expiringdict import ExpiringDict

from cogs.games import _tictactoe, _minesweeper, _hangman, _blackjack, _roulette
from ._hangman import WaitforHangman
from ._roulette import Space
from ..economy import Economy
from ..utils import helpers, commands, errors, fuzzy
from ..utils.constants import cash_emoji, WORKING_RESPONSES, SUCCESSFULL_CRIME_RESPONSES, FAILED_CRIME_RESPONSES, \
    SUCCESSFULL_SLUT_RESPONSES, FAILED_SLUT_RESPONSES

if TYPE_CHECKING:
    from bot import Percy
    from ..utils.context import GuildContext, Context


class MinimumBet(enum.Enum):
    """Minimum Bets for Games."""

    BLACKJACK = 100
    ROULETTE = 100


async def roulette_space_autocomplete(
        interaction: discord.Interaction, current: str  # noqa
) -> list[app_commands.Choice[int]]:
    results = fuzzy.finder(
        current, [space for space in Space], key=lambda p: p.value)
    print(results)
    return [
        app_commands.Choice(name=space.value, value=space.value) for space in results[:20]
    ]


class Payouts(enum.Enum):
    WORK_PAYOUT_MIN = 20
    WORK_PAYOUT_MAX = 250
    WORK_COODLWON = 7200.0  # 2 hours

    CRIME_PAYOUT_MIN = 250
    CRIME_PAYOUT_MAX = 700
    CRIME_FINE_MIN = 0.2  # 20%
    CRIME_FINE_MAX = 0.4  # 40%
    CRIME_FAIL_RATE = 0.6  # 60%
    CRIME_COOLDOWN = 86400.0  # 1 Day

    SLUT_PAYOUT_MIN = 100
    SLUT_PAYOUT_MAX = 400
    SLUT_FINE_MIN = 0.1  # 10%
    SLUT_FINE_MAX = 0.2  # 20%
    SLUT_FAIL_RATE = 0.35  # 35%
    SLUT_COODLWON = 14400.0  # 4 hours


class Games(commands.GroupCog):
    """Play games against the bot or other players to earn money."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

        self.economy: Economy = bot.get_cog('Economy')  # noqa

        self.blackjack_tables: Dict[int, _blackjack.Table] = ExpiringDict(max_len=1000, max_age_seconds=21600)
        self.roulette_tables: Dict[int, _roulette.RouletteTable] = {}

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='\N{VIDEO GAME}')

    def __repr__(self) -> str:
        return '<cogs.Games>'

    @commands.command(
        commands.hybrid_command,
        name='tictactoe',
        description='Play a TicTacToe party with another user.',
        aliases=['ttt']
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
            title='TicTacToe',
            description=f'{other.mention} has been challenged to a TicTacToe party by {ctx.author.mention}.\n'
                        f'Do you accept this party, {other.mention}?',
            colour=helpers.Colour.light_orange(),
        )
        msg = await ctx.send(embed=embed,  view=prompt)

        await prompt.wait()
        await msg.delete(delay=1)

    @commands.command(
        commands.hybrid_command,
        name='minesweeper',
        aliases=['ms'],
        description='Play a Minesweeper game.',
        guild_only=True
    )
    @app_commands.describe(mines='The amount of mines to play with')
    async def minesweeper(self, ctx: Context, *, mines: int = 3):
        """Play a Minesweeper Game."""
        if mines < 3 or mines > 25:
            return await ctx.send(
                'The amount of mines must be greater than equal to 3 and less than 25.', ephemeral=True)

        ms = _minesweeper.Minesweeper(ctx, mines=mines)
        await ctx.send(embed=ms.build_embed(), view=ms)

    @commands.command(
        commands.hybrid_command,
        name='hangman',
        description='Play a Hangman game.',
        guild_only=True
    )
    @app_commands.choices(
        language=[
            app_commands.Choice(name='English', value='en'),
            app_commands.Choice(name='German', value='de'),
        ]
    )
    @app_commands.describe(language='The language to play with.')
    async def hangman(self, ctx: Context, language: Literal['de', 'en'] = 'en'):
        """Play hangman with the bot."""

        GER_WORDS_URL = 'https://raw.githubusercontent.com/enz/german-wordlist/master/words'
        ENG_WORDS_URL = 'https://raw.githubusercontent.com/mjmcloughlin10/hangman-words/main/words.txt'

        async with self.bot.session.get(ENG_WORDS_URL if language == 'en' else GER_WORDS_URL) as resp:
            if resp.status != 200:
                return await ctx.send(f'Failed to fetch words from word List (**{language}**).')
            data = await resp.text()
            word = data.split('\n')[random.randint(0, len(data.split('\n')) - 1)]

        async with WaitforHangman(self.bot, ctx, word) as builder:
            message = await ctx.send(f'*If you want to stop the game, type `?abort`.*', embed=builder.build_embed())

            async for action in builder.wait_for():
                message = await message.edit(embed=builder.build_embed())

                if isinstance(action, asyncio.TimeoutError):
                    await ctx.stick(False, 'You took too long to guess the word.', delete_after=10)
                elif action == _hangman.Action.ABORTED:
                    await ctx.stick(False, 'You aborted the game.', delete_after=5)
                elif action in {_hangman.Action.GUESSED_WORD, _hangman.Action.GUESSED_ALL}:
                    # Earn money for guessing the word
                    user_balance = await self.economy.get_balance(ctx.author.id, ctx.guild.id)
                    amount: int = len(builder.word) * 15
                    await user_balance.add(amount, 'cash')

                    await ctx.stick(True, f'You\'ve guessed the word. Congratulations, you\'ve earned {cash_emoji} **{amount:,}**.')
                elif action == _hangman.Action.GUESSED_ALREADY:
                    await ctx.stick(False, 'You already guessed that letter.', delete_after=5)
                elif action == _hangman.Action.GUESSED_INVALID:
                    await ctx.stick(False, 'Invalid guess. Please enter a single letter.', delete_after=5)
                elif action == _hangman.Action.NO_REMAINING_TRIES or builder.errors == 6:
                    builder.finished = -1
                    builder._current_colour = helpers.Colour.red()
                    await message.edit(embed=builder.build_embed())
                    await ctx.send(f'<:redTick:1079249771975413910> You\'ve lost. The word was **`{builder.word}`**.')

                    break  # Break out to prevent the bot from sending multiple messages

    @commands.command(
        commands.hybrid_command,
        name='blackjack',
        description='Play a Blackjack game.',
        aliases=['bj'],
        guild_only=True
    )
    @app_commands.describe(bet='The amount of coins to bet.')
    async def blackjack(self, ctx: Context, bet: int):
        if bet < 0:
            return await ctx.send('You cannot bet negative coins.', ephemeral=True)

        if bet < MinimumBet.BLACKJACK.value:
            return await ctx.send(f'You must bet at least {cash_emoji} **{MinimumBet.BLACKJACK.value:,}**.', ephemeral=True)

        balance = await self.economy.get_balance(ctx.author.id, ctx.guild.id)

        if bet > balance.cash:
            return await ctx.send(f'You do not have enough money to bet that amount.\n'
                                  f'You currently have {cash_emoji} **{balance.cash:,}** in **cash**.', ephemeral=True)

        await balance.remove(bet, 'cash')

        if ctx.author.id in self.blackjack_tables:
            blackjack = self.blackjack_tables[ctx.author.id]
            blackjack.wake_up(ctx, bet)
        else:
            blackjack = _blackjack.Table(ctx, bet=bet, decks=3)
            self.blackjack_tables[ctx.author.id] = blackjack

        # Shuffle cards ;)
        # Note that this is just for aesthetics
        embed = blackjack.build_embed(
            hand=blackjack.active_hand,
            image_url='https://i.imgur.com/TXIT1Sr.gif',
            colour=discord.Colour.light_grey(),
            text='*Shuffling Cards...*'
        )
        message = await ctx.send(embed=embed)
        blackjack.active_hand.message = message

        await asyncio.sleep(3)

        await blackjack.view.update_buttons(active=True)
        if not await blackjack.view.check_for_winner(ctx):
            await message.edit(embed=blackjack.build_embed(blackjack.active_hand), view=blackjack.view)

    @commands.command(
        name='work',
        description='Work for money.',
        guild_only=True
    )
    @commands.cooldown(1, Payouts.WORK_COODLWON.value, commands.BucketType.member)
    async def work(self, ctx: Context):
        """Work for money.

        Fail Rate: `0%`
        Minimum Payout: `20`
        Maximum Payout: `250`
        Cooldown: `2 hours`
        """
        balance = await self.economy.get_balance(ctx.author.id, ctx.guild.id)
        amount = round(random.randint(Payouts.WORK_PAYOUT_MIN.value, Payouts.WORK_PAYOUT_MAX.value))
        await balance.add(amount, 'cash')
        await ctx.stick(True, random.choice(WORKING_RESPONSES).format(coins=f'{cash_emoji} **{amount:,}**'))

    @commands.command(
        name='crime',
        description='Commit a crime for money. Higher risk, higher reward.',
        guild_only=True
    )
    @commands.cooldown(1, Payouts.CRIME_COOLDOWN.value, commands.BucketType.member)
    async def crime(self, ctx: Context):
        """Commit a crime for money.

        Fail Rate: `60%`
        Minimum Payout: `250`
        Maximum Payout: `700`
        Cooldown: `1 day`
        """
        balance = await self.economy.get_balance(ctx.author.id, ctx.guild.id)
        amount = round(random.randint(Payouts.CRIME_PAYOUT_MIN.value, Payouts.CRIME_PAYOUT_MAX.value))

        rate = random.uniform(0, 1)
        if rate > Payouts.CRIME_FAIL_RATE.value:
            await balance.add(amount, 'cash')
            await ctx.stick(True, random.choice(SUCCESSFULL_CRIME_RESPONSES).format(coins=f'{cash_emoji} **{amount:,}**'))
        else:
            amount = round(random.uniform(Payouts.CRIME_FINE_MIN.value, Payouts.CRIME_FINE_MAX.value) * amount)
            await balance.remove(amount, 'cash')
            await ctx.stick(False, random.choice(FAILED_CRIME_RESPONSES).format(coins=f'{cash_emoji} **{amount:,}**'))

    @commands.command(
        name='slut',
        description='Whip it out, for a bit of cash. ;) (NSFW)',
        nsfw=True,
        guild_only=True
    )
    @commands.cooldown(1, Payouts.SLUT_COODLWON.value, commands.BucketType.member)
    async def slut(self, ctx: Context):
        """Do some naughty work for cash. (NSFW)

        Fail Rate: `35%`
        Minimum Payout: `100`
        Maximum Payout: `400`
        Cooldown: `4 hours`
        """
        balance = await self.economy.get_balance(ctx.author.id, ctx.guild.id)
        amount = round(random.randint(Payouts.SLUT_PAYOUT_MIN.value, Payouts.SLUT_PAYOUT_MAX.value))

        rate = random.uniform(0, 1)
        if rate > Payouts.SLUT_FAIL_RATE.value:
            await balance.add(amount, 'cash')
            await ctx.stick(True, random.choice(SUCCESSFULL_SLUT_RESPONSES).format(coins=f'{cash_emoji} **{amount:,}**'))
        else:
            amount = round(random.uniform(Payouts.SLUT_FINE_MIN.value, Payouts.SLUT_FINE_MAX.value) * amount)
            await balance.remove(amount, 'cash')
            await ctx.stick(False, random.choice(FAILED_SLUT_RESPONSES).format(coins=f'{cash_emoji} **{amount:,}**'))

    @commands.command(
        name='rob',
        description='Attempt to rob another user.',
        guild_only=True
    )
    @commands.cooldown(1, Payouts.CRIME_COOLDOWN.value, commands.BucketType.member)
    async def rob(self, ctx: Context, user: Annotated[discord.Member, commands.UserConverter]):
        """Rob another Users cash.

        You can only rob a user's **cash** balance.

        Fail Rate: `your total / (their cash + your total)`
        This has a minimum of 20% and maximum of 80%.

        Possible amount to steal: `success rate * their cash`
        Failing penalty is the same as with command `crime`
        """
        robber_balance = await self.economy.get_balance(ctx.author.id, ctx.guild.id)
        robbed_balance = await self.economy.get_balance(user.id, ctx.guild.id)

        if robbed_balance.cash == 0:
            raise errors.BadArgument('This player has no cash.')

        ROB_FAIL_PROBABILLITY = robber_balance.total / (robbed_balance.cash + robbed_balance.total)
        ROB_FAIL_RATE = max(0.2, min(0.8, ROB_FAIL_PROBABILLITY))
        amount = round((1 - ROB_FAIL_RATE) * robbed_balance.cash)

        rate = random.uniform(0, 1)
        if rate > ROB_FAIL_RATE:
            await robber_balance.add(amount, 'cash')
            await robbed_balance.remove(amount, 'cash')
            await ctx.stick(True, f'You were able to rob {cash_emoji} **{amount:,}** from **{user.display_name}**.')
        else:
            amount = round(random.uniform(Payouts.CRIME_FINE_MIN.value, Payouts.CRIME_FINE_MAX.value) * amount)
            await robber_balance.remove(amount, 'cash')
            await ctx.stick(False, f'You failed to rob **{user.display_name}** and lost {cash_emoji} **{amount:,}**.')

    @commands.command(
        name='roulette',
        description='Play a game of roulette.',
        guild_only=True
    )
    @app_commands.choices(space=[])
    @app_commands.autocomplete(space=roulette_space_autocomplete)
    async def roulette(self, ctx: Context, bet: int, space: Space):
        """Play a game of roulette.

        You can bet on a single space or a range of spaces.
        """
        if bet < 0:
            return await ctx.send('You cannot bet negative coins.', ephemeral=True)

        if bet < MinimumBet.ROULETTE.value:
            return await ctx.send(f'You must bet at least {cash_emoji} **{MinimumBet.ROULETTE.value:,}**.', ephemeral=True)

        balance = await self.economy.get_balance(ctx.author.id, ctx.guild.id)

        if bet > balance.cash:
            return await ctx.send(f'You do not have enough money to bet that amount.\n'
                                  f'You currently have {cash_emoji} **{balance.cash:,}** in **cash**.', ephemeral=True)

        await balance.remove(bet, 'cash')

        if ctx.channel.id in self.roulette_tables:
            roulette = self.roulette_tables[ctx.channel.id]
            roulette.place(_roulette.Bet(ctx.author, space, bet))
            await roulette.message.edit(embed=roulette.build_embed())
        else:
            roulette = _roulette.RouletteTable(ctx, ctx.author)
            roulette.place(_roulette.Bet(ctx.author, space, bet))

            message = await ctx.send(embed=roulette.build_embed(), view=roulette.view)

            roulette.message = message
            self.roulette_tables[ctx.channel.id] = roulette

            # Wait for other bets to be placed before spinning the wheel.
            await asyncio.sleep(60)

            roulette = self.roulette_tables[ctx.channel.id]
            roulette.close()

            # Note this is just for aesthetics
            await message.edit(
                embed=roulette.build_embed(image_url='https://i.giphy.com/26uf2YTgF5upXUTm0.gif'), view=roulette.view)
            await asyncio.sleep(5)

            result = random.randint(0, 36)
            # Get all bets that are on the winning space.
            winning_spaces = list(roulette.get_winning_spaces(result))
            winning_bets = [bet for _space in winning_spaces for bet in roulette.spaces[_space] if _space == bet.space]

            if winning_bets:
                # Calculate the payout for each bet.
                for bet in winning_bets:
                    payout = round(bet.amount * bet.space.payout)
                    await balance.add(payout, 'cash')

            await message.edit(embed=roulette.build_embed(winning_spaces, result=result), view=None)

            self.roulette_tables.pop(ctx.channel.id)
        await ctx.message.add_reaction(ctx.tick(True))


async def setup(bot: Percy):
    await bot.add_cog(Games(bot))
