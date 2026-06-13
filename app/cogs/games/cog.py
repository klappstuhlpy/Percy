from __future__ import annotations

import asyncio
import datetime
import json
import random
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, cast

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands
from expiringdict import ExpiringDict

from app.cogs.games import (
    blackjack_bridge,
    hangman_ui,
    higherlower_ui,
    horserace_ui,
    mines_ui,
    minesweeper_ui,
    poker_bridge,
    roulette_ui,
    russianroulette_ui,
    slot_ui,
    tictactoe_ui,
    tower_ui,
    trivia_ui,
    wordle_ui,
)
from app.cogs.games.engine.cards import MinimumBet, Payouts
from app.cogs.games.engine import dice as dice_engine
from app.cogs.games.engine import horserace as horserace_engine
from app.cogs.games.engine import roulette as roulette_engine
from app.cogs.games.engine.trivia import RawQuestion, build_round
from app.cogs.games.engine.wordle import WORD_LENGTH, daily_index
from app.cogs.games.models import Game, GameResult
from app.cogs.games.roulette_ui import Payout, Space
from app.core import Bot, Cog, Flags, flag
from app.core.models import Context, command, cooldown, describe
from app.utils import (
    FAILED_CRIME_RESPONSES,
    FAILED_SLUT_RESPONSES,
    SUCCESSFULL_CRIME_RESPONSES,
    SUCCESSFULL_SLUT_RESPONSES,
    WORKING_RESPONSES,
    fnumb,
    fuzzy,
    helpers,
    txt,
)
from config import Emojis, path

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.core.timer import Timer
    from app.database.base import Balance


async def roulette_space_autocomplete(_, current: str) -> list[Choice[str | int | float]]:
    results = fuzzy.finder(current, list(Space), key=lambda p: str(p.value))
    return [app_commands.Choice(name=str(space.value), value=str(space.value)) for space in results[:20]]


class HangmanFlags(Flags):
    min_length: int = flag(description="The minimum length the word should have.", short="min", default=0)
    max_length: int = flag(description="The maximum length the word should have.", short="max", default=25)
    min_unique_letters: int = flag(
        description="The minimum amount of unique letters the word should have.", aliases=["umin", "uniquemin"], default=0
    )
    max_unique_letters: int = flag(
        description="The maximum amount of unique letters the word should have.", aliases=["umax", "uniquemax"], default=25
    )


class Games(Cog):
    """Play games against the bot or other players to earn money."""

    emoji = "\N{VIDEO GAME}"

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

        self.blackjack_tables: dict[int, blackjack_bridge.Blackjack] = ExpiringDict(max_len=1000, max_age_seconds=21600)
        self.roulette_tables: dict[int, roulette_ui.Table] = {}
        self.poker_tables: dict[int, poker_bridge.PokerSession] = {}
        self.russian_tables: dict[int, russianroulette_ui.RussianRoulette] = {}
        self.horse_tables: dict[int, horserace_ui.Table] = {}

        # (guild_id, user_id, date.toordinal()) of members who already played today's Wordle.
        self.wordle_played: set[tuple[int, int, int]] = set()
        self._trivia_questions: list[RawQuestion] | None = None
        self._wordle_words: list[str] | None = None

    def _load_trivia(self) -> list[RawQuestion]:
        """Lazily loads and caches the bundled trivia question bank."""
        if self._trivia_questions is None:
            self._trivia_questions = json.loads(txt(Path(path, "assets/trivia_questions.json")))
        return self._trivia_questions

    def _load_wordle_words(self) -> list[str]:
        """Lazily loads and caches the bundled Wordle word list (5-letter words)."""
        if self._wordle_words is None:
            self._wordle_words = [
                word.strip().lower()
                for word in txt(Path(path, "assets/wordle_words.txt")).splitlines()
                if len(word.strip()) == WORD_LENGTH and word.strip().isalpha()
            ]
        return self._wordle_words

    async def _take_bet(self, ctx: Context, bet: int, *, minimum: int = 1) -> bool:
        """Validates a positive, affordable bet and debits it. Returns success.

        On failure it sends the appropriate error and returns ``False`` so the caller
        can simply ``return``. The stake is removed from cash up front, mirroring the
        other betting games (winnings are credited back by the game's own flow).
        """
        assert ctx.guild is not None
        if bet < minimum:
            await ctx.send_error(f"You must bet at least {Emojis.Economy.cash} **{fnumb(minimum)}**.")
            return False
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        assert balance is not None
        if bet > balance.cash:
            await ctx.send_error(
                f"You do not have enough money to bet that amount.\n"
                f"You currently have {Emojis.Economy.cash} **{fnumb(balance.cash)}** in **cash**."
            )
            return False
        await balance.remove(cash=bet)
        return True

    @command(
        "tictactoe", description="Play a TicTacToe party with another user.", aliases=["ttt"], guild_only=True, hybrid=True
    )
    @app_commands.rename(other="with")
    @describe(other="The opponent to play with")
    async def tictactoe(self, ctx: Context, *, other: discord.Member) -> None:
        """Play a TicTacToe party with another user."""
        if other.bot:
            await ctx.send_error("You cannot play against a bot")
            return

        prompt = tictactoe_ui.Prompt(ctx.author, other)
        embed = discord.Embed(
            title="TicTacToe",
            description=f"{other.mention} has been challenged to a TicTacToe party by {ctx.author.mention}.\n"
            f"Do you accept this party, {other.mention}?",
            colour=helpers.Colour.white(),
        )
        msg = await ctx.send(embed=embed, view=prompt)

        await prompt.wait()
        await ctx.maybe_delete(msg, delay=1)

    @command("minesweeper", alias="ms", description="Play a Minesweeper game.", guild_only=True, hybrid=True)
    @describe(mines="The amount of mines to play with")
    async def minesweeper(self, ctx: Context, *, mines: commands.Range[int, 3, 24] = 3) -> None:
        """Play a Minesweeper Game."""
        if mines < 3 or mines > 24:
            await ctx.send_error("The amount of mines must be greater than equal to 3 and less than 25.")
            return

        ms = minesweeper_ui.Minesweeper(ctx, mines=mines)
        await ctx.send(view=ms)

    @command("hangman", description="Play a Hangman game.", guild_only=True, hybrid=True)
    async def hangman(self, ctx: Context, *, flags: HangmanFlags) -> None:
        """Play hangman with the bot."""
        word_list = txt(Path(path, "assets/hangman_words.txt")).splitlines()
        if not word_list:
            await ctx.send_error("No words found. :/")
            return

        filtered_words = [
            word
            for word in word_list
            if flags.min_length < len(word) < flags.max_length
            and flags.min_unique_letters < len(set(word)) < flags.max_unique_letters
        ]

        if not filtered_words:
            await ctx.send_error("No words found with the given criteria.")
            return

        word = random.choice(filtered_words)
        hangman = hangman_ui.Hangman(cast("discord.Member", ctx.author), word)

        async def record(result: GameResult) -> None:
            if ctx.guild is not None:
                await self.bot.db.game_stats.record_result(ctx.guild.id, ctx.author.id, Game.HANGMAN, result)

        origin = await ctx.send(view=hangman.render())

        def check(msg: discord.Message) -> bool:
            return msg.author == ctx.author and msg.channel == ctx.channel

        while not hangman.finished:
            try:
                message = await self.bot.wait_for("message", timeout=60.0, check=check)
            except TimeoutError:
                await ctx.send(f"{Emojis.error} Time's up! The game has been aborted. You must send a letter within **60** seconds. :/", reference=origin)
                await origin.edit(
                    view=hangman.render(False),
                )
                return

            content = message.content.strip().lower()

            await ctx.maybe_delete(message)

            if content == "abort":
                await origin.edit(view=hangman.render(False))
                return

            if (len(content) != 1 and content != word) or not content.isalpha():
                continue

            if content == word:
                hangman.finished = True
                await origin.edit(view=hangman.render(True))
                await record(GameResult.WIN)
                return

            if content in hangman.used:
                hangman.tries -= 1
                hangman._last_input = f'`❌ "{content}" has already been used.`'
                await origin.edit(view=hangman.render())
                continue

            hangman.used.add(content)

            if content in hangman.letters:
                if hangman.letters.issubset(hangman.used):
                    hangman.finished = True
                    await origin.edit(view=hangman.render(True))
                    await record(GameResult.WIN)
                    return
                else:
                    hangman._last_input = f'`✅ "{content}" is correct.`'

                await origin.edit(view=hangman.render())
                continue

            if content not in hangman.letters:
                hangman.tries -= 1
                if hangman.tries == 0:
                    hangman.finished = True
                    await origin.edit(view=hangman.render(False))
                    await record(GameResult.LOSS)
                    return
                else:
                    hangman._last_input = f'`❌ "{content}" is wrong.`'

                await origin.edit(view=hangman.render())
                continue

    @command("tower", description="Play a round of Tower game.", guild_only=True, hybrid=True)
    @describe(bet="The amount of coins to bet.")
    async def tower(self, ctx: Context, bet: int = 100) -> None:
        """Play a round of Tower game."""
        if bet < 0:
            await ctx.send_error("You cannot bet negative coins.")
            return

        assert ctx.guild is not None
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        assert balance is not None

        if bet > balance.cash:
            await ctx.send_error(
                f"You do not have enough money to bet that amount.\n"
                f"You currently have {Emojis.Economy.cash} **{fnumb(balance.cash)}** in **cash**."
            )
            return

        await balance.remove(cash=bet)

        tower = tower_ui.Tower(cast("discord.Member", ctx.author), bet)
        await ctx.send(view=tower)

    @command("slots", description="Play a game of slots.", alias="slot", guild_only=True, hybrid=True)
    @describe(bet="The amount of coins to bet.")
    async def slots(self, ctx: Context, bet: int) -> None:
        """Play a game of slots."""
        if bet < 0:
            await ctx.send_error("You cannot bet negative coins.")
            return

        if bet < MinimumBet.BLACKJACK.value:
            await ctx.send_error(f"You must bet at least {Emojis.Economy.cash} **{fnumb(MinimumBet.BLACKJACK.value)}**.")
            return

        assert ctx.guild is not None
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        assert balance is not None

        if bet > balance.cash:
            await ctx.send_error(
                f"You do not have enough money to bet that amount.\n"
                f"You currently have {Emojis.Economy.cash} **{fnumb(balance.cash)}** in **cash**."
            )
            return

        await balance.remove(cash=bet)

        slots = slot_ui.SlotMachine(cast("discord.Member", ctx.author), bet)
        await ctx.send(view=slots)

    @command("blackjack", description="Play a Blackjack game.", alias="bj", guild_only=True, hybrid=True)
    @describe(bet="The amount of coins to bet.")
    async def blackjack(self, ctx: Context, bet: int) -> None:
        if bet < 0:
            await ctx.send_error("You cannot bet negative coins.")
            return

        if bet < MinimumBet.BLACKJACK.value:
            await ctx.send_error(f"You must bet at least {Emojis.Economy.cash} **{fnumb(MinimumBet.BLACKJACK.value)}**.")
            return

        assert ctx.guild is not None
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        assert balance is not None

        if bet > balance.cash:
            await ctx.send_error(
                f"You do not have enough money to bet that amount.\n"
                f"You currently have {Emojis.Economy.cash} **{fnumb(balance.cash)}** in **cash**."
            )
            return

        await balance.remove(cash=bet)

        if ctx.author.id in self.blackjack_tables:
            blackjack = self.blackjack_tables[ctx.author.id]
            if blackjack.is_running:
                await ctx.send_error("You are already playing a game of Blackjack.")
                return

            blackjack = blackjack.wake_up(ctx, bet)
        else:
            blackjack = blackjack_bridge.Blackjack(ctx, bet=bet, decks=3)
            self.blackjack_tables[ctx.author.id] = blackjack

        # Shuffle cards, just for aesthetics
        message = await ctx.send(
            view=blackjack.view.render(
                colour=discord.Colour.light_grey(),
                text="*Shuffling Cards...*",
                image_url="https://klappstuhl.me/gallery/raw/TpjOl.gif",
                with_buttons=False,
            )
        )
        blackjack.message = message

        await asyncio.sleep(3)

        await blackjack.view.update_buttons(active=True)
        if not await blackjack.view.check_for_winner(ctx):
            await message.edit(view=blackjack.view.render())

    @command("work", description="Work for money.", guild_only=True, hybrid=True)
    @cooldown(1, Payouts.WORK_COODLWON.value, commands.BucketType.member)
    async def work(self, ctx: Context) -> None:
        """Work for money.

        Fail Rate: `0%`
        Minimum Payout: `20`
        Maximum Payout: `250`
        Cooldown: `2 hours`
        """
        assert ctx.guild is not None
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        assert balance is not None
        amount = round(random.randint(Payouts.WORK_PAYOUT_MIN.value, Payouts.WORK_PAYOUT_MAX.value))
        await balance.add(cash=amount)
        await ctx.send_success(random.choice(WORKING_RESPONSES).format(coins=f"{Emojis.Economy.cash} **{fnumb(amount)}**"))

    @command("crime", description="Commit a crime for money. Higher risk, higher reward.", guild_only=True, hybrid=True)
    @cooldown(1, Payouts.CRIME_COOLDOWN.value, commands.BucketType.member)
    async def crime(self, ctx: Context) -> None:
        """Commit a crime for money.

        Fail Rate: `60%`
        Minimum Payout: `250`
        Maximum Payout: `700`
        Cooldown: `1 day`
        """
        assert ctx.guild is not None
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        assert balance is not None
        amount = round(random.randint(Payouts.CRIME_PAYOUT_MIN.value, Payouts.CRIME_PAYOUT_MAX.value))

        rate = random.uniform(0, 1)
        if rate > Payouts.CRIME_FAIL_RATE.value:
            await balance.add(cash=amount)
            await ctx.send_success(
                random.choice(SUCCESSFULL_CRIME_RESPONSES).format(coins=f"{Emojis.Economy.cash} **{fnumb(amount)}**")
            )
        else:
            amount = round(random.uniform(Payouts.CRIME_FINE_MIN.value, Payouts.CRIME_FINE_MAX.value) * amount)
            await balance.remove(cash=amount)
            await ctx.send_error(
                random.choice(FAILED_CRIME_RESPONSES).format(coins=f"{Emojis.Economy.cash} **{fnumb(amount)}**")
            )

    @command("slut", description="Whip it out, for a bit of cash. ;) (NSFW)", nsfw=True, guild_only=True, hybrid=True)
    @cooldown(1, Payouts.SLUT_COODLWON.value, commands.BucketType.member)
    async def slut(self, ctx: Context) -> None:
        """Do some naughty work for cash. (NSFW)

        Fail Rate: `35%`
        Minimum Payout: `100`
        Maximum Payout: `400`
        Cooldown: `4 hours`
        """
        assert ctx.guild is not None
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        assert balance is not None
        amount = round(random.randint(Payouts.SLUT_PAYOUT_MIN.value, Payouts.SLUT_PAYOUT_MAX.value))

        rate = random.uniform(0, 1)
        if rate > Payouts.SLUT_FAIL_RATE.value:
            await balance.add(cash=amount)
            await ctx.send_success(
                random.choice(SUCCESSFULL_SLUT_RESPONSES).format(coins=f"{Emojis.Economy.cash} **{fnumb(amount)}**")
            )
        else:
            amount = round(random.uniform(Payouts.SLUT_FINE_MIN.value, Payouts.SLUT_FINE_MAX.value) * amount)
            await balance.remove(cash=amount)
            await ctx.send_error(
                random.choice(FAILED_SLUT_RESPONSES).format(coins=f"{Emojis.Economy.cash} **{fnumb(amount)}**")
            )

    @command("rob", description="Attempt to rob another user.", guild_only=True, hybrid=True)
    @cooldown(1, Payouts.CRIME_COOLDOWN.value, commands.BucketType.member)
    @describe(user="The user you want to rob.")
    async def rob(self, ctx: Context, user: discord.Member) -> None:
        """Rob another Users cash.

        You can only rob a user's **cash** balance.

        Fail Rate: `your total / (their cash + your total)`
        This has a minimum of 20% and a maximum of 80%.

        Possible amount to steal: `success rate * their cash`
        Failing penalty is the same as with command `crime`
        """
        if user.bot:
            if ctx.command is not None:
                ctx.command.reset_cooldown(ctx)
            await ctx.send_error("Cannot rob a bot.")
            return

        if user == ctx.author:
            if ctx.command is not None:
                ctx.command.reset_cooldown(ctx)
            await ctx.send_error("You cannot rob yourself.")
            return

        assert ctx.guild is not None
        robber_balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        robbed_balance = await ctx.db.get_user_balance(user.id, ctx.guild.id)
        assert robber_balance is not None
        assert robbed_balance is not None

        if robbed_balance.cash == 0:
            if ctx.command is not None:
                ctx.command.reset_cooldown(ctx)
            await ctx.send_error(f"**{user.display_name}** has no cash to rob.")
            return

        ROB_FAIL_PROBABILLITY = robber_balance.total / (robbed_balance.cash + robbed_balance.total)
        ROB_FAIL_RATE = max(0.2, min(0.8, ROB_FAIL_PROBABILLITY))
        amount = round((1 - ROB_FAIL_RATE) * robbed_balance.cash)

        rate = random.uniform(0, 1)
        if rate > ROB_FAIL_RATE:
            await robber_balance.add(cash=amount)
            await robbed_balance.remove(cash=amount)
            await ctx.send_success(
                f"You were able to rob {Emojis.Economy.cash} **{fnumb(amount)}** from **{user.display_name}**."
            )
        else:
            amount = round(random.uniform(Payouts.CRIME_FINE_MIN.value, Payouts.CRIME_FINE_MAX.value) * amount)
            await robber_balance.remove(cash=amount)
            await ctx.send_error(
                f"You failed to rob **{user.display_name}** and lost {Emojis.Economy.cash} **{fnumb(amount)}**."
            )

    @command("roulette", description="Play a game of roulette.", guild_only=True, hybrid=True)
    @app_commands.choices(space=[])  # Do this because of the "Space" Class Enum
    @app_commands.autocomplete(space=roulette_space_autocomplete)
    @describe(bet="The amount of coins to bet.", space="The space to bet on.")
    async def roulette(self, ctx: Context, bet: int, space: Space) -> None:
        """Play a game of roulette.

        You can bet on a single space or a range of VALID_SPACES.
        """
        if bet < 0:
            await ctx.send_error("You cannot bet negative coins.")
            return

        if bet < MinimumBet.ROULETTE.value:
            await ctx.send_error(f"You must bet at least {Emojis.Economy.cash} **{fnumb(MinimumBet.ROULETTE.value)}**.")
            return

        assert ctx.guild is not None
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        assert balance is not None

        if bet > balance.cash:
            await ctx.send_error(
                f"You do not have enough money to bet that amount.\n"
                f"You currently have {Emojis.Economy.cash} **{fnumb(balance.cash)}** in **cash**."
            )
            return

        await balance.remove(cash=bet)

        if ctx.channel.id in self.roulette_tables:
            roulette = self.roulette_tables[ctx.channel.id]

            if not roulette.open:
                await ctx.send_error("**Bets are closed.** *Rien ne va plus*")
                return

            roulette.place(roulette_ui.Bet(cast("discord.Member", ctx.author), space, bet))
            await ctx.maybe_edit(roulette.message, view=roulette.view.render())
        else:
            roulette = roulette_ui.Table(ctx)
            roulette.place(roulette_ui.Bet(cast("discord.Member", ctx.author), space, bet))

            message = await ctx.send(view=roulette.view.render())

            if not message:
                await ctx.send_error("The roulette game message has not been found.")
                return

            roulette.message = message
            self.roulette_tables[ctx.channel.id] = roulette

            await self.bot.timers.create(
                datetime.timedelta(seconds=60), "roulette", channel_id=ctx.channel.id, message_id=message.id
            )

        with suppress(discord.HTTPException):
            await ctx.message.add_reaction(Emojis.success)

    @command("poker", description="Play a game of Texas Hold'em Poker.", guild_only=True, hybrid=True)
    @describe(stack="The amount of coins to play with.")
    async def poker(self, ctx: Context, stack: int) -> None:
        """Play a game of Texas Hold'em Poker."""
        if stack < 0:
            await ctx.send_error("You cannot bet negative coins.")
            return

        assert ctx.guild is not None
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        assert balance is not None

        if stack > balance.cash:
            await ctx.send_error(
                f"You do not have enough money to bet that amount.\n"
                f"You currently have {Emojis.Economy.cash} **{fnumb(balance.cash)}** in **cash**."
            )
            return

        await balance.remove(cash=stack)

        if ctx.channel.id in self.poker_tables:
            poker = self.poker_tables[ctx.channel.id]
            if len(poker.players) == 4:
                await ctx.send_error("The table is full.")
                return

            if poker.state != poker_bridge.TableState.STOPPED:
                await ctx.send_error(
                    "The game is already running.\nPlease wait for the next round or open a new game in a different channel."
                )
                return

            if any(player.member == ctx.author for player in poker.players):
                await ctx.send_error("You are already playing.")
                return

            if stack < poker.min_buy_in or stack > poker.max_buy_in:
                await ctx.send_error(
                    f"The buy-in range for this table is {Emojis.Economy.cash} **{fnumb(poker.min_buy_in)}** - **{fnumb(poker.max_buy_in)}**."
                )
                return

            poker.add_player(cast("discord.Member", ctx.author), stack)
            await ctx.maybe_edit(poker.message, view=poker.view.render())
        else:
            poker = poker_bridge.PokerSession(self, ctx, first_buy_in=stack)
            poker.add_player(cast("discord.Member", ctx.author), stack)

            message = await ctx.send(view=poker.view.render())
            poker.message = message
            self.poker_tables[ctx.channel.id] = poker

    @command(
        "higherlower", aliases=["hl"], description="Play Higher or Lower for a rising multiplier.",
        guild_only=True, hybrid=True,
    )
    @describe(bet="The amount of coins to bet.")
    async def higherlower(self, ctx: Context, bet: int) -> None:
        """Guess whether the next card is higher or lower; cash out before you bust."""
        if not await self._take_bet(ctx, bet, minimum=10):
            return
        game = higherlower_ui.HigherLowerGame(cast("discord.Member", ctx.author), bet)
        await ctx.send(view=game)

    @command("dice", description="Bet on the total of two dice (2-12).", guild_only=True, hybrid=True)
    @describe(bet="The amount of coins to bet.", target="The total to bet on (2-12).")
    async def dice(self, ctx: Context, bet: int, target: commands.Range[int, 2, 12]) -> None:
        """Roll two dice and bet on their total — rarer totals pay much more."""
        if not await self._take_bet(ctx, bet, minimum=10):
            return
        assert ctx.guild is not None

        message = await ctx.send(f"{Emojis.loading} Rolling the dice...")
        await asyncio.sleep(1.5)

        d1, d2 = dice_engine.roll()
        total = d1 + d2
        faces = f"{dice_engine.DIE_FACES[d1 - 1]} {dice_engine.DIE_FACES[d2 - 1]}"

        if total == target:
            multiplier = dice_engine.payout_multiplier(target)
            payout = round(bet * multiplier)
            balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
            assert balance is not None
            await balance.add(cash=payout)
            embed = discord.Embed(
                title="\N{DIRECT HIT} Dice",
                colour=helpers.Colour.lime_green(),
                description=f"# {faces}\nTotal: **{total}** — you called **{target}**!\n"
                f"Won {Emojis.Economy.cash} **{fnumb(payout)}** (**x{multiplier}**).",
            )
            result, profit = GameResult.WIN, payout - bet
        else:
            embed = discord.Embed(
                title="\N{DIRECT HIT} Dice",
                colour=helpers.Colour.light_red(),
                description=f"# {faces}\nTotal: **{total}** — you called **{target}**.\n"
                f"Lost {Emojis.Economy.cash} **{fnumb(bet)}**.",
            )
            result, profit = GameResult.LOSS, -bet

        await message.edit(content=None, embed=embed)
        await self.bot.db.game_stats.record_result(
            ctx.guild.id, ctx.author.id, Game.DICE, result, wagered=bet, profit=profit
        )

    @command("mines", description="Reveal gems while dodging mines; cash out anytime.", guild_only=True, hybrid=True)
    @describe(bet="The amount of coins to bet.", mines="Number of mines hidden in the grid (1-19).")
    async def mines(self, ctx: Context, bet: int, mines: commands.Range[int, 1, 19] = 3) -> None:
        """Flip gems for a rising multiplier and cash out before you hit a mine."""
        if not await self._take_bet(ctx, bet, minimum=10):
            return
        game = mines_ui.MinesGame(cast("discord.Member", ctx.author), bet, mines)
        await ctx.send(view=game)

    @command("trivia", description="Answer a trivia question — first correct answer wins.", guild_only=True, hybrid=True)
    @cooldown(1, 8.0, commands.BucketType.channel)
    async def trivia(self, ctx: Context) -> None:
        """Post a multiple-choice trivia question; the first member to answer correctly wins coins."""
        raw = random.choice(self._load_trivia())
        view = trivia_ui.TriviaView(build_round(raw))
        message = await ctx.send(view=view)
        view.message = message

    @command("wordle", description="Guess the guild's daily 5-letter word.", guild_only=True, hybrid=True)
    async def wordle(self, ctx: Context) -> None:
        """Play the guild's daily Wordle — guess the 5-letter word in 6 tries (type guesses in chat)."""
        assert ctx.guild is not None
        words = self._load_wordle_words()
        today = datetime.datetime.now(datetime.UTC).date()
        key = (ctx.guild.id, ctx.author.id, today.toordinal())
        if key in self.wordle_played:
            await ctx.send_info("You've already played today's Wordle here. Come back tomorrow!")
            return

        answer = words[daily_index(len(words), ctx.guild.id, today)]
        game = wordle_ui.Wordle(cast("discord.Member", ctx.author), answer)
        origin = await ctx.send(view=game.render())

        def check(msg: discord.Message) -> bool:
            return msg.author == ctx.author and msg.channel == ctx.channel

        while not game.finished:
            try:
                guess_message = await self.bot.wait_for("message", timeout=180.0, check=check)
            except TimeoutError:
                await origin.edit(view=game.render(reveal=True))
                await ctx.send(f"{Emojis.error} Time's up! The word was **{answer.upper()}**.", reference=origin)
                return

            content = guess_message.content.strip().lower()
            await ctx.maybe_delete(guess_message)

            if content == "abort":
                await origin.edit(view=game.render(reveal=True))
                return
            if len(content) != WORD_LENGTH or not content.isalpha():
                await origin.edit(view=game.render(note=f"`\N{CROSS MARK} Guesses must be {WORD_LENGTH} letters.`"))
                continue
            if content not in words:
                await origin.edit(view=game.render(note="`\N{CROSS MARK} Not in the word list.`"))
                continue

            game.add_guess(content)
            await origin.edit(view=game.render())

        self.wordle_played.add(key)
        if game.won:
            reward = max(50, 350 - (game.tries_used - 1) * 50)
            balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
            assert balance is not None
            await balance.add(cash=reward)
            await ctx.send(
                f"{Emojis.success} Solved in **{game.tries_used}**! You won {Emojis.Economy.cash} **{fnumb(reward)}**.",
                reference=origin,
            )
            await self.bot.db.game_stats.record_result(
                ctx.guild.id, ctx.author.id, Game.WORDLE, GameResult.WIN, profit=reward
            )
        else:
            await self.bot.db.game_stats.record_result(ctx.guild.id, ctx.author.id, Game.WORDLE, GameResult.LOSS)

    @command(
        "russianroulette", aliases=["rr"], description="Last player standing wins the pot.",
        guild_only=True, hybrid=True,
    )
    @describe(ante="Coins each player antes into the pot.")
    async def russianroulette(self, ctx: Context, ante: int = 100) -> None:
        """Open a Russian Roulette table; players ante in, take turns, and the last survivor takes the pot."""
        assert ctx.guild is not None
        if ctx.channel.id in self.russian_tables:
            await ctx.send_error("A Russian Roulette game is already running in this channel.")
            return
        if not await self._take_bet(ctx, ante, minimum=10):
            return

        game = russianroulette_ui.RussianRoulette(self, ctx, ante)
        self.russian_tables[ctx.channel.id] = game
        message = await ctx.send(view=game)
        game.message = message

    @command(
        "horserace", aliases=["hr"], description="Bet on a horse; parimutuel payouts after the race.",
        guild_only=True, hybrid=True,
    )
    @describe(bet="The amount of coins to bet.", horse="The horse to back (1-6).")
    async def horserace(self, ctx: Context, bet: int, horse: commands.Range[int, 1, 6]) -> None:
        """Bet on a horse. When the gates close the race runs and the pool is split among winning bets."""
        if not await self._take_bet(ctx, bet, minimum=10):
            return
        assert ctx.guild is not None

        member = cast("discord.Member", ctx.author)
        if ctx.channel.id in self.horse_tables:
            table = self.horse_tables[ctx.channel.id]
            if not table.open:
                balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
                assert balance is not None
                await balance.add(cash=bet)
                await ctx.send_error("The gates are closed for this race. Wait for the next one.")
                return
            table.place(horserace_ui.Bet(member, horse, bet))
            await ctx.maybe_edit(table.message, view=table.view.render())
        else:
            table = horserace_ui.Table(ctx)
            table.place(horserace_ui.Bet(member, horse, bet))
            message = await ctx.send(view=table.view.render())
            table.message = message
            self.horse_tables[ctx.channel.id] = table
            await self.bot.timers.create(
                datetime.timedelta(seconds=horserace_ui.BETTING_SECONDS),
                "horserace",
                channel_id=ctx.channel.id,
                message_id=message.id,
            )

        with suppress(discord.HTTPException):
            await ctx.message.add_reaction(Emojis.success)

    async def _balances_for(self, guild_id: int, user_ids: Iterable[int]) -> dict[int, Balance]:
        """Fetch balances for the given users in a single query, keyed by user id.

        Bettors always have a row (placing a bet debits them), but any missing
        user falls back to a single fetch which inserts an empty record. This
        replaces per-bet ``get_user_balance`` calls in the payout/refund loops.
        """
        balances = {b.user_id: b for b in await self.bot.db.get_guild_balances(guild_id)}
        return {uid: balances.get(uid) or await self.bot.db.get_user_balance(uid, guild_id) for uid in set(user_ids)}

    @Cog.listener()
    async def on_roulette_timer_complete(self, timer: Timer) -> None:
        """Handle the completion of a roulette timer."""
        channel_id = timer["channel_id"]
        message_id = timer["message_id"]

        if channel_id not in self.roulette_tables:
            return

        roulette = self.roulette_tables[channel_id]
        roulette.close()

        if not roulette.message:
            channel = self.bot.get_channel(channel_id)
            assert isinstance(channel, discord.abc.Messageable)
            try:
                roulette.message = await channel.fetch_message(message_id)
            except discord.HTTPException:
                assert roulette.ctx.guild is not None
                balances = await self._balances_for(roulette.ctx.guild.id, [bet.placed_by.id for bet in roulette.bets])
                for bet in roulette.bets:
                    await balances[bet.placed_by.id].add(cash=bet.amount)
                # give people their money back
                await channel.send(f"{Emojis.warning} The roulette game message has not been found. *Returning bets.*")
                self.roulette_tables.pop(channel_id)
                return

        assert roulette.message is not None
        # Note this is just for aesthetics
        await roulette.message.edit(
            view=roulette.view.render(image_url="https://klappstuhl.me/gallery/raw/KdKof.gif")
        )
        await asyncio.sleep(5)

        result = roulette_engine.spin()
        # Get all bets that are on the winning space.
        winning_spaces = list(roulette.get_winning_spaces(result))
        winning_bets = [bet for bet in roulette.bets if bet.space in winning_spaces]

        assert roulette.ctx.guild is not None
        guild_id = roulette.ctx.guild.id
        balances = await self._balances_for(guild_id, [bet.placed_by.id for bet in roulette.bets])
        for bet in roulette.bets:
            if bet in winning_bets:
                payout = round(bet.amount * Payout.by_value(bet.space.value))
                await balances[bet.placed_by.id].add(cash=payout)
                await self.bot.db.game_stats.record_result(
                    guild_id, bet.placed_by.id, Game.ROULETTE, GameResult.WIN, wagered=bet.amount, profit=payout - bet.amount
                )
            else:
                await self.bot.db.game_stats.record_result(
                    guild_id, bet.placed_by.id, Game.ROULETTE, GameResult.LOSS, wagered=bet.amount, profit=-bet.amount
                )

        try:
            await roulette.message.edit(
                view=roulette.view.render(winning_spaces, result=result, with_buttons=False)
            )
        except discord.HTTPException:
            return
        finally:
            self.roulette_tables.pop(channel_id)

    @Cog.listener()
    async def on_horserace_timer_complete(self, timer: Timer) -> None:
        """Run the race and pay out parimutuel winnings when betting closes."""
        channel_id = timer["channel_id"]
        message_id = timer["message_id"]

        table = self.horse_tables.get(channel_id)
        if table is None:
            return
        table.open = False
        assert table.ctx.guild is not None
        guild_id = table.ctx.guild.id

        if table.message is None:
            channel = self.bot.get_channel(channel_id)
            assert isinstance(channel, discord.abc.Messageable)
            try:
                table.message = await channel.fetch_message(message_id)
            except discord.HTTPException:
                balances = await self._balances_for(guild_id, [bet.placed_by.id for bet in table.bets])
                for bet in table.bets:
                    await balances[bet.placed_by.id].add(cash=bet.amount)
                await channel.send(f"{Emojis.warning} The horse race message was lost. *Refunding bets.*")
                self.horse_tables.pop(channel_id, None)
                return

        winner, frames = horserace_engine.simulate_race()

        # Animate a handful of evenly spaced frames to avoid edit rate limits.
        step = max(1, len(frames) // 5)
        for frame in frames[::step]:
            with suppress(discord.HTTPException):
                await table.message.edit(view=table.view.render(frame, racing=True))
            await asyncio.sleep(1.1)

        pool = table.pool
        multiplier = horserace_engine.parimutuel_multiplier(pool, table.total_on(winner + 1))
        balances = await self._balances_for(guild_id, [bet.placed_by.id for bet in table.bets])
        for bet in table.bets:
            if bet.horse == winner + 1:
                payout = round(bet.amount * multiplier)
                await balances[bet.placed_by.id].add(cash=payout)
                await self.bot.db.game_stats.record_result(
                    guild_id, bet.placed_by.id, Game.HORSERACE, GameResult.WIN, wagered=bet.amount, profit=payout - bet.amount
                )
            else:
                await self.bot.db.game_stats.record_result(
                    guild_id, bet.placed_by.id, Game.HORSERACE, GameResult.LOSS, wagered=bet.amount, profit=-bet.amount
                )

        with suppress(discord.HTTPException):
            await table.message.edit(view=table.view.render(frames[-1], winner=winner))
        self.horse_tables.pop(channel_id, None)
