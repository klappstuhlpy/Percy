from __future__ import annotations

import asyncio
import datetime
import random
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands
from expiringdict import ExpiringDict

from app.cogs.games import _blackjack, _minesweeper, _poker, _roulette, _short_games, _slot, _tictactoe
from app.cogs.games._classes import MinimumBet, Payouts
from app.cogs.games._roulette import Payout, Space
from app.core import Bot, Cog, Flags, flag
from app.core.models import Context, command, cooldown, describe
from app.utils import (
    FAILED_CRIME_RESPONSES,
    FAILED_SLUT_RESPONSES,
    SUCCESSFULL_CRIME_RESPONSES,
    SUCCESSFULL_SLUT_RESPONSES,
    WORKING_RESPONSES,
    fuzzy,
    helpers,
    txt, fnumb,
)
from config import Emojis, path

if TYPE_CHECKING:
    from app.core.timer import Timer


async def roulette_space_autocomplete(_, current: str) -> list[app_commands.Choice[int]]:
    results = fuzzy.finder(current, list(Space), key=lambda p: p.value)
    return [
        app_commands.Choice(name=space.value, value=space.value) for space in results[:20]
    ]


class HangmanFlags(Flags):
    min_length: int = flag(description='The minimum length the word should have.', short='min', default=0)
    max_length: int = flag(description='The maximum length the word should have.', short='max', default=25)
    min_unique_letters: int = flag(description='The minimum amount of unique letters the word should have.',
                                   aliases=['umin', 'uniquemin'], default=0)
    max_unique_letters: int = flag(description='The maximum amount of unique letters the word should have.',
                                   aliases=['umax', 'uniquemax'], default=25)


class Games(Cog):
    """Play games against the bot or other players to earn money."""

    emoji = '\N{VIDEO GAME}'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

        self.blackjack_tables: dict[int, _blackjack.Blackjack] = ExpiringDict(max_len=1000, max_age_seconds=21600)
        self.roulette_tables: dict[int, _roulette.Table] = {}
        self.poker_tables: dict[int, _poker.TexasHoldem] = {}

    @command(
        'tictactoe',
        description='Play a TicTacToe party with another user.',
        aliases=['ttt'],
        guild_only=True,
        hybrid=True
    )
    @app_commands.rename(other='with')
    @describe(other='The opponent to play with')
    async def tictactoe(self, ctx: Context, *, other: discord.Member) -> None:
        """Play a TicTacToe party with another user."""
        if other.bot:
            await ctx.send_error('You cannot play against a bot')
            return

        # noinspection PyProtectedMember
        prompt = _tictactoe.Prompt(ctx.author._user, other._user)
        embed = discord.Embed(
            title='TicTacToe',
            description=f'{other.mention} has been challenged to a TicTacToe party by {ctx.author.mention}.\n'
                        f'Do you accept this party, {other.mention}?',
            colour=helpers.Colour.white(),
        )
        msg = await ctx.send(embed=embed, view=prompt)

        await prompt.wait()
        await ctx.maybe_delete(msg, delay=1)

    @command(
        'minesweeper',
        alias='ms',
        description='Play a Minesweeper game.',
        guild_only=True,
        hybrid=True
    )
    @describe(mines='The amount of mines to play with')
    async def minesweeper(self, ctx: Context, *, mines: commands.Range[int, 3, 24] = 3) -> None:
        """Play a Minesweeper Game."""
        if mines < 3 or mines > 24:
            await ctx.send_error('The amount of mines must be greater than equal to 3 and less than 25.')
            return

        ms = _minesweeper.Minesweeper(ctx, mines=mines)
        await ctx.send(embed=ms.build_embed(), view=ms)

    @command(
        'hangman',
        description='Play a Hangman game.',
        guild_only=True,
        hybrid=True
    )
    async def hangman(self, ctx: Context, *, flags: HangmanFlags) -> None:
        """Play hangman with the bot."""
        word_list = txt(Path(path, 'assets/hangman_words.txt')).splitlines()
        if not word_list:
            await ctx.send_error('No words found. :/')
            return

        filtered_words = [
            word for word in word_list
            if flags.min_length < len(word) < flags.max_length and flags.min_unique_letters < len(
                set(word)) < flags.max_unique_letters
        ]

        if not filtered_words:
            await ctx.send_error('No words found with the given criteria.')
            return

        word = random.choice(filtered_words)
        hangman = _short_games.Hangman(ctx.author, word)

        origin = await ctx.send(embed=hangman.build_embed())

        def check(msg: discord.Message) -> bool:
            return msg.author == ctx.author and msg.channel == ctx.channel

        while not hangman.finished:
            try:
                message = await self.bot.wait_for(
                    "message",
                    timeout=60.0,
                    check=check
                )
            except TimeoutError:
                await origin.edit(
                    content=f'{Emojis.error} Time\'s up! The game has been aborted. You must send a letter within 60 seconds.',
                    embed=hangman.build_embed(False)
                )
                return

            content = message.content.strip().lower()

            await ctx.maybe_delete(message)

            if content == 'abort':
                await origin.edit(embed=hangman.build_embed(False))
                return

            if (len(content) != 1 and content != word) or not content.isalpha():
                continue

            if content == word:
                hangman.finished = True
                await origin.edit(embed=hangman.build_embed(True))
                return

            if content in hangman.used:
                hangman.tries -= 1
                hangman._last_input = f'`❌ "{content}" has already been used.`'
                await origin.edit(embed=hangman.build_embed())
                continue

            hangman.used.add(content)

            if content in hangman.letters:
                if hangman.letters.issubset(hangman.used):
                    hangman.finished = True
                    await origin.edit(embed=hangman.build_embed(True))
                    return
                else:
                    hangman._last_input = f'`✅ "{content}" is correct.`'

                await origin.edit(embed=hangman.build_embed())
                continue

            if content not in hangman.letters:
                hangman.tries -= 1
                if hangman.tries == 0:
                    hangman.finished = True
                    await origin.edit(embed=hangman.build_embed(False))
                    return
                else:
                    hangman._last_input = f'`❌ "{content}" is wrong.`'

                await origin.edit(embed=hangman.build_embed())
                continue

    @command(
        'tower',
        description='Play a round of Tower game.',
        guild_only=True,
        hybrid=True
    )
    @describe(bet='The amount of coins to bet.')
    async def tower(self, ctx: Context, bet: int = 100) -> None:
        """Play a round of Tower game."""
        if bet < 0:
            await ctx.send_error('You cannot bet negative coins.')
            return

        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)

        if bet > balance.cash:
            await ctx.send_error(
                f'You do not have enough money to bet that amount.\n'
                f'You currently have {Emojis.Economy.cash} **{fnumb(balance.cash)}** in **cash**.')
            return

        await balance.remove(cash=bet)

        tower = _short_games.Tower(ctx.author, bet)
        await ctx.send(embed=tower.build_embed(), view=tower)

    @command(
        'slots',
        description='Play a game of slots.',
        alias='slot',
        guild_only=True,
        hybrid=True
    )
    @describe(bet='The amount of coins to bet.')
    async def slots(self, ctx: Context, bet: int) -> None:
        """Play a game of slots."""
        if bet < 0:
            await ctx.send_error('You cannot bet negative coins.')
            return

        if bet < MinimumBet.BLACKJACK.value:
            await ctx.send_error(
                f'You must bet at least {Emojis.Economy.cash} **{fnumb(MinimumBet.BLACKJACK.value)}**.')
            return

        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)

        if bet > balance.cash:
            await ctx.send_error(
                f'You do not have enough money to bet that amount.\n'
                f'You currently have {Emojis.Economy.cash} **{fnumb(balance.cash)}** in **cash**.')
            return

        await balance.remove(cash=bet)

        slots = _slot.SlotMachine(ctx.author, bet)
        await ctx.send(embed=slots.build_embed(), view=slots)

    @command(
        'blackjack',
        description='Play a Blackjack game.',
        alias='bj',
        guild_only=True,
        hybrid=True
    )
    @describe(bet='The amount of coins to bet.')
    async def blackjack(self, ctx: Context, bet: int) -> None:
        if bet < 0:
            await ctx.send_error('You cannot bet negative coins.')
            return

        if bet < MinimumBet.BLACKJACK.value:
            await ctx.send_error(f'You must bet at least {Emojis.Economy.cash} **{fnumb(MinimumBet.BLACKJACK.value)}**.')
            return

        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)

        if bet > balance.cash:
            await ctx.send_error(
                f'You do not have enough money to bet that amount.\n'
                f'You currently have {Emojis.Economy.cash} **{fnumb(balance.cash)}** in **cash**.')
            return

        await balance.remove(cash=bet)

        if ctx.author.id in self.blackjack_tables:
            blackjack = self.blackjack_tables[ctx.author.id]
            if blackjack.is_running:
                await ctx.send_error('You are already playing a game of Blackjack.')
                return

            blackjack = blackjack.wake_up(ctx, bet)
        else:
            blackjack = _blackjack.Blackjack(ctx, bet=bet, decks=3)
            self.blackjack_tables[ctx.author.id] = blackjack

        # Shuffle cards, just for aesthetics
        embed = blackjack.build_embed(
            hand=blackjack.active_hand,
            image_url='https://klappstuhl.me/gallery/ZvGkGVKtXx.gif',
            colour=discord.Colour.light_grey(),
            text='*Shuffling Cards...*'
        )
        message = await ctx.send(embed=embed)
        blackjack.active_hand.message = message

        await asyncio.sleep(3)

        await blackjack.view.update_buttons(active=True)
        if not await blackjack.view.check_for_winner(ctx):
            await message.edit(embed=blackjack.build_embed(blackjack.active_hand), view=blackjack.view)

    @command(
        'work',
        description='Work for money.',
        guild_only=True,
        hybrid=True
    )
    @cooldown(1, Payouts.WORK_COODLWON.value, commands.BucketType.member)
    async def work(self, ctx: Context) -> None:
        """Work for money.

        Fail Rate: `0%`
        Minimum Payout: `20`
        Maximum Payout: `250`
        Cooldown: `2 hours`
        """
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        amount = round(random.randint(Payouts.WORK_PAYOUT_MIN.value, Payouts.WORK_PAYOUT_MAX.value))
        await balance.add(cash=amount)
        await ctx.send_success(random.choice(WORKING_RESPONSES).format(coins=f'{Emojis.Economy.cash} **{fnumb(amount)}**'))

    @command(
        'crime',
        description='Commit a crime for money. Higher risk, higher reward.',
        guild_only=True,
        hybrid=True
    )
    @cooldown(1, Payouts.CRIME_COOLDOWN.value, commands.BucketType.member)
    async def crime(self, ctx: Context) -> None:
        """Commit a crime for money.

        Fail Rate: `60%`
        Minimum Payout: `250`
        Maximum Payout: `700`
        Cooldown: `1 day`
        """
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        amount = round(random.randint(Payouts.CRIME_PAYOUT_MIN.value, Payouts.CRIME_PAYOUT_MAX.value))

        rate = random.uniform(0, 1)
        if rate > Payouts.CRIME_FAIL_RATE.value:
            await balance.add(cash=amount)
            await ctx.send_success(
                random.choice(SUCCESSFULL_CRIME_RESPONSES).format(coins=f'{Emojis.Economy.cash} **{fnumb(amount)}**'))
        else:
            amount = round(random.uniform(Payouts.CRIME_FINE_MIN.value, Payouts.CRIME_FINE_MAX.value) * amount)
            await balance.remove(cash=amount)
            await ctx.send_error(
                random.choice(FAILED_CRIME_RESPONSES).format(coins=f'{Emojis.Economy.cash} **{fnumb(amount)}**'))

    @command(
        'slut',
        description='Whip it out, for a bit of cash. ;) (NSFW)',
        nsfw=True,
        guild_only=True,
        hybrid=True
    )
    @cooldown(1, Payouts.SLUT_COODLWON.value, commands.BucketType.member)
    async def slut(self, ctx: Context) -> None:
        """Do some naughty work for cash. (NSFW)

        Fail Rate: `35%`
        Minimum Payout: `100`
        Maximum Payout: `400`
        Cooldown: `4 hours`
        """
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        amount = round(random.randint(Payouts.SLUT_PAYOUT_MIN.value, Payouts.SLUT_PAYOUT_MAX.value))

        rate = random.uniform(0, 1)
        if rate > Payouts.SLUT_FAIL_RATE.value:
            await balance.add(cash=amount)
            await ctx.send_success(
                random.choice(SUCCESSFULL_SLUT_RESPONSES).format(coins=f'{Emojis.Economy.cash} **{fnumb(amount)}**'))
        else:
            amount = round(random.uniform(Payouts.SLUT_FINE_MIN.value, Payouts.SLUT_FINE_MAX.value) * amount)
            await balance.remove(cash=amount)
            await ctx.send_error(
                random.choice(FAILED_SLUT_RESPONSES).format(coins=f'{Emojis.Economy.cash} **{fnumb(amount)}**'))

    @command(
        'rob',
        description='Attempt to rob another user.',
        guild_only=True,
        hybrid=True
    )
    @cooldown(1, Payouts.CRIME_COOLDOWN.value, commands.BucketType.member)
    @describe(user='The user you want to rob.')
    async def rob(self, ctx: Context, user: discord.Member) -> None:
        """Rob another Users cash.

        You can only rob a user's **cash** balance.

        Fail Rate: `your total / (their cash + your total)`
        This has a minimum of 20% and a maximum of 80%.

        Possible amount to steal: `success rate * their cash`
        Failing penalty is the same as with command `crime`
        """
        if user.bot:
            ctx.command.reset_cooldown(ctx)
            await ctx.send_error('Cannot rob a bot.')
            return

        if user == ctx.author:
            ctx.command.reset_cooldown(ctx)
            await ctx.send_error('You cannot rob yourself.')
            return

        robber_balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        robbed_balance = await ctx.db.get_user_balance(user.id, ctx.guild.id)

        if robbed_balance.cash == 0:
            ctx.command.reset_cooldown(ctx)
            await ctx.send_error(f'**{user.display_name}** has no cash to rob.')
            return

        ROB_FAIL_PROBABILLITY = robber_balance.total / (robbed_balance.cash + robbed_balance.total)
        ROB_FAIL_RATE = max(0.2, min(0.8, ROB_FAIL_PROBABILLITY))
        amount = round((1 - ROB_FAIL_RATE) * robbed_balance.cash)

        rate = random.uniform(0, 1)
        if rate > ROB_FAIL_RATE:
            await robber_balance.add(cash=amount)
            await robbed_balance.remove(cash=amount)
            await ctx.send_success(
                f'You were able to rob {Emojis.Economy.cash} **{fnumb(amount)}** from **{user.display_name}**.')
        else:
            amount = round(random.uniform(Payouts.CRIME_FINE_MIN.value, Payouts.CRIME_FINE_MAX.value) * amount)
            await robber_balance.remove(cash=amount)
            await ctx.send_error(
                f'You failed to rob **{user.display_name}** and lost {Emojis.Economy.cash} **{fnumb(amount)}**.')

    @command(
        'roulette',
        description='Play a game of roulette.',
        guild_only=True,
        hybrid=True
    )
    @app_commands.choices(space=[])  # Do this because of the "Space" Class Enum
    @app_commands.autocomplete(space=roulette_space_autocomplete)
    @describe(
        bet='The amount of coins to bet.',
        space='The space to bet on.'
    )
    async def roulette(self, ctx: Context, bet: int, space: Space) -> None:
        """Play a game of roulette.

        You can bet on a single space or a range of VALID_SPACES.
        """
        if bet < 0:
            await ctx.send_error('You cannot bet negative coins.')
            return

        if bet < MinimumBet.ROULETTE.value:
            await ctx.send_error(
                f'You must bet at least {Emojis.Economy.cash} **{fnumb(MinimumBet.ROULETTE.value)}**.')
            return

        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)

        if bet > balance.cash:
            await ctx.send_error(
                f'You do not have enough money to bet that amount.\n'
                f'You currently have {Emojis.Economy.cash} **{fnumb(balance.cash)}** in **cash**.')
            return

        await balance.remove(cash=bet)

        if ctx.channel.id in self.roulette_tables:
            roulette = self.roulette_tables[ctx.channel.id]

            if not roulette.open:
                await ctx.send_error('**Bets are closed.** *Rien ne va plus*')
                return

            roulette.place(_roulette.Bet(ctx.author, space, bet))
            await ctx.maybe_edit(roulette.message, embed=roulette.build_embed())
        else:
            roulette = _roulette.Table(ctx)
            roulette.place(_roulette.Bet(ctx.author, space, bet))

            message = await ctx.send(embed=roulette.build_embed(), view=roulette.view)

            if not message:
                await ctx.send_error('The roulette game message has not been found.')
                return

            roulette.message = message
            self.roulette_tables[ctx.channel.id] = roulette

            await self.bot.timers.create(
                datetime.timedelta(seconds=60),
                'roulette',
                channel_id=ctx.channel.id,
                message_id=message.id
            )
        await ctx.message.add_reaction(Emojis.success)

    @command(
        'poker',
        description='Play a game of Texas Hold\'em Poker.',
        guild_only=True,
        hybrid=True
    )
    @describe(stack='The amount of coins to play with.')
    async def poker(self, ctx: Context, stack: int) -> None:
        """Play a game of Texas Hold'em Poker."""
        if stack < 0:
            await ctx.send_error('You cannot bet negative coins.')
            return

        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)

        if stack > balance.cash:
            await ctx.send_error(
                f'You do not have enough money to bet that amount.\n'
                f'You currently have {Emojis.Economy.cash} **{fnumb(balance.cash)}** in **cash**.')
            return

        await balance.remove(cash=stack)

        if ctx.channel.id in self.poker_tables:
            poker = self.poker_tables[ctx.channel.id]
            if len(poker.players) == 4:
                await ctx.send_error('The table is full.')
                return

            if poker.state != _poker.TableState.STOPPED:
                await ctx.send_error(
                    'The game is already running.\n'
                    'Please wait for the next round or open a new game in a different channel.')
                return

            if any(player.member == ctx.author for player in poker.players):
                await ctx.send_error('You are already playing.')
                return

            if stack < poker.min_buy_in or stack > poker.max_buy_in:
                await ctx.send_error(
                    f'The buy-in range for this table is {Emojis.Economy.cash} **{fnumb(poker.min_buy_in)}** - **{fnumb(poker.max_buy_in)}**.')
                return

            poker.add_player(ctx.author, stack)
            poker.view.update_buttons()
            await ctx.maybe_edit(poker.message, embed=poker.build_embed(), view=poker.view)
        else:
            poker = _poker.TexasHoldem(self, ctx, first_buy_in=stack)
            poker.add_player(ctx.author, stack)
            poker.view.update_buttons()

            message = await ctx.send(embed=poker.build_embed(), view=poker.view)
            poker.message = message
            self.poker_tables[ctx.channel.id] = poker

    @Cog.listener()
    async def on_roulette_timer_complete(self, timer: Timer) -> None:
        """Handle the completion of a roulette timer."""
        channel_id = timer['channel_id']
        message_id = timer['message_id']

        if channel_id not in self.roulette_tables:
            return

        roulette = self.roulette_tables[channel_id]
        roulette.close()

        if not roulette.message:
            channel = self.bot.get_channel(channel_id)
            try:
                roulette.message = await channel.fetch_message(message_id)
            except discord.HTTPException:
                for bet in roulette.bets:
                    balance = await self.bot.db.get_user_balance(bet.placed_by.id, roulette.ctx.guild.id)
                    await balance.add(cash=bet.amount)
                # give people their money back
                await channel.send(f'{Emojis.warning} The roulette game message has not been found. *Returning bets.*')
                self.roulette_tables.pop(channel_id)
                return

        # Note this is just for aesthetics
        await roulette.message.edit(
            embed=roulette.build_embed(image_url='https://klappstuhl.me/gallery/GlbnUFmzan.gif'),
            view=roulette.view)
        await asyncio.sleep(5)

        result = random.randint(0, 36)
        # Get all bets that are on the winning space.
        winning_spaces = list(roulette.get_winning_spaces(result))
        winning_bets = [bet for bet in roulette.bets if bet.space in winning_spaces]

        if winning_bets:
            # Calculate the payout for each bet.
            for bet in winning_bets:
                balance = await self.bot.db.get_user_balance(bet.placed_by.id, roulette.ctx.guild.id)
                payout = round(bet.amount * Payout.by_space(bet.space))
                await balance.add(cash=payout)

        try:
            await roulette.message.edit(embed=roulette.build_embed(winning_spaces, result=result), view=None)
        except discord.HTTPException:
            return
        finally:
            self.roulette_tables.pop(channel_id)
