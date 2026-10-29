import asyncio
import datetime
import random
import time
import weakref
from contextlib import nullcontext
from typing import Annotated, Any, Literal

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands
from discord.ext.commands import Range

from app.cogs.economy.item_ui import ItemBrowser
from app.cogs.economy.risk import resolve_death
from app.cogs.economy.trade_ui import TradeView
from app.cogs.economy.ui import (
    BOOST_LABELS,
    DropView,
    EconomyHub,
    SearchView,
    WorkChallengeView,
    boost_display_line,
    progress_bar,
)
from app.core import Accent, Bot, Cog, converter, make_notice
from app.core.models import Context, PermissionTemplate, command, cooldown, describe, group
from app.core.pagination import LinePaginator
from app.core.timer import Timer
from app.services.economy import (
    ACHIEVEMENTS,
    BEG_COOLDOWN,
    BEG_DONORS,
    BEG_FAIL_LINES,
    BEG_SUCCESS_LINES,
    BEG_TABLE,
    DAILY_COOLDOWN,
    DIG_COOLDOWN,
    DIG_TABLE,
    DROP_KEYS,
    FISHING_COOLDOWN,
    FISHING_TABLE,
    HUNTING_COOLDOWN,
    HUNTING_TABLE,
    JOB_LADDER,
    LIFESAVER_KEY,
    MARKET_TAX,
    MAX_ITEM_STOCK,
    MAX_LISTINGS,
    MEME_FORMATS,
    MONTHLY_AMOUNT,
    MONTHLY_COOLDOWN,
    NO_TOOL_PAYOUT,
    PASSIVE_COOLDOWN,
    PET_SPECIES,
    POSTMEME_COOLDOWN,
    PRESTIGE_STEP,
    REPLY_LIMIT,
    SEARCH_COOLDOWN,
    VOTE_REWARD,
    WEEKLY_AMOUNT,
    WEEKLY_COOLDOWN,
    WORK_COOLDOWN,
    WORK_FAIL_FACTOR,
    WORK_SUCCESS_BONUS,
    ChatDrop,
    EconomySnapshot,
    GuildEconomySettings,
    PurchaseStatus,
    SearchLocation,
    TradeOffer,
    bank_room,
    best_game,
    boost_multiplier,
    builtin_error,
    collection_summary,
    compute_daily,
    compute_periodic,
    compute_pet_claim,
    compute_shift,
    daily_sale,
    describe_effect,
    describe_multipliers,
    evaluate_achievements,
    generate_challenge,
    generate_daily_quests,
    get_achievement,
    get_job,
    get_species,
    item_emoji,
    market_error,
    match_achievement,
    name_reserved_error,
    parse_amount,
    pick_search_options,
    pick_weighted_winner,
    prestige_multiplier,
    prestige_requirement,
    purchase_error,
    resolve_search,
    roll_bank_space,
    roll_chat_drop,
    roll_death,
    roll_drop,
    roll_loot,
    roll_lootbox,
    sale_price,
    sale_proceeds,
    sell_price,
    should_drop,
    split_cooldowns,
    tool_for_activity,
    tool_uses_left,
    trade_error,
    trade_failure_message,
    validate_item_effect,
    validate_item_meta,
    vote_claimable,
)
from app.utils import fnumb, fuzzy, get_asset_url, helpers, pluralize, timetools
from config import Emojis

#: Staked casino games whose wins advance the ``casino_win`` quest (fight and heist have their own kinds).
_CASINO_QUEST_GAMES = frozenset({
    'blackjack', 'slots', 'roulette', 'poker', 'tower', 'higherlower', 'dice', 'mines', 'coinflip',
    'horserace', 'russianroulette', 'crash', 'scratch', 'snakeeyes', 'gamble',
})

#: Ceiling for admin/transfer money arguments, far under the bigint columns so sums never overflow.
MAX_MONEY = 10**15

#: Species ids offered as slash choices by ``pet adopt``.
PetSpeciesChoice = Literal['hamster', 'cat', 'dog', 'parrot', 'fox', 'dragon']
#: Rarity ids offered as slash choices by ``shop add`` / ``shop edit`` (mirrors ``RARITIES``).
RarityChoice = Literal['common', 'uncommon', 'rare', 'epic', 'legendary']


class Economy(Cog):
    """Economy commands"""

    emoji = Emojis.Economy.cash

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self._last_drop: dict[int, datetime.datetime] = {}
        self._drops_cache: dict[int, tuple[float, bool]] = {}
        # ponytail: one lock per claimer, never pruned (a few bytes each); stops two racing claims both paying out.
        # Weak values: a lock disappears once no claim holds it, so the map never outgrows active claims.
        self._claim_locks: weakref.WeakValueDictionary[tuple[int, int], asyncio.Lock] = weakref.WeakValueDictionary()

    def _claim_lock(self, user_id: int, guild_id: int) -> asyncio.Lock:
        return self._claim_locks.setdefault((user_id, guild_id), asyncio.Lock())

    def _effect_line(self, guild: discord.Guild, item: Any) -> str | None:
        """A display line for an item's use-effect, resolving role mentions where possible."""
        effect = item.get('effect') or 'none'
        if effect == 'role':
            role = guild.get_role(item.get('effect_value') or 0)
            return f'Grants {role.mention} when used.' if role else 'Grants a role that no longer exists.'
        return describe_effect(effect, item.get('effect_value'), item.get('duration_minutes'))

    # -- shared plumbing (settings, payout scaling, quests, achievements) --

    async def _settings(self, guild_id: int) -> GuildEconomySettings:
        """The guild's economy settings (defaults when never configured)."""
        return GuildEconomySettings.from_record(await self.bot.db.economy.get_settings(guild_id))

    async def _payout_scale(
        self, user_id: int, guild_id: int, *, settings: GuildEconomySettings | None = None
    ) -> float:
        """The combined earning multiplier: guild payout multiplier times prestige bonus."""
        settings = settings or await self._settings(guild_id)
        prestige = await self.bot.db.economy.get_prestige(user_id, guild_id)
        return settings.payout_multiplier * prestige_multiplier(prestige)

    async def _gear(self, user_id: int, guild_id: int, activity: str) -> tuple[float, str]:
        """Spend a use of the activity's tool; returns ``(payout factor, text appended to the result)``.

        With the tool: full payout, a wear tick and a chance at an item drop (added to the inventory).
        Without it: :data:`NO_TOOL_PAYOUT` of the cash and a tip pointing at the shop.
        """
        db = self.bot.db.economy
        tool = tool_for_activity(activity)
        assert tool is not None and tool.durability is not None
        row = await db.get_builtin(guild_id, tool.key)
        if row is None or not await db.use_tool(user_id, guild_id, row['id'], tool.durability):
            return NO_TOOL_PAYOUT, f'\n-# Buy a {tool.name} in the shop for full payouts and item drops.'
        drop = roll_drop(activity)
        if drop is None:
            return 1.0, ''
        item = await db.ensure_builtin(guild_id, drop)
        await db.add_to_inventory(user_id, guild_id, item['id'], 1)
        await db.record_discovery(user_id, guild_id, drop.key)
        return 1.0, f'\n…and found {drop.emoji} **{item["name"]}**!'

    async def _award_drop(self, member: discord.Member, drop: ChatDrop) -> str:
        """Pay a chat drop to ``member`` (coins scaled like any earning, or the item); returns the result text."""
        db = self.bot.db.economy
        if drop.item is None:
            scale = await self._payout_scale(member.id, member.guild.id)
            amount = max(1, round(drop.coins * scale))
            balance = await self.bot.db.get_user_balance(member.id, member.guild.id)
            await balance.add(cash=amount)
            return f'{member.mention} grabbed the drop: {Emojis.Economy.cash} **{fnumb(amount)}**!'
        row = await db.ensure_builtin(member.guild.id, drop.item)
        await db.add_to_inventory(member.id, member.guild.id, row['id'], 1)
        if drop.item.drop_from:
            await db.record_discovery(member.id, member.guild.id, drop.item.key)
        return f'{member.mention} grabbed the drop: {drop.item.emoji} **{row["name"]}**!'

    async def _drops_enabled(self, guild_id: int) -> bool:
        """Whether chat drops are on for the guild, cached for 5 minutes so a message costs no DB read."""
        now = time.monotonic()
        cached = self._drops_cache.get(guild_id)
        if cached is None or cached[0] < now:
            cached = (now + 300, (await self._settings(guild_id)).drops_enabled)
            self._drops_cache[guild_id] = cached
        return cached[1]

    @Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        now = datetime.datetime.now(datetime.UTC)
        last = self._last_drop.get(message.guild.id)
        # Cheap in-memory gate first; the settings lookup only happens on a would-be drop.
        if not should_drop(last, now):
            return
        self._last_drop[message.guild.id] = now  # before the await, so concurrent messages can't both drop
        if not await self._drops_enabled(message.guild.id):
            return
        drop = roll_chat_drop()
        view = DropView(lambda member: self._award_drop(member, drop))
        prize = f'{Emojis.Economy.cash} coins' if drop.item is None else f'{drop.item.emoji} an item'
        try:
            view.message = await message.channel.send(
                f'\N{WRAPPED PRESENT} A drop appeared with {prize}! Press **Grab** first.', view=view)
        except discord.HTTPException:
            pass

    @staticmethod
    def _scale_suffix(scale: float) -> str:
        """A short note appended to payout messages when a bonus multiplier applied."""
        return f' *(x{scale:.2f} payout bonus)*' if scale > 1.0 else ''

    async def _ensure_quests(self, user_id: int, guild_id: int, day: datetime.date) -> list[Any]:
        """Fetch today's quest rows, materializing the deterministic board on first touch."""
        rows = await self.bot.db.economy.get_quests(user_id, guild_id, day)
        if rows:
            return rows
        quests = generate_daily_quests(guild_id, user_id, day)
        await self.bot.db.economy.create_quests(
            user_id, guild_id, day, [(q.key, q.kind, q.goal, q.reward) for q in quests])
        return await self.bot.db.economy.get_quests(user_id, guild_id, day)

    async def _advance_quests(self, user_id: int, guild_id: int, kind: str, amount: int = 1) -> str | None:
        """Advance today's quests of ``kind``; pays completed ones and returns their announcement (or ``None``)."""
        today = datetime.datetime.now(datetime.UTC).date()
        await self._ensure_quests(user_id, guild_id, today)
        completed = await self.bot.db.economy.advance_quests(user_id, guild_id, today, kind, amount)
        if not completed:
            return None

        board = {q.key: q for q in generate_daily_quests(guild_id, user_id, today)}
        total = sum(row['reward'] for row in completed)
        balance = await self.bot.db.get_user_balance(user_id, guild_id)
        await balance.add(cash=total)
        return '\n'.join(
            f'\N{SCROLL} **Quest complete:** {board[row["quest"]].description if row["quest"] in board else row["quest"]} '
            f'— {Emojis.Economy.cash} **{fnumb(row["reward"])}**'
            for row in completed
        )

    async def _bump_quests(self, ctx: Context, kind: str, amount: int = 1) -> None:
        """Advance today's quests of ``kind``; pays and announces any that complete."""
        assert ctx.guild is not None
        lines = await self._advance_quests(ctx.author.id, ctx.guild.id, kind, amount)
        if lines:
            await ctx.send(lines)
            await self._sync_achievements(ctx)

    async def _award_achievements(self, user_id: int, guild_id: int) -> str | None:
        """Award any newly qualified achievements (idempotent); returns the unlock text, or ``None``.

        Assembles the :class:`EconomySnapshot` from the database, diffs the
        qualifying set against what is already earned and pays the rewards.
        """
        db = self.bot.db
        balance = await db.get_user_balance(user_id, guild_id)
        daily = await db.economy.get_daily(user_id, guild_id)
        job_row = await db.economy.get_job(user_id, guild_id)
        snapshot = EconomySnapshot(
            net_worth=balance.total + await db.economy.inventory_value(user_id, guild_id),
            daily_streak=daily['streak'] if daily else 0,
            shifts=job_row['shifts'] if job_row else 0,
            job_id=job_row['job_id'] if job_row else None,
            prestige=await db.economy.get_prestige(user_id, guild_id),
            has_pet=await db.economy.get_pet(user_id, guild_id) is not None,
            quests_completed=await db.economy.count_completed_quests(user_id, guild_id),
            items_owned=await db.economy.count_items(user_id, guild_id),
            discoveries=len(DROP_KEYS.intersection(await db.economy.get_discoveries(user_id, guild_id))),
        )
        new_ids = await db.economy.award_achievements(user_id, guild_id, evaluate_achievements(snapshot))
        unlocked = [a for a in (get_achievement(aid) for aid in new_ids) if a is not None]
        if not unlocked:
            return None
        reward = sum(a.reward for a in unlocked)
        if reward:
            await balance.add(cash=reward)
        lines = '\n'.join(f'{a.emoji} **{a.name}** — {a.description}' for a in unlocked)
        return f'{lines}\n\nReward: {Emojis.Economy.cash} **{fnumb(reward)}**'

    async def _sync_achievements(self, ctx: Context, *, announce: bool = True) -> None:
        """Award any newly qualified achievements for the invoker and, unless ``announce`` is off, post them."""
        assert ctx.guild is not None
        text = await self._award_achievements(ctx.author.id, ctx.guild.id)
        if announce and text:
            await ctx.send(view=make_notice('\N{TROPHY} Achievement unlocked!', text, accent=Accent.success))

    async def job_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[Choice[str | int | float]]:
        """Autocomplete over the career ladder, cheapest rung first."""
        results = fuzzy.finder(current, JOB_LADDER, key=lambda j: j.name)
        return [
            app_commands.Choice(
                name=f'{job.name} • {fnumb(job.pay_min)}-{fnumb(job.pay_max)}/shift • needs {job.shifts_required} shifts',
                value=job.id,
            )
            for job in results[:25]
        ]

    async def shop_item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[Choice[str | int | float]]:
        """Autocomplete over every item sold in the guild's shop."""
        assert interaction.guild_id is not None
        items = await self.bot.db.economy.get_items(interaction.guild_id, for_sale_only=True)
        results = fuzzy.finder(current, items, key=lambda r: r['name'])
        return [
            app_commands.Choice(name=f"{r['name'][:80]} • {fnumb(r['price'])}", value=r['name'][:100])
            for r in results[:25]
        ]

    async def owned_item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[Choice[str | int | float]]:
        """Autocomplete over the items the invoking member actually owns."""
        assert interaction.guild_id is not None
        rows = await self.bot.db.economy.get_inventory(interaction.user.id, interaction.guild_id)
        results = fuzzy.finder(current, rows, key=lambda r: r['name'])
        return [
            app_commands.Choice(name=f"{r['name'][:80]} ×{r['quantity']}", value=r['name'][:100])
            for r in results[:25]
        ]

    @group(
        "economy",
        aliases=["eco"],
        description="Your whole economy standing in one interactive card.",
        guild_only=True,
        hybrid=True,
    )
    async def economy(self, ctx: Context) -> None:
        """An interactive overview: balance, job, pet, daily quests, achievements and perks.

        Use the select menu to jump between sections; every switch shows live numbers.
        """
        assert ctx.guild is not None
        hub = EconomyHub(self, ctx)
        await hub.build()
        hub.message = await ctx.send(view=hub)

    @economy.command(
        "set-money",
        aliases=["setbal", "set-balance"],
        description="Sets a user's balance",
        guild_only=True,
        user_permissions=PermissionTemplate.admin,
    )
    @describe(
        member="The user to set the balance for.",
        amount="The amount to set the balance to.",
        to="Whether to set the balance to the bank or cash.",
    )
    async def set_money(
        self,
        ctx: Context,
        member: Annotated[discord.Member, converter.MemberConverter],
        amount: Range[int, 0, MAX_MONEY],
        to: Literal["bank", "cash"],
    ) -> None:
        """Sets a user's balance"""
        if member.bot:
            await ctx.send_error("Cannot set a bot's balance.")
            return

        balance = await ctx.db.get_user_balance(member.id, ctx.guild.id)  # type: ignore[union-attr]
        if to == "bank":
            await balance.update(bank=amount)
        else:
            await balance.update(cash=amount)

        await ctx.send_success(
            f"Successfully set **{member.display_name}'s** {to} to {Emojis.Economy.cash} **{fnumb(amount)}**."
        )

    @economy.command(
        "add-money-role",
        description="Adds a certain amount of money to all users with the specified role.",
        guild_only=True,
        user_permissions=PermissionTemplate.admin,
    )
    @describe(
        role="The role to add the money to.",
        amount="The amount to add to the balance.",
        to="Whether to add the balance to the bank or cash.",
    )
    async def add_money_role(
        self, ctx: Context, role: Annotated[discord.Role, commands.RoleConverter], amount: Range[int, 1, MAX_MONEY], to: Literal["bank", "cash"]
    ) -> None:
        """Sets a user's balance"""
        humans = [member for member in role.members if not member.bot]
        total = len(humans)
        # Each member is a balance fetch + write, so surface progress for large roles.
        tracker = ctx.progress(f"Adding money to {total} members...") if total >= 10 else nullcontext()
        async with tracker as progress:
            for i, member in enumerate(humans, 1):
                balance = await ctx.db.get_user_balance(member.id, ctx.guild.id)  # type: ignore[union-attr]
                if to == "bank":
                    await balance.add(bank=amount)
                else:
                    await balance.add(cash=amount)

                if progress is not None and (i == total or i % 10 == 0):
                    await progress.tick(i, total, "Adding money")

        await ctx.send_success(
            f"Successfully added {Emojis.Economy.cash} **{fnumb(amount)}** to all users with the role **{role.name}**."
        )

    @economy.command(
        "remove-money",
        aliases=["rmbal", "rm-money"],
        description="Removes from a user's balance",
        guild_only=True,
        user_permissions=PermissionTemplate.admin,
    )
    @describe(
        member="The user to remove the balance from.",
        amount="The amount to remove from the balance.",
        to="Whether to remove the balance from the bank or cash.",
    )
    async def remove_money(
        self,
        ctx: Context,
        member: Annotated[discord.Member, converter.MemberConverter],
        amount: Range[int, 1, MAX_MONEY],
        to: Literal["bank", "cash"],
    ) -> None:
        """Removes from a user's balance"""
        if member.bot:
            await ctx.send_error("Cannot remove from a bot's balance.")
            return

        # One guarded UPDATE: a concurrent spend cannot push the wallet negative between check and write.
        deltas = {to: -amount}
        if await self.bot.db.users.adjust_balance(member.id, ctx.guild.id, **deltas) is None:  # type: ignore[union-attr]
            await ctx.send_error("Cannot remove more than the user's balance.")
            return

        await ctx.send_success(
            f"Successfully removed {Emojis.Economy.cash} **{fnumb(amount)}** from **{member.display_name}'s** {to}."
        )

    @command("deposit", aliases=["dep"], description="Deposits money into your bank.", guild_only=True, hybrid=True)
    @describe(amount="The amount to deposit: a number (2k works), all or half.")
    async def deposit(self, ctx: Context, amount: str) -> None:
        """Deposits money into your bank. Takes a number, `all`/`max` or `half` of your cash."""
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)  # type: ignore[union-attr]
        value = parse_amount(amount, balance.cash)
        if value is None:
            await ctx.send_error("Give a positive amount, `all` or `half` — and have some cash to deposit.")
            return
        if balance.cash < value:
            await ctx.send_error("Cannot deposit more than your balance.")
            return

        capped = (await self._settings(ctx.guild.id)).bank_cap  # type: ignore[union-attr]
        if capped:
            room = bank_room(balance.bank, balance.bank_space)
            if room == 0:
                await ctx.send_error(f"Your bank is full: it holds up to {Emojis.Economy.cash} **{fnumb(balance.bank_space)}**.")
                return
            value = min(value, room)

        # ponytail: the bank cap is checked against the read above; two racing deposits can overshoot it once.
        if await ctx.db.users.adjust_balance(ctx.author.id, ctx.guild.id, cash=-value, bank=value) is None:  # type: ignore[union-attr]
            await ctx.send_error("Cannot deposit more than your balance.")
            return
        await ctx.send_success(f"Successfully deposited {Emojis.Economy.cash} **{fnumb(value)}** into your bank.")
        await self._bump_quests(ctx, "deposit", value)

    @command("withdraw", description="Withdraws money from your bank.", guild_only=True, hybrid=True)
    @describe(amount="The amount to withdraw: a number (2k works), all or half.")
    async def withdraw(self, ctx: Context, amount: str) -> None:
        """Withdraws money from your bank. Takes a number, `all`/`max` or `half` of your bank."""
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)  # type: ignore[union-attr]
        value = parse_amount(amount, balance.bank)
        if value is None:
            await ctx.send_error("Give a positive amount, `all` or `half` — and have something in the bank.")
            return
        if balance.bank < value:
            await ctx.send_error("Cannot withdraw more than your bank balance.")
            return

        if await ctx.db.users.adjust_balance(ctx.author.id, ctx.guild.id, cash=value, bank=-value) is None:  # type: ignore[union-attr]
            await ctx.send_error("Cannot withdraw more than your bank balance.")
            return
        await ctx.send_success(f"Successfully withdrew {Emojis.Economy.cash} **{fnumb(value)}** from your bank.")

    @command("transfer", description="Transfers money to another user.", guild_only=True, hybrid=True)
    @describe(member="The user to transfer the money to.", amount="The amount to transfer.")
    async def transfer(
        self, ctx: Context, member: Annotated[discord.Member, converter.MemberConverter], amount: Range[int, 1, MAX_MONEY]
    ) -> None:
        """Transfers money to another user."""
        if member.bot:
            await ctx.send_error("Cannot transfer to a bot.")
            return

        if await self.bot.db.economy.transfer_cash(ctx.author.id, member.id, ctx.guild.id, amount) is None:  # type: ignore[union-attr]
            await ctx.send_error("Cannot transfer more than your balance.")
            return
        await ctx.send_success(
            f"Successfully transferred {Emojis.Economy.cash} **{fnumb(amount)}** to **{member.display_name}**."
        )

    @group("balance", alias="bal", description="Shows a user's balance", guild_only=True, hybrid=True)
    @describe(user="The user to show the balance for.")
    async def balance(
        self, ctx: Context, member: Annotated[discord.Member | None, converter.MemberConverter] = None
    ) -> None:
        """Shows your balance"""
        if member and member.bot:
            await ctx.send_error("Cannot get a bot's balance.")
            return

        user = member or ctx.author
        balance = await ctx.db.get_user_balance(user.id, ctx.guild.id)  # type: ignore[union-attr]
        everyone = await ctx.db.get_guild_balances(ctx.guild.id)  # type: ignore[union-attr]
        items = await ctx.db.economy.inventory_value(user.id, ctx.guild.id)  # type: ignore[union-attr]
        capped = (await self._settings(ctx.guild.id)).bank_cap  # type: ignore[union-attr]
        bank = f'{fnumb(balance.bank)} / {fnumb(balance.bank_space)}' if capped else fnumb(balance.bank)
        richer = sum(1 for other in everyone if other.user_id != user.id and other.total > balance.total)
        rank = f'**#{richer + 1:,}** of {max(len(everyone), 1):,}'
        cash = Emojis.Economy.cash
        await ctx.send(view=make_notice(
            f"{user.display_name}'s Balance",
            f'{cash} **Cash** {fnumb(balance.cash)}\n{cash} **Bank** {bank}\n'
            f'{cash} **Items** {fnumb(items)}\n{cash} **Net worth** {fnumb(balance.total + items)}\n'
            f'\N{TROPHY} Server rank {rank}',
            thumbnail=get_asset_url(user),
        ))

    @balance.command("leaderboard", alias="top", description="Shows the leaderboard of the server", guild_only=True)
    @describe(kind="What to rank by (default: total cash + bank).")
    async def leaderboard(
        self, ctx: Context, kind: Literal["total", "networth", "prestige", "streak", "games"] = "total"
    ) -> None:
        """Shows the leaderboard of the server: total money, net worth, prestige, daily streak or game profit."""
        assert ctx.guild is not None
        repo = self.bot.db.economy
        cash = Emojis.Economy.cash
        if kind == "total":
            balances = await ctx.db.get_guild_balances(ctx.guild.id)
            # The underlying query returns rows in arbitrary order, so rank richest-first here.
            balances = sorted(balances, key=lambda balance: balance.total, reverse=True)
            rows = [(balance.user_id, balance.total) for balance in balances]
            footer = f"Total Server Money: {fnumb(sum(value for _, value in rows))}"
            title, fmt = "Economy Leaderboard", lambda v: f"{cash} **{fnumb(v)}**"
        else:
            title, query, fmt = {
                "networth": ("Net Worth Leaderboard", repo.top_networth, lambda v: f"{cash} **{fnumb(v)}**"),
                "prestige": ("Prestige Leaderboard", repo.top_prestige, lambda v: f"**Prestige {v}**"),
                "streak": ("Daily Streak Leaderboard", repo.top_streak, lambda v: f"**{v}** day streak"),
                "games": ("Game Profit Leaderboard", repo.top_game_profit, lambda v: f"{cash} **{fnumb(v)}**"),
            }[kind]
            rows = [(r["user_id"], r["value"]) for r in await query(ctx.guild.id, 100)]
            footer = f"Top {len(rows)} by {kind}"

        users = [f"**{index}.** <@{user_id}> • {fmt(value)}" for index, (user_id, value) in enumerate(rows, 1)]
        embed = discord.Embed(
            title=title, description="This is the server's leaderboard.\n\n", colour=helpers.Colour.white()
        )
        embed.set_author(name=ctx.guild.name, icon_url=get_asset_url(ctx.guild))  # type: ignore[arg-type]
        embed.set_footer(text=footer, icon_url=discord.PartialEmoji.from_str(cash).url)
        await LinePaginator.start(ctx, entries=users, embed=embed, location='description')

    @command("daily", description="Claim your daily reward and build a streak.", guild_only=True, hybrid=True, extras={"example": "daily"})
    async def daily(self, ctx: Context) -> None:
        """Claim your daily reward. Claim on consecutive days to grow your streak bonus."""
        assert ctx.guild is not None
        async with self._claim_lock(ctx.author.id, ctx.guild.id):
            record = await self.bot.db.economy.get_daily(ctx.author.id, ctx.guild.id)

            last_claim = record["last_claim"].replace(tzinfo=datetime.UTC) if record and record["last_claim"] else None
            streak = record["streak"] if record else 0
            now = ctx.message.created_at

            settings = await self._settings(ctx.guild.id)
            result = compute_daily(last_claim, streak, now=now, base=settings.daily_base)
            if not result.claimed:
                assert result.next_available is not None
                await ctx.send_error(
                    f"You've already claimed your daily reward. Come back {discord.utils.format_dt(result.next_available, 'R')}."
                )
                return

            scale = await self._payout_scale(ctx.author.id, ctx.guild.id, settings=settings)
            amount = round(result.amount * scale)
            balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
            await balance.add(cash=amount, bank_space=roll_bank_space())
            await self.bot.db.economy.set_daily(ctx.author.id, ctx.guild.id, now, result.streak)

        await ctx.send_success(
            f"You claimed {Emojis.Economy.cash} **{fnumb(amount)}**!{self._scale_suffix(scale)} "
            f"\N{FIRE} Streak: **{pluralize(result.streak):day}**."
        )
        await self._sync_achievements(ctx)

    @command("weekly", description="Claim your weekly reward.", guild_only=True)
    async def weekly(self, ctx: Context) -> None:
        """Claim your weekly reward — a bigger payout on a 7-day cooldown."""
        assert ctx.guild is not None
        async with self._claim_lock(ctx.author.id, ctx.guild.id):
            record = await self.bot.db.economy.get_daily(ctx.author.id, ctx.guild.id)
            last = record["last_weekly"].replace(tzinfo=datetime.UTC) if record and record["last_weekly"] else None
            now = ctx.message.created_at

            result = compute_periodic(last, now=now, cooldown=WEEKLY_COOLDOWN)
            if not result.claimed:
                assert result.next_available is not None
                await ctx.send_error(
                    f"You've already claimed your weekly reward. Come back {discord.utils.format_dt(result.next_available, 'R')}."
                )
                return

            scale = await self._payout_scale(ctx.author.id, ctx.guild.id)
            amount = round(WEEKLY_AMOUNT * scale)
            balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
            await balance.add(cash=amount)
            await self.bot.db.economy.set_weekly(ctx.author.id, ctx.guild.id, now)
        await ctx.send_success(
            f"You claimed your weekly {Emojis.Economy.cash} **{fnumb(amount)}**!{self._scale_suffix(scale)}"
        )

    @command("monthly", description="Claim your monthly reward.", guild_only=True)
    async def monthly(self, ctx: Context) -> None:
        """Claim your monthly reward — the biggest fixed payout, once every 30 days."""
        assert ctx.guild is not None
        async with self._claim_lock(ctx.author.id, ctx.guild.id):
            record = await self.bot.db.economy.get_daily(ctx.author.id, ctx.guild.id)
            last = record["last_monthly"].replace(tzinfo=datetime.UTC) if record and record["last_monthly"] else None
            now = ctx.message.created_at

            result = compute_periodic(last, now=now, cooldown=MONTHLY_COOLDOWN)
            if not result.claimed:
                assert result.next_available is not None
                await ctx.send_error(
                    f"You've already claimed your monthly reward. Come back {discord.utils.format_dt(result.next_available, 'R')}."
                )
                return

            scale = await self._payout_scale(ctx.author.id, ctx.guild.id)
            amount = round(MONTHLY_AMOUNT * scale)
            balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
            await balance.add(cash=amount)
            await self.bot.db.economy.set_monthly(ctx.author.id, ctx.guild.id, now)
        await ctx.send_success(
            f"You claimed your monthly {Emojis.Economy.cash} **{fnumb(amount)}**!{self._scale_suffix(scale)}"
        )

    @command("voteclaim", alias="claimvote", description="Claim coins for your active bot-list vote.", guild_only=True)
    async def voteclaim(self, ctx: Context) -> None:
        """Claim a coin reward for voting. One claim per vote, per server (see `vote` to vote)."""
        assert ctx.guild is not None
        status = await ctx.bot.db.votes.get_status(ctx.author.id)
        now = ctx.message.created_at

        def aware(value: datetime.datetime | None) -> datetime.datetime | None:
            return value.replace(tzinfo=datetime.UTC) if value is not None else None

        voted = aware(status["last_voted_at"]) if status else None
        expires = aware(status["expires_at"]) if status else None
        claimed = aware(await self.bot.db.economy.get_last_vote_claim(ctx.author.id, ctx.guild.id))
        if not vote_claimable(voted, expires, claimed, now):
            if expires is not None and expires > now:
                await ctx.send_error("You already claimed the reward for your current vote. Vote again to claim more!")
            else:
                await ctx.send_error("You have no active vote. Use `vote` to see where to vote, then claim here.")
            return

        assert voted is not None
        # The stamp is the gate: only the invocation that wins the atomic update gets paid.
        if not await self.bot.db.economy.claim_vote(ctx.author.id, ctx.guild.id, voted, now):
            await ctx.send_error("You already claimed the reward for your current vote. Vote again to claim more!")
            return

        scale = await self._payout_scale(ctx.author.id, ctx.guild.id)
        amount = round(VOTE_REWARD * scale)
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        await balance.add(cash=amount)
        await ctx.send_success(
            f"Thanks for voting! You claimed {Emojis.Economy.cash} **{fnumb(amount)}**!{self._scale_suffix(scale)}"
        )

    # -- items: shared actions (commands and the item browser both call these) --

    @staticmethod
    def _valid_emoji(value: str) -> bool:
        """Whether ``value`` is a custom emoji or plausibly a unicode one (no plain words)."""
        emoji = discord.PartialEmoji.from_str(value.strip())
        if emoji.id:
            return True
        return 0 < len(value.strip()) <= 8 and not any(c.isascii() and c.isalnum() for c in value)

    async def daily_deals(self, guild_id: int) -> dict[int, int]:
        """Today's (UTC) sale for the guild: ``item_id -> percent off``."""
        items = await self.bot.db.economy.get_items(guild_id)
        return daily_sale(guild_id, datetime.datetime.now(datetime.UTC).date(), [i['id'] for i in items])

    @staticmethod
    def _parse_until(text: str) -> datetime.datetime:
        """A future moment from ``text`` ("2d", "next friday") as naive UTC; raises ``commands.BadArgument``."""
        return timetools.ensure_utc(timetools.FutureTime(text).dt).replace(tzinfo=None)

    async def trade_action(
        self, a: discord.Member, b: discord.Member, offer_a: TradeOffer, offer_b: TradeOffer
    ) -> tuple[bool, str]:
        """Run a confirmed trade in one transaction; returns ``(ok, message)``."""
        guild_id = a.guild.id
        await self.bot.db.get_user_balance(a.id, guild_id)  # make sure both wallets exist
        await self.bot.db.get_user_balance(b.id, guild_id)
        result = await self.bot.db.economy.execute_trade(
            guild_id, a.id, b.id, list(offer_a.items.items()), offer_a.cash, list(offer_b.items.items()), offer_b.cash
        )
        if result != 'ok':
            return False, trade_failure_message(result)
        return True, f'\N{HANDSHAKE} Trade complete between **{a.display_name}** and **{b.display_name}**.'

    async def buy_item_action(self, member: discord.Member, item_id: int, quantity: int) -> tuple[bool, str]:
        """Buy ``quantity`` of a shop item for ``member``; returns ``(ok, message)``."""
        guild_id = member.guild.id
        item = await self.bot.db.economy.get_item_by_id(guild_id, item_id)
        if item is None:
            return False, 'That item is no longer in the shop.'
        if not item['for_sale']:
            return False, f'**{item["name"]}** is not sold in the shop - it can only be found.'
        if item['required_role_id'] and not member.get_role(item['required_role_id']):
            return False, f'**{item["name"]}** is reserved for members with <@&{item["required_role_id"]}>.'

        balance = await self.bot.db.get_user_balance(member.id, guild_id)  # also creates the wallet row
        percent = (await self.daily_deals(guild_id)).get(item_id, 0)
        unit = sale_price(item['price'], percent) if percent else item['price']
        status, item = await self.bot.db.economy.buy_item(member.id, guild_id, item_id, quantity, unit)
        outcome = PurchaseStatus(status)
        if outcome is not PurchaseStatus.ok:
            owned = await self.bot.db.economy.get_quantity(member.id, guild_id, item_id)
            error = purchase_error(
                outcome, name=item['name'] if item else 'That item', stock=item['stock'] if item else None,
                limit=item['max_per_user'] if item else None, owned=owned,
                cost=unit * quantity if item else 0, cash=balance.cash,
            )
            return False, error or 'The purchase failed.'

        assert item is not None
        message = f'Bought **{quantity}× {item_emoji(item)} {item["name"]}** for {Emojis.Economy.cash} **{fnumb(unit * quantity)}**.'
        if percent:
            message += f' (-{percent}% today)'
        if unlocked := await self._award_achievements(member.id, guild_id):
            message += f'\n\N{TROPHY} {unlocked}'
        return True, message

    async def sell_item_action(self, member: discord.Member, item_id: int, quantity: int) -> tuple[bool, str]:
        """Sell ``quantity`` of an owned item back for :func:`sell_price` each; returns ``(ok, message)``."""
        guild_id = member.guild.id
        item = await self.bot.db.economy.get_item_by_id(guild_id, item_id)
        if item is None:
            return False, 'That item no longer exists.'
        payout = sell_price(item['price']) * quantity
        left = await self.bot.db.economy.sell_item(member.id, guild_id, item_id, quantity, payout)
        if left is None:
            owned = await self.bot.db.economy.get_quantity(member.id, guild_id, item_id)
            return False, f'You only own **{owned}× {item["name"]}**.'
        return True, (
            f'Sold **{quantity}× {item_emoji(item)} {item["name"]}** for {Emojis.Economy.cash} **{fnumb(payout)}**. '
            f'You have **{left}** left.'
        )

    async def gift_item_action(
        self, member: discord.Member, recipient: discord.Member, item_id: int, quantity: int
    ) -> tuple[bool, str]:
        """Move ``quantity`` of an item from ``member`` to ``recipient``; returns ``(ok, message)``."""
        guild_id = member.guild.id
        if recipient.bot:
            return False, 'Cannot gift items to a bot.'
        if recipient.id == member.id:
            return False, 'You cannot gift items to yourself.'
        item = await self.bot.db.economy.get_item_by_id(guild_id, item_id)
        if item is None:
            return False, 'That item no longer exists.'
        if item['max_per_user'] is not None:
            held = await self.bot.db.economy.get_quantity(recipient.id, guild_id, item_id)
            if held + quantity > item['max_per_user']:
                return False, f'**{recipient.display_name}** can hold at most **{item["max_per_user"]}× {item["name"]}**.'

        if not await self.bot.db.economy.transfer_item(member.id, recipient.id, guild_id, item_id, quantity):
            owned = await self.bot.db.economy.get_quantity(member.id, guild_id, item_id)
            return False, f'You only own **{owned}× {item["name"]}**.'

        message = f'\N{WRAPPED PRESENT} You gifted **{quantity}× {item_emoji(item)} {item["name"]}** to **{recipient.display_name}**.'
        if quests := await self._advance_quests(member.id, guild_id, 'gift', quantity):
            message += f'\n{quests}'
        return True, message

    async def use_item_action(self, member: discord.Member, item_id: int, quantity: int = 1) -> tuple[bool, str]:
        """Use an item (see :meth:`_use_item`), then take its ``remove_role`` and append its reply message."""
        ok, message = await self._use_item(member, item_id, quantity)
        item = await self.bot.db.economy.get_item_by_id(member.guild.id, item_id) if ok else None
        if item is None:
            return ok, message
        if item.get('remove_role_id') and (role := member.guild.get_role(item['remove_role_id'])) in member.roles:
            try:
                await member.remove_roles(role, reason=f'Used shop item {item["name"]}')
                message += f'\nThe **{role.name}** role was removed.'
            except discord.HTTPException:
                message += f'\n\N{WARNING SIGN} I couldn\'t remove the **{role.name}** role - check my role hierarchy.'
        if item.get('reply_message'):
            message += f'\n{item["reply_message"]}'
        return True, message

    async def _use_item(self, member: discord.Member, item_id: int, quantity: int = 1) -> tuple[bool, str]:
        """Use (consume ``quantity`` of) an owned item and apply its effect; returns ``(ok, message)``.

        The units are taken atomically *before* the effect applies, so two quick uses
        of the last one cannot both pay out; a role grant that fails refunds it.
        Only ``cash`` and ``lootbox`` items can be used in bulk.
        """
        guild = member.guild
        item = await self.bot.db.economy.get_item_by_id(guild.id, item_id)
        if item is None:
            return False, 'That item no longer exists.'

        if item.get('builtin_key') == LIFESAVER_KEY:
            return False, f'**{item["name"]}** works automatically - keep it in your inventory.'

        effect: str = item['effect'] or 'none'
        value: int = item['effect_value'] or 0
        duration: int = item['duration_minutes'] or 0
        if validate_item_effect(effect, value or None, duration or None) is not None:
            # Misconfigured (e.g. pre-dates validation) - fall back to a plain collectible.
            effect = 'none'
        if quantity > 1 and effect not in ('cash', 'lootbox'):
            return False, f'**{item["name"]}** can only be used one at a time.'

        role: discord.Role | None = None
        if effect == 'role':
            role = guild.get_role(value)
            if role is None:
                return False, 'The role this item grants no longer exists — ask an admin to fix the item.'
            if role in member.roles:
                return False, f'You already have the **{role.name}** role; the item was not consumed.'

        left = await self.bot.db.economy.consume_item(member.id, guild.id, item_id, quantity)
        if left is None:
            owned = await self.bot.db.economy.get_quantity(member.id, guild.id, item_id)
            if quantity > 1 and owned:
                return False, f'You only own **{owned}× {item["name"]}**.'
            return False, f'You don\'t own any **{item["name"]}**.'
        name = f'{item_emoji(item)} **{item["name"]}**' if quantity == 1 else f'**{quantity}× {item_emoji(item)} {item["name"]}**'
        tail = f'You have **{left}** left.'

        if effect == 'cash':
            balance = await self.bot.db.get_user_balance(member.id, guild.id)
            await balance.add(cash=value * quantity)
            return True, f'You redeemed {name} for {Emojis.Economy.cash} **{fnumb(value * quantity)}**. {tail}'
        if effect == 'lootbox':
            payout = sum(roll_lootbox(value) for _ in range(quantity))
            balance = await self.bot.db.get_user_balance(member.id, guild.id)
            await balance.add(cash=payout)
            flavour = (
                '\N{PARTY POPPER} Jackpot!' if payout >= value * quantity * 2
                else 'Unlucky...' if payout < value * quantity // 2
                else 'Not bad!'
            )
            return True, f'You opened {name} and found {Emojis.Economy.cash} **{fnumb(payout)}**. {flavour} {tail}'
        if role is not None:
            try:
                await member.add_roles(role, reason=f'Used shop item {item["name"]}')
            except discord.HTTPException:
                await self.bot.db.economy.add_to_inventory(member.id, guild.id, item_id, 1)
                return False, f'I couldn\'t grant **{role.name}** — check my role hierarchy and permissions. The item was refunded.'
            return True, f'You used {name} and received the **{role.name}** role. {tail}'
        if effect in ('xp_boost', 'loot_boost'):
            kind = 'xp' if effect == 'xp_boost' else 'loot'
            expires = await self.bot.db.economy.add_boost(member.id, guild.id, kind, boost_multiplier(value), duration)
            when = discord.utils.format_dt(expires.replace(tzinfo=datetime.UTC), 'R')
            return True, f'You activated {name}: **+{value}%** {BOOST_LABELS[kind]}, ending {when}. {tail}'
        if effect == 'bank_space':
            balance = await self.bot.db.get_user_balance(member.id, guild.id)
            await balance.add(bank_space=value)
            return True, f'You used {name}: your bank can now hold **{fnumb(value)}** more. {tail}'
        if effect == 'rob_shield':
            expires = await self.bot.db.economy.add_boost(member.id, guild.id, 'shield', 1.0, duration)
            when = discord.utils.format_dt(expires.replace(tzinfo=datetime.UTC), 'R')
            return True, f'\N{SHIELD} You activated {name}: rob attempts against you are blocked until {when}. {tail}'
        return True, f'You used {name}. Nothing obvious happened — must be a collectible. {tail}'

    async def _send_result(self, ctx: Context, result: tuple[bool, str]) -> None:
        ok, message = result
        await (ctx.send_success(message) if ok else ctx.send_error(message))

    async def _open_hub(self, ctx: Context, page: str, member: discord.Member | None = None) -> None:
        """Open the economy hub on ``page`` (``member`` shows someone else's data read-only)."""
        assert ctx.guild is not None
        hub = EconomyHub(self, ctx, page=page, target=member)
        await hub.build()
        hub.message = await ctx.send(view=hub)

    async def _send_browser(self, ctx: Context, browser: ItemBrowser) -> None:
        await browser.build()
        browser.message = await ctx.send(view=browser)

    # -- items: commands --------------------------------------------------------

    @group("shop", fallback="list", description="Browse and buy from the server shop.", guild_only=True, hybrid=True, extras={"example": "shop"})
    async def shop(self, ctx: Context) -> None:
        """Browse the server shop: filter by category, open an item, buy with one click."""
        assert isinstance(ctx.author, discord.Member)
        await self._send_browser(ctx, ItemBrowser(self, ctx.author, mode='shop'))

    @command("item", description="Show what an item does, what it costs and how many you own.", guild_only=True, hybrid=True, extras={"example": "item Lucky Charm"})
    @describe(name="The item to look up.")
    @app_commands.autocomplete(name=shop_item_autocomplete)  # type: ignore
    async def item(self, ctx: Context, *, name: str) -> None:
        """Open an item's card with its price, effect, stock and the actions you can take."""
        assert ctx.guild is not None and isinstance(ctx.author, discord.Member)
        item = await self.bot.db.economy.get_item(ctx.guild.id, name)
        if item is None:
            await ctx.send_error(f"No item named **{name}** exists in the shop.")
            return
        mode = 'shop' if item['for_sale'] else 'inventory'  # drops have no shop card, only the owner's
        await self._send_browser(ctx, ItemBrowser(self, ctx.author, mode=mode, item_id=item['id']))

    @shop.command(
        "add",
        description="Add an item to the shop.",
        guild_only=True,
        user_permissions=PermissionTemplate.admin,
    )
    @describe(
        name="The item name (quote it if it has spaces).",
        price="The purchase price.",
        effect="What using the item does (default: nothing, a plain collectible).",
        value="Effect payload: the cash amount (cash/lootbox) or the bonus percent (boosts).",
        duration="How long boost effects last, in minutes.",
        role="The role a role item grants.",
        emoji="An emoji shown before the item's name.",
        rarity="Cosmetic rarity tier (sorting and colour).",
        stock="Limited stock; leave empty for unlimited.",
        limit="Most one member may hold at once; leave empty for unlimited.",
        required_role="Only members with this role may buy it.",
        available_for='How long it stays on sale, e.g. "7d" or "2 weeks"; leave empty for always.',
        remove_role="A role taken away from the member when the item is used.",
        reply_message="An extra line shown when the item is used.",
        description="A description.",
    )
    async def shop_add(
        self,
        ctx: Context,
        name: str,
        price: commands.Range[int, 1],
        effect: Literal["none", "cash", "lootbox", "role", "xp_boost", "loot_boost", "rob_shield"] = "none",
        value: commands.Range[int, 1] | None = None,
        duration: commands.Range[int, 1] | None = None,
        role: discord.Role | None = None,
        emoji: str | None = None,
        rarity: RarityChoice = "common",
        stock: commands.Range[int, 0, MAX_ITEM_STOCK] | None = None,
        limit: commands.Range[int, 1] | None = None,
        required_role: discord.Role | None = None,
        available_for: str | None = None,
        remove_role: discord.Role | None = None,
        reply_message: commands.Range[str, 1, REPLY_LIMIT] | None = None,
        *,
        description: str | None = None,
    ) -> None:
        """Add an item to the server shop, optionally with an effect for `use`.

        **Effects:**
        - **cash**: redeems for `value` cash.
        - **lootbox**: pays out a random amount around `value` cash.
        - **role**: grants `role` to the user.
        - **xp_boost**: +`value`% leveling XP for `duration` minutes.
        - **loot_boost**: +`value`% fish/hunt payouts for `duration` minutes.
        - **rob_shield**: blocks rob attempts against the user for `duration` minutes.

        Emoji, rarity, stock, per-member limit, a required role, an end date
        (`available_for`), a role to remove on use and a reply message are optional
        and can be changed later with `shop edit`.
        """
        assert ctx.guild is not None
        effect_value = value
        if effect == "role":
            if role is None:
                await ctx.send_error("Role items need a **role** to grant.")
                return
            if role.is_default() or role.managed:
                await ctx.send_error("That role cannot be granted by a bot.")
                return
            if role >= ctx.guild.me.top_role:
                await ctx.send_error("That role is above my top role — I wouldn't be able to grant it.")
                return
            effect_value = role.id

        error = name_reserved_error(name) or validate_item_effect(effect, effect_value, duration) or validate_item_meta(
            name, rarity=rarity, reply_message=reply_message
        )
        until = None
        if available_for is not None:
            try:
                until = self._parse_until(available_for)
            except commands.BadArgument:
                error = error or 'Could not parse `available_for`. Try something like "7d" or "2 weeks".'
        if emoji is not None and not self._valid_emoji(emoji):
            error = error or "That doesn't look like an emoji."
        if error:
            await ctx.send_error(error)
            return

        record = await self.bot.db.economy.create_item(
            ctx.guild.id, name.strip(), description, price, effect, effect_value, duration
        )
        if record is None:
            await ctx.send_error(f"An item named **{name}** already exists.")
            return
        meta = {
            "emoji": emoji.strip() if emoji else None,
            "rarity": rarity,
            "stock": stock,
            "max_per_user": limit,
            "required_role_id": required_role.id if required_role and not required_role.is_default() else None,
            "available_until": until,
            "remove_role_id": remove_role.id if remove_role and not remove_role.is_default() else None,
            "reply_message": reply_message,
        }
        record = await self.bot.db.economy.update_item(ctx.guild.id, record["id"], meta) or record

        effect_line = self._effect_line(ctx.guild, record)
        await ctx.send_success(
            f"Added {item_emoji(record)} **{record['name']}** to the shop for {Emojis.Economy.cash} **{fnumb(price)}**."
            + (f"\n\N{SMALL BLUE DIAMOND} {effect_line}" if effect_line else "")
        )

    @shop.command(
        "edit",
        description="Change a shop item's price, look, stock or limits.",
        guild_only=True,
        user_permissions=PermissionTemplate.admin,
    )
    @describe(
        name="The item to edit.",
        new_name="Rename the item.",
        price="The new purchase price.",
        emoji="A new emoji (type 'none' to remove it).",
        rarity="The new rarity tier.",
        stock="Units left for sale; -1 makes it unlimited.",
        limit="Most one member may hold; 0 removes the limit.",
        required_role="Only members with this role may buy it; pick @everyone to open it to all.",
        available_for="Stay on sale for this long from now, e.g. '7d' ('none' = always).",
        remove_role="Role taken away on use; pick @everyone to stop removing one.",
        reply_message="Extra line shown on use ('none' removes it).",
        description="The new description (type 'none' to remove it).",
    )
    @app_commands.autocomplete(name=shop_item_autocomplete)  # type: ignore
    async def shop_edit(
        self,
        ctx: Context,
        name: str,
        new_name: str | None = None,
        price: commands.Range[int, 1] | None = None,
        emoji: str | None = None,
        rarity: RarityChoice | None = None,
        stock: commands.Range[int, -1, MAX_ITEM_STOCK] | None = None,
        limit: commands.Range[int, 0] | None = None,
        required_role: discord.Role | None = None,
        available_for: str | None = None,
        remove_role: discord.Role | None = None,
        reply_message: str | None = None,
        *,
        description: str | None = None,
    ) -> None:
        """Edit a shop item in place — owned copies keep working, unlike remove + add."""
        assert ctx.guild is not None
        item = await self.bot.db.economy.get_item(ctx.guild.id, name)
        if item is None:
            await ctx.send_error(f"No item named **{name}** exists.")
            return
        if error := builtin_error(item, "edit"):
            await ctx.send_error(error)
            return

        values: dict[str, Any] = {}
        if new_name is not None:
            values["name"] = new_name.strip()
        if price is not None:
            values["price"] = price
        if emoji is not None:
            if emoji.lower() != "none" and not self._valid_emoji(emoji):
                await ctx.send_error("That doesn't look like an emoji.")
                return
            values["emoji"] = None if emoji.lower() == "none" else emoji.strip()
        if rarity is not None:
            values["rarity"] = rarity
        if stock is not None:
            values["stock"] = None if stock < 0 else stock
        if limit is not None:
            values["max_per_user"] = limit or None
        if required_role is not None:
            values["required_role_id"] = None if required_role.is_default() else required_role.id
        if available_for is not None:
            try:
                values["available_until"] = None if available_for.lower() == "none" else self._parse_until(available_for)
            except commands.BadArgument:
                await ctx.send_error('Could not parse `available_for`. Try "7d", "2 weeks" or "none".')
                return
        if remove_role is not None:
            values["remove_role_id"] = None if remove_role.is_default() else remove_role.id
        if reply_message is not None:
            values["reply_message"] = None if reply_message.lower() == "none" else reply_message
        if description is not None:
            values["description"] = None if description.lower() == "none" else description

        if not values:
            await ctx.send_error("Nothing to change — pass at least one field.")
            return
        if error := (name_reserved_error(values["name"]) if "name" in values else None) or validate_item_meta(
            values.get("name"), rarity=values.get("rarity"), reply_message=values.get("reply_message")
        ):
            await ctx.send_error(error)
            return

        record = await self.bot.db.economy.update_item(ctx.guild.id, item["id"], values)
        if record is None:
            await ctx.send_error(f"An item named **{values.get('name')}** already exists.")
            return
        changed = ", ".join(f"`{key}`" for key in values)
        await ctx.send_success(f"Updated {item_emoji(record)} **{record['name']}** ({changed}).")

    @shop.command(
        "remove",
        aliases=["delete", "rm"],
        description="Remove an item from the shop.",
        guild_only=True,
        user_permissions=PermissionTemplate.admin,
    )
    @describe(name="The item to remove.")
    @app_commands.autocomplete(name=shop_item_autocomplete)  # type: ignore
    async def shop_remove(self, ctx: Context, *, name: str) -> None:
        """Remove an item from the server shop (also clears it from inventories)."""
        assert ctx.guild is not None
        item = await self.bot.db.economy.get_item(ctx.guild.id, name)
        if item is None:
            await ctx.send_error(f"No item named **{name}** exists.")
            return
        if error := builtin_error(item, "remove"):
            await ctx.send_error(error)
            return
        confirm = await ctx.confirm(
            f"Remove **{item['name']}** from the shop? Every copy members own is deleted too. "
            f"To just stop selling it, use `shop edit {item['name']} stock:0` instead."
        )
        if not confirm:
            await ctx.send_info("Nothing was removed.")
            return
        await self.bot.db.economy.delete_item(ctx.guild.id, item["name"])
        await ctx.send_success(f"Removed **{item['name']}** from the shop.")

    @command("buy", description="Buy an item from the shop.", guild_only=True, hybrid=True, extras={"example": "buy Lucky Charm 2"})
    @describe(name="The item to buy.", quantity="How many to buy.")
    @app_commands.autocomplete(name=shop_item_autocomplete)  # type: ignore
    async def buy(self, ctx: Context, name: str, quantity: commands.Range[int, 1] = 1) -> None:
        """Buy an item from the shop with your cash."""
        assert ctx.guild is not None and isinstance(ctx.author, discord.Member)
        item = await self.bot.db.economy.get_item(ctx.guild.id, name)
        if item is None:
            await ctx.send_error(f"No item named **{name}** exists in the shop.")
            return
        await self._send_result(ctx, await self.buy_item_action(ctx.author, item["id"], quantity))

    @command("sell", description="Sell an item back to the shop.", guild_only=True, hybrid=True, extras={"example": "sell Lucky Charm 1"})
    @describe(name="The item to sell.", quantity="How many to sell.")
    @app_commands.autocomplete(name=owned_item_autocomplete)  # type: ignore
    async def sell(self, ctx: Context, name: str, quantity: commands.Range[int, 1] = 1) -> None:
        """Sell an item from your inventory back for half its price."""
        assert ctx.guild is not None and isinstance(ctx.author, discord.Member)
        item = await self.bot.db.economy.get_item(ctx.guild.id, name)
        if item is None:
            await ctx.send_error(f"No item named **{name}** exists in the shop.")
            return
        await self._send_result(ctx, await self.sell_item_action(ctx.author, item["id"], quantity))

    @command("inventory", aliases=["inv"], description="Show your or another member's items.", guild_only=True, hybrid=True, extras={"example": "inventory @member"})
    @describe(member="The member whose inventory to show.")
    async def inventory(
        self, ctx: Context, member: Annotated[discord.Member | None, converter.MemberConverter] = None
    ) -> None:
        """Your items in one card: filter, open an item, then use, sell or gift it.

        Viewing someone else's inventory is read-only.
        """
        assert isinstance(ctx.author, discord.Member)
        await self._send_browser(ctx, ItemBrowser(self, ctx.author, mode='inventory', target=member))

    @command("use", description="Use an item from your inventory.", guild_only=True, hybrid=True, extras={"example": "use Lootbox"}, bot_permissions=[discord.Permissions.manage_roles])
    @describe(name="The item to use.", quantity="How many to use (vouchers and lootboxes only).")
    @app_commands.autocomplete(name=owned_item_autocomplete)  # type: ignore
    async def use(self, ctx: Context, name: str, quantity: commands.Range[int, 1, 100] = 1) -> None:
        """Use (consume) an item you own — what happens depends on the item's effect.

        Vouchers redeem for cash, lootboxes roll a random payout, role items grant
        their role, and boost items activate a timed XP or loot multiplier. Only
        vouchers and lootboxes can be used several at once.
        """
        assert ctx.guild is not None and isinstance(ctx.author, discord.Member)
        item = await self.bot.db.economy.get_item(ctx.guild.id, name)
        if item is None:
            await ctx.send_error(f"No item named **{name}** exists in the shop.")
            return
        await self._send_result(ctx, await self.use_item_action(ctx.author, item["id"], quantity))

    @command("trade", description="Trade items and cash with another member.", guild_only=True, hybrid=True, extras={"example": "trade @member"})
    @describe(member="Who to trade with.")
    async def trade(self, ctx: Context, member: Annotated[discord.Member, converter.MemberConverter]) -> None:
        """Open a two-sided trade: both of you add items or cash, both confirm, then it happens at once."""
        assert isinstance(ctx.author, discord.Member)
        if error := trade_error(ctx.author.id, member.id, partner_is_bot=member.bot):
            await ctx.send_error(error)
            return
        view = TradeView(self, ctx.author, member)
        await view.build()
        view.message = await ctx.send(view=view, allowed_mentions=discord.AllowedMentions(users=[member]))

    # Text-only group: the bot sits at the 99/100 global slash-command budget, so no new slash slot.
    @group("market", fallback="browse", description="Buy and sell items with other members.", guild_only=True)
    async def market(self, ctx: Context, *, item: str | None = None) -> None:
        """Browse open listings, optionally for one item. Sales are taxed 5% (paid by the seller)."""
        assert ctx.guild is not None
        item_id = None
        if item:
            record = await self.bot.db.economy.get_item(ctx.guild.id, item)
            if record is None:
                await ctx.send_error(f"No item named **{item}** exists.")
                return
            item_id = record["id"]
        rows = await self.bot.db.economy.market_listings(ctx.guild.id, item_id=item_id)
        await self._send_listings(ctx, rows, "Player Market", "Nothing is listed right now. Be the first with `market sell`.")

    @market.command("mine", description="Show your own open listings.", guild_only=True)
    async def market_mine(self, ctx: Context) -> None:
        """Your open listings; cancel one with `market cancel <id>`."""
        assert ctx.guild is not None
        rows = await self.bot.db.economy.market_listings(ctx.guild.id, seller_id=ctx.author.id)
        await self._send_listings(ctx, rows, "Your Listings", "You have no open listings.")

    async def _send_listings(self, ctx: Context, rows: list[Any], title: str, empty: str) -> None:
        if not rows:
            await ctx.send_info(empty)
            return
        entries = [
            f"`#{r['id']}` {item_emoji(r)} **{r['name']}** ×{fnumb(r['quantity'])} • "
            f"{Emojis.Economy.cash} **{fnumb(r['price_each'])}** each • <@{r['seller_id']}>"
            for r in rows
        ]
        embed = discord.Embed(title=title, colour=helpers.Colour.white())
        embed.set_footer(text=f"{MARKET_TAX:.0%} tax is paid by the seller • buy with market buy <id> [quantity]")
        await LinePaginator.start(ctx, entries=entries, embed=embed, location='description')

    @market.command("sell", description="List items for sale; they are held until sold or cancelled.", guild_only=True)
    @describe(item="The item to list.", quantity="How many to list.", price_each="Asking price per item.")
    async def market_sell(self, ctx: Context, item: str, quantity: commands.Range[int, 1], price_each: commands.Range[int, 1]) -> None:
        """List items on the market. They leave your inventory until bought or cancelled."""
        assert ctx.guild is not None
        record = await self.bot.db.economy.get_item(ctx.guild.id, item)
        if record is None:
            await ctx.send_error(f"No item named **{item}** exists.")
            return
        db = self.bot.db.economy
        owned = await db.get_quantity(ctx.author.id, ctx.guild.id, record["id"])
        open_listings = await db.market_count(ctx.guild.id, ctx.author.id)
        if error := market_error(quantity=quantity, price_each=price_each, owned=owned, open_listings=open_listings):
            await ctx.send_error(error)
            return
        listing = await db.market_list(ctx.guild.id, ctx.author.id, record["id"], quantity, price_each, MAX_LISTINGS)
        if listing == 'items':
            await ctx.send_error("You no longer own that many.")
        elif listing == 'listings':
            await ctx.send_error(f"You can only have {MAX_LISTINGS} listings open at a time.")
        else:
            assert not isinstance(listing, str)
            await ctx.send_success(
                f"Listed **{quantity}× {item_emoji(record)} {record['name']}** at {Emojis.Economy.cash} "
                f"**{fnumb(price_each)}** each as `#{listing['id']}`."
            )

    @market.command("buy", description="Buy from a listing (partial buys allowed).", guild_only=True)
    @describe(listing_id="The listing number.", quantity="How many to buy (default: all of it).")
    async def market_buy(self, ctx: Context, listing_id: commands.Range[int, 1], quantity: commands.Range[int, 1] | None = None) -> None:
        """Buy some or all of a listing with your cash."""
        assert ctx.guild is not None
        db = self.bot.db.economy
        status, info = await db.market_buy(ctx.guild.id, ctx.author.id, listing_id, quantity, sale_proceeds)
        if status == 'missing':
            await ctx.send_error("That listing no longer exists.")
        elif status == 'own':
            await ctx.send_error("You cannot buy your own listing; use `market cancel` instead.")
        elif status == 'quantity':
            await ctx.send_error(f"Only **{info['available']}** left in that listing.")
        elif status == 'limit':
            await ctx.send_error("That would put you over the holding limit for this item.")
        elif status == 'cash':
            await ctx.send_error(f"You need {Emojis.Economy.cash} **{fnumb(info['gross'])}** in cash for that.")
        elif status != 'ok':
            await ctx.send_error("The sale could not be completed.")
        else:
            record = await db.get_item_by_id(ctx.guild.id, info['item_id'])
            name = f"{item_emoji(record)} {record['name']}" if record else 'items'
            await ctx.send_success(
                f"Bought **{info['quantity']}× {name}** for {Emojis.Economy.cash} **{fnumb(info['gross'])}**."
            )

    @market.command("cancel", description="Cancel one of your listings and get the items back.", guild_only=True)
    @describe(listing_id="The listing number.")
    async def market_cancel(self, ctx: Context, listing_id: commands.Range[int, 1]) -> None:
        """Take a listing down; the escrowed items return to your inventory."""
        assert ctx.guild is not None
        row = await self.bot.db.economy.market_cancel(ctx.guild.id, listing_id, ctx.author.id)
        if row is None:
            await ctx.send_error("You have no listing with that number.")
            return
        record = await self.bot.db.economy.get_item_by_id(ctx.guild.id, row['item_id'])
        name = record['name'] if record else 'items'
        await ctx.send_success(f"Cancelled `#{listing_id}`: **{row['quantity']}× {name}** returned to your inventory.")

    # -- earning activities ----------------------------------------------

    @command("fish", description="Cast a line and earn a random catch.", guild_only=True)
    @cooldown(1, FISHING_COOLDOWN)
    async def fish(self, ctx: Context) -> None:
        """Go fishing for a chance at cash — outcomes range from junk to a rare pearl."""
        assert ctx.guild is not None
        catch = roll_loot(FISHING_TABLE)
        boost = await self.bot.db.economy.get_boost_multiplier(ctx.author.id, ctx.guild.id, "loot")
        scale = await self._payout_scale(ctx.author.id, ctx.guild.id)
        factor, extra = await self._gear(ctx.author.id, ctx.guild.id, "fish")
        amount = round(catch.amount * boost * scale * factor)
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        await balance.add(cash=amount)

        if catch.amount <= 5:
            await ctx.send_info(f"{catch.emoji} You reeled in **{catch.name}** — barely worth it.{extra}")
        else:
            suffix = f" *(+{boost - 1.0:.0%} loot boost)*" if boost > 1.0 else ""
            await ctx.send_success(
                f"{catch.emoji} You caught **{catch.name}** and sold it for "
                f"{Emojis.Economy.cash} **{fnumb(amount)}**!{suffix}{self._scale_suffix(scale)}{extra}"
            )
        await self._bump_quests(ctx, "fish")

    @command("hunt", description="Head out hunting for a bigger, riskier payout.", guild_only=True)
    @cooldown(1, HUNTING_COOLDOWN)
    async def hunt(self, ctx: Context) -> None:
        """Go hunting — higher payouts and variance than fishing, on a longer cooldown."""
        assert ctx.guild is not None
        catch = roll_loot(HUNTING_TABLE)
        boost = await self.bot.db.economy.get_boost_multiplier(ctx.author.id, ctx.guild.id, "loot")
        scale = await self._payout_scale(ctx.author.id, ctx.guild.id)
        factor, extra = await self._gear(ctx.author.id, ctx.guild.id, "hunt")
        amount = round(catch.amount * boost * scale * factor)
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        await balance.add(cash=amount)
        if roll_death('hunt'):
            extra += await resolve_death(self.bot, ctx.author.id, ctx.guild.id)

        if catch.amount <= 10:
            await ctx.send_info(f"{catch.emoji} You found **{catch.name}** and came back empty-handed.{extra}")
        else:
            suffix = f" *(+{boost - 1.0:.0%} loot boost)*" if boost > 1.0 else ""
            await ctx.send_success(
                f"{catch.emoji} You bagged **{catch.name}** worth "
                f"{Emojis.Economy.cash} **{fnumb(amount)}**!{suffix}{self._scale_suffix(scale)}{extra}"
            )
        await self._bump_quests(ctx, "hunt")

    @command("beg", description="Beg for a little spare change.", guild_only=True)
    @cooldown(1, BEG_COOLDOWN)
    async def beg(self, ctx: Context) -> None:
        """Beg passers-by for spare change — quick, low stakes, occasionally lucrative."""
        assert ctx.guild is not None
        catch = roll_loot(BEG_TABLE)
        if catch.amount <= 0:
            await ctx.send_info(f'{catch.emoji} {random.choice(BEG_FAIL_LINES)}')
        else:
            scale = await self._payout_scale(ctx.author.id, ctx.guild.id)
            amount = round(catch.amount * scale)
            balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
            await balance.add(cash=amount)
            line = random.choice(BEG_SUCCESS_LINES).format(
                donor=f'**{random.choice(BEG_DONORS).title()}**',
                coins=f'{Emojis.Economy.cash} **{fnumb(amount)}**',
            )
            await ctx.send_success(f'{catch.emoji} {line}{self._scale_suffix(scale)}')
        await self._bump_quests(ctx, "beg")

    @command("dig", description="Dig around for buried valuables.", guild_only=True)
    @cooldown(1, DIG_COOLDOWN)
    async def dig(self, ctx: Context) -> None:
        """Grab a shovel and dig — finds range from bottle caps to ancient relics."""
        assert ctx.guild is not None
        catch = roll_loot(DIG_TABLE)
        boost = await self.bot.db.economy.get_boost_multiplier(ctx.author.id, ctx.guild.id, "loot")
        scale = await self._payout_scale(ctx.author.id, ctx.guild.id)
        factor, extra = await self._gear(ctx.author.id, ctx.guild.id, "dig")
        amount = round(catch.amount * boost * scale * factor)
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        await balance.add(cash=amount)

        if catch.amount <= 8:
            await ctx.send_info(f"{catch.emoji} You dug up **{catch.name}** — hardly a treasure.{extra}")
        else:
            suffix = f" *(+{boost - 1.0:.0%} loot boost)*" if boost > 1.0 else ""
            await ctx.send_success(
                f"{catch.emoji} You unearthed **{catch.name}** worth "
                f"{Emojis.Economy.cash} **{fnumb(amount)}**!{suffix}{self._scale_suffix(scale)}{extra}"
            )
        await self._bump_quests(ctx, "dig")

    @command("search", description="Search one of three random spots for cash.", guild_only=True)
    @cooldown(1, SEARCH_COOLDOWN)
    async def search(self, ctx: Context) -> None:
        """Pick one of three random locations to rummage through.

        Safer spots pay less; the risky ones pay more but can go wrong and cost
        you cash instead.
        """
        assert ctx.guild is not None
        guild_id = ctx.guild.id
        options = pick_search_options()

        async def resolve(interaction: discord.Interaction, location: SearchLocation) -> None:
            outcome = resolve_search(location)
            balance = await ctx.db.get_user_balance(ctx.author.id, guild_id)
            if outcome.injured:
                fine = await self.bot.db.economy.fine_cash(ctx.author.id, guild_id, outcome.fine)
                text = (
                    f'{location.emoji} **{location.name}** — {outcome.flavor}\n'
                    f'You lost {Emojis.Economy.cash} **{fnumb(fine)}**.'
                )
                if roll_death('search'):
                    text += await resolve_death(self.bot, ctx.author.id, guild_id)
            else:
                assert outcome.catch is not None
                scale = await self._payout_scale(ctx.author.id, guild_id)
                amount = round(outcome.catch.amount * scale)
                await balance.add(cash=amount)
                text = (
                    f'{location.emoji} **{location.name}** — you found {outcome.catch.emoji} '
                    f'**{outcome.catch.name}** worth {Emojis.Economy.cash} **{fnumb(amount)}**!'
                    f'{self._scale_suffix(scale)}'
                )
            await interaction.response.edit_message(content=text, view=view)
            await self._bump_quests(ctx, "search")

        view = SearchView(ctx.author, options, resolve)
        view.message = await ctx.send("\N{RIGHT-POINTING MAGNIFYING GLASS} **Where do you want to search?**", view=view)

    @command("postmemes", description="Post a meme online and hope it goes viral.", guild_only=True)
    @cooldown(1, POSTMEME_COOLDOWN)
    async def postmemes(self, ctx: Context) -> None:
        """Pick one of three meme formats to post.

        Safe formats earn steady change; edgy ones go viral more often but can
        flop and cost you instead.
        """
        assert ctx.guild is not None
        guild_id = ctx.guild.id

        async def resolve(interaction: discord.Interaction, meme: SearchLocation) -> None:
            outcome = resolve_search(meme)
            balance = await ctx.db.get_user_balance(ctx.author.id, guild_id)
            if outcome.injured:
                fine = await self.bot.db.economy.fine_cash(ctx.author.id, guild_id, outcome.fine)
                text = (
                    f'{meme.emoji} **{meme.name}** flopped — {outcome.flavor}\n'
                    f'You lost {Emojis.Economy.cash} **{fnumb(fine)}**.'
                )
            else:
                assert outcome.catch is not None
                scale = await self._payout_scale(ctx.author.id, guild_id)
                amount = round(outcome.catch.amount * scale)
                await balance.add(cash=amount)
                text = (
                    f'{meme.emoji} Your **{meme.name}** earned {outcome.catch.emoji} **{outcome.catch.name}** — '
                    f'{Emojis.Economy.cash} **{fnumb(amount)}** in ad revenue!{self._scale_suffix(scale)}'
                )
            await interaction.response.edit_message(content=text, view=view)
            await self._bump_quests(ctx, 'postmeme')

        view = SearchView(
            ctx.author, pick_search_options(locations=MEME_FORMATS), resolve,
            timeout_text='You overthought the caption and never posted.',
        )
        view.message = await ctx.send("\N{MOBILE PHONE} **What kind of meme are you posting?**", view=view)

    @command("work", description="Work a shift at your job.", guild_only=True, extras={"example": "work"})
    @cooldown(1, WORK_COOLDOWN, commands.BucketType.member)
    async def work(self, ctx: Context) -> None:
        """Work a shift at your current job.

        Everyone starts as a **Freelancer**; shifts accumulate and unlock better
        jobs on the career ladder — see `job list` and `job apply`. Shifts can
        roll random events: overtime, tips, or a costly mishap.
        """
        assert ctx.guild is not None
        row = await self.bot.db.economy.get_job(ctx.author.id, ctx.guild.id)
        job = get_job(row["job_id"] if row else None)

        result = compute_shift(job)
        challenge = generate_challenge()
        view = WorkChallengeView(ctx.author, challenge)
        if challenge.kind == 'memory':
            header = f"{job.emoji} **Memorise the order!**\n{challenge.prompt}"
            view.message = await ctx.send(header)
            await asyncio.sleep(3)
            view.message = await view.message.edit(content=f"{job.emoji} **Which order did you see?**", view=view)
        else:
            view.message = await ctx.send(f"{job.emoji} **Quick task before you clock in!**\n{challenge.prompt}", view=view)
        await view.wait()
        factor = WORK_SUCCESS_BONUS if view.correct else WORK_FAIL_FACTOR
        if view.correct:
            outcome = f"\N{WHITE HEAVY CHECK MARK} Task done right: **+{WORK_SUCCESS_BONUS - 1:.0%}** pay."
        elif view.correct is None:
            outcome = f"\N{ALARM CLOCK} You ran out of time: pay is **{WORK_FAIL_FACTOR:.0%}**."
        else:
            outcome = f"\N{CROSS MARK} Wrong answer: pay is **{WORK_FAIL_FACTOR:.0%}**."

        scale = await self._payout_scale(ctx.author.id, ctx.guild.id)
        amount = round(result.amount * scale * factor)
        shifts = await self.bot.db.economy.add_shift(ctx.author.id, ctx.guild.id, job.id)

        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        await balance.add(cash=amount, bank_space=roll_bank_space())

        message = (
            f"{job.emoji} You worked a shift as **{job.name}** and earned "
            f"{Emojis.Economy.cash} **{fnumb(amount)}**.{self._scale_suffix(scale)}"
        )
        message += f"\n{outcome}"
        if result.event.id != "normal":
            message += f"\n{result.event.flavor}"
        newly_unlocked = [j for j in JOB_LADDER if j.shifts_required == shifts]
        if newly_unlocked:
            unlocked = newly_unlocked[0]
            message += (
                f"\n\N{CHART WITH UPWARDS TREND} **{unlocked.name}** is now available to you — "
                f"apply with `job apply {unlocked.id}`!"
            )
        await ctx.send_success(message)
        await self._bump_quests(ctx, "work")
        await self._sync_achievements(ctx)

    @group("job", fallback="info", description="View and manage your job.", guild_only=True, hybrid=True)
    async def job(self, ctx: Context) -> None:
        """Show your current job, lifetime shifts and progress to the next rung; switch jobs from the menu."""
        await self._open_hub(ctx, 'job')

    @job.command("list", description="Show the career ladder.", guild_only=True)
    async def job_list(self, ctx: Context) -> None:
        """List every job with its pay range and unlock requirement."""
        assert ctx.guild is not None
        row = await self.bot.db.economy.get_job(ctx.author.id, ctx.guild.id)
        shifts = row["shifts"] if row else 0
        current_id = row["job_id"] if row else JOB_LADDER[0].id

        lines = []
        for ladder_job in JOB_LADDER:
            unlocked = ladder_job.shifts_required <= shifts
            marker = "\N{BRIEFCASE}" if ladder_job.id == current_id else ("\N{WHITE HEAVY CHECK MARK}" if unlocked else "\N{LOCK}")
            lines.append(
                f"{marker} {ladder_job.emoji} **{ladder_job.name}** — "
                f"{Emojis.Economy.cash} {fnumb(ladder_job.pay_min)}-{fnumb(ladder_job.pay_max)}/shift "
                f"*(needs {ladder_job.shifts_required} shifts)*"
            )
        await ctx.send(view=make_notice(
            "Career Ladder", "\n".join(lines),
            footer=f"You have worked {fnumb(shifts)} shifts. Apply with `job apply <name>`.",
        ))

    async def job_apply_action(self, member: discord.Member, name: str) -> tuple[bool, str]:
        """Switch ``member`` to the job called ``name`` (id or name) if unlocked; returns ``(ok, message)``."""
        wanted = name.strip().lower()
        target = next((j for j in JOB_LADDER if j.id == wanted or j.name.lower() == wanted), None)
        if target is None:
            return False, f"No job named **{name}** exists. See `job list`."

        row = await self.bot.db.economy.get_job(member.id, member.guild.id)
        shifts = row["shifts"] if row else 0
        if row and row["job_id"] == target.id:
            return False, f"You already work as **{target.name}**."
        if target.shifts_required > shifts:
            return False, f"**{target.name}** requires **{target.shifts_required}** lifetime shifts — you have **{shifts}**."

        await self.bot.db.economy.set_job(member.id, member.guild.id, target.id)
        message = (
            f"{target.emoji} You are now employed as **{target.name}** "
            f"({Emojis.Economy.cash} {fnumb(target.pay_min)}-{fnumb(target.pay_max)} per shift)."
        )
        if unlocked := await self._award_achievements(member.id, member.guild.id):
            message += f"\n\N{TROPHY} {unlocked}"
        return True, message

    @job.command("apply", description="Apply for a job on the ladder.", guild_only=True)
    @describe(name="The job to apply for.")
    @app_commands.autocomplete(name=job_autocomplete)  # type: ignore
    async def job_apply(self, ctx: Context, *, name: str) -> None:
        """Apply for a job you've unlocked with lifetime shifts."""
        assert isinstance(ctx.author, discord.Member)
        await self._send_result(ctx, await self.job_apply_action(ctx.author, name))

    @job.command("quit", description="Quit your job.", guild_only=True)
    async def job_quit(self, ctx: Context) -> None:
        """Quit back down to Freelancer (lifetime shifts are kept)."""
        assert ctx.guild is not None
        row = await self.bot.db.economy.get_job(ctx.author.id, ctx.guild.id)
        if row is None or row["job_id"] == JOB_LADDER[0].id:
            await ctx.send_error("You don't have a job to quit.")
            return
        old = get_job(row["job_id"])
        await self.bot.db.economy.set_job(ctx.author.id, ctx.guild.id, JOB_LADDER[0].id)
        await ctx.send_success(f"You quit your job as **{old.name}**. Back to freelancing.")

    # -- lottery ----------------------------------------------------------

    @group("lottery", fallback="status", description="View the server lottery.", guild_only=True, hybrid=True)
    async def lottery(self, ctx: Context) -> None:
        """Show the current lottery: jackpot, ticket price, time left and your tickets."""
        assert ctx.guild is not None
        record = await self.bot.db.economy.get_lottery(ctx.guild.id)
        if record is None:
            await ctx.send_info("No lottery is running. An admin can start one with `lottery start`.")
            return

        entries = await self.bot.db.economy.get_lottery_entries(ctx.guild.id)
        total_tickets = sum(e["tickets"] for e in entries)
        yours = await self.bot.db.economy.get_lottery_tickets(ctx.guild.id, ctx.author.id)
        ends_at = record["ends_at"].replace(tzinfo=datetime.UTC)
        odds = f"{yours / total_tickets:.1%}" if total_tickets else "0%"

        view = make_notice(
            "Server Lottery",
            "The pot grows with every ticket sold.\n"
            "You'll need to have the Ticket Price in cash in order to buy a ticket.\n"
            "Buy yourself in with `lottery buy <amount>`.",
            accent=Accent.info,
            thumbnail=get_asset_url(ctx.guild),
            fields=[
                ("Jackpot", f"{Emojis.Economy.cash} **{fnumb(record['jackpot'])}**"),
                ("Ticket Price", f"{Emojis.Economy.cash} **{fnumb(record['ticket_price'])}**"),
                ("Tickets Sold", f"**{fnumb(total_tickets)}**"),
                ("Your Tickets", f"**{fnumb(yours)}** ({odds} chance)"),
                ("Drawing", discord.utils.format_dt(ends_at, "R")),
            ],
        )
        await ctx.send(view=view, ephemeral=True)

    @lottery.command(
        "start",
        description="Start a server lottery.",
        guild_only=True,
        user_permissions=PermissionTemplate.admin,
    )
    @describe(duration='How long entries stay open, e.g. "2h" or "1 day".', ticket_price="Cost per ticket.")
    async def lottery_start(self, ctx: Context, duration: str, ticket_price: Range[int, 1, 1_000_000_000]) -> None:
        """Start a lottery that draws a weighted-random winner when the timer ends."""
        assert ctx.guild is not None
        try:
            when = timetools.FutureTime(duration).dt
        except commands.BadArgument:
            await ctx.send_error('Could not parse that duration. Try something like "2h" or "1 day".')
            return

        now = ctx.message.created_at
        if when - now < datetime.timedelta(minutes=5):
            await ctx.send_error("The lottery must run for at least **5 minutes**.")
            return
        if when - now > datetime.timedelta(days=30):
            await ctx.send_error("The lottery can run for at most **30 days**.")
            return

        record = await self.bot.db.economy.create_lottery(
            ctx.guild.id, ctx.channel.id, int(ticket_price), int(ticket_price), when
        )
        if record is None:
            await ctx.send_error("A lottery is already running. End it before starting another.")
            return

        # ends_at identifies this lottery: a timer left over from a deleted one must not end its successor.
        await self.bot.timers.create(when, "lottery", ctx.guild.id, record["ends_at"].isoformat())
        await ctx.send_success(
            f"Lottery started! Tickets are {Emojis.Economy.cash} **{fnumb(int(ticket_price))}** each. "
            f"Drawing {discord.utils.format_dt(when, 'R')}."
        )

        view = make_notice(
            "Server Lottery",
            "## There has been a lottery started for this server!\n"
            "-# Enter to participate and grab the chance to earn a fortune!\n\n"
            "The pot grows with every ticket sold.\n"
            "You'll need to have the Ticket Price in cash in order to buy a ticket.\n"
            "Buy yourself in with `lottery buy <amount>`.",
            accent=Accent.info,
            thumbnail=get_asset_url(ctx.guild),
            fields=[
                ("Jackpot", f"{Emojis.Economy.cash} **{fnumb(record['jackpot'])}**"),
                ("Ticket Price", f"{Emojis.Economy.cash} **{fnumb(record['ticket_price'])}**"),
                ("Drawing", discord.utils.format_dt(when, "R")),
            ],
        )
        await ctx.send(view=view)

    @lottery.command("buy", description="Buy lottery tickets.", guild_only=True, hybrid=True)
    @describe(amount="How many tickets to buy.")
    async def lottery_buy(self, ctx: Context, amount: Range[int, 1, 1000] = 1) -> None:
        """Buy tickets for the active lottery; more tickets mean better odds."""
        assert ctx.guild is not None
        record = await self.bot.db.economy.get_lottery(ctx.guild.id)
        if record is None:
            await ctx.send_error("No lottery is running right now.")
            return

        cost = record["ticket_price"] * amount
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        if await self.bot.db.economy.debit_cash(ctx.author.id, ctx.guild.id, cost) is None:
            await ctx.send_error(
                f"You need {Emojis.Economy.cash} **{fnumb(cost)}** but only have **{fnumb(balance.cash)}**."
            )
            return

        total = await self.bot.db.economy.add_lottery_tickets(ctx.guild.id, ctx.author.id, amount, cost)
        if total is None:  # the lottery ended between the check and the purchase
            await balance.add(cash=cost)
            await ctx.send_error("The lottery just ended - you were not charged.")
            return
        await ctx.send_success(
            f"Bought **{pluralize(amount):ticket}** for {Emojis.Economy.cash} **{fnumb(cost)}**. "
            f"You now hold **{fnumb(total)}**."
        )

    @Cog.listener()
    async def on_game_result(self, guild_id: int, user_id: int, game: str, result: str) -> None:
        """Advance game quests from a recorded round (silent: there is no ctx to announce on)."""
        kind = None
        if game == 'heist':
            kind = 'heist'
        elif result == 'win':
            if game == 'fight':
                kind = 'fight_win'
            elif game in _CASINO_QUEST_GAMES:
                kind = 'casino_win'
        if kind is not None:
            await self._advance_quests(user_id, guild_id, kind)

    @Cog.listener()
    async def on_lottery_timer_complete(self, timer: Timer) -> None:
        guild_id: int = timer.args[0]
        record = await self.bot.db.economy.get_lottery(guild_id)
        if record is None:
            return
        if len(timer.args) > 1 and timer.args[1] != record["ends_at"].isoformat():
            return  # stale timer from an earlier lottery

        entries = await self.bot.db.economy.get_lottery_entries(guild_id)
        jackpot: int = record["jackpot"]
        channel = self.bot.get_channel(record["channel_id"])
        await self.bot.db.economy.delete_lottery(guild_id)

        winner_id = pick_weighted_winner([(e["user_id"], e["tickets"]) for e in entries])
        if winner_id is not None and jackpot > 0:
            # Pay first: a deleted announcement channel must not burn the jackpot.
            balance = await self.bot.db.get_user_balance(winner_id, guild_id)
            await balance.add(cash=jackpot)
        if not isinstance(channel, discord.abc.Messageable):
            return
        if winner_id is None or jackpot <= 0:
            view = make_notice(
                "Lottery Ended",
                "The lottery ended with no tickets sold. Better luck next time!",
                accent=Accent.warning,
            )
            await channel.send(view=view)
            return

        # The mention lives inside the container's text display, which still pings.
        view = make_notice(
            "\N{PARTY POPPER} Lottery Winner!",
            f"<@{winner_id}> won the jackpot of {Emojis.Economy.cash} **{fnumb(jackpot)}**!",
            accent=Accent.success,
        )
        await channel.send(view=view, allowed_mentions=discord.AllowedMentions(users=True))

    @command("perks", description="View your active item perks and boosts.", guild_only=True, hybrid=True)
    @describe(member="The member whose perks to view.")
    async def perks(self, ctx: Context, member: Annotated[discord.Member | None, converter.MemberConverter] = None) -> None:
        """View active boosts and perks for yourself or another member."""
        await self._open_hub(ctx, "perks", member)

    # -- gifting ------------------------------------------------------------

    @command("passive", description="Opt out of robbing and heists — both ways.", guild_only=True, hybrid=True)
    @describe(enabled="Turn passive mode on or off; leave empty to see your status.")
    async def passive(self, ctx: Context, enabled: bool | None = None) -> None:
        """Passive mode: nobody can rob you or heist your bank, and you can't rob or heist anyone.

        It can be toggled once every 24 hours, so it can't be flipped on right
        before someone robs you back.
        """
        assert ctx.guild is not None
        db = self.bot.db.economy
        row = await db.get_passive(ctx.author.id, ctx.guild.id)
        current = bool(row and row['enabled'])
        if enabled is None:
            state = 'on \N{DOVE OF PEACE}' if current else 'off'
            await ctx.send_info(f"Passive mode is **{state}**. Toggle it with `passive on` / `passive off`.")
            return
        if enabled == current:
            await ctx.send_info(f"Passive mode is already **{'on' if current else 'off'}**.")
            return
        last = row['changed_at'].replace(tzinfo=datetime.UTC) if row else None
        result = compute_periodic(last, now=ctx.message.created_at, cooldown=PASSIVE_COOLDOWN)
        if not result.claimed:
            assert result.next_available is not None
            await ctx.send_error(
                f"You can toggle passive mode again {discord.utils.format_dt(result.next_available, 'R')}."
            )
            return
        await db.set_passive(ctx.author.id, ctx.guild.id, enabled)
        if enabled:
            await ctx.send_success("\N{DOVE OF PEACE} Passive mode **on** — you can't rob or be robbed, and heists skip you.")
        else:
            await ctx.send_success("Passive mode **off** — robbing and heists are back on the table, both ways.")

    @command("cooldowns", alias="cd", description="See which economy and game commands are ready.", guild_only=True)
    async def cooldowns(self, ctx: Context) -> None:
        """Every Economy and Games command with a cooldown, plus your daily, weekly and monthly timers."""
        assert ctx.guild is not None
        now = ctx.message.created_at
        entries: list[tuple[str, float]] = []
        for cog_name in ('Economy', 'Games'):
            cog = self.bot.get_cog(cog_name)
            entries.extend(
                (cmd.qualified_name, cmd.get_cooldown_retry_after(ctx))
                for cmd in (cog.walk_commands() if cog else ())
                if cmd.cooldown is not None
            )

        record = await self.bot.db.economy.get_daily(ctx.author.id, ctx.guild.id)
        for name, column, span in (
            ('daily', 'last_claim', DAILY_COOLDOWN),
            ('weekly', 'last_weekly', WEEKLY_COOLDOWN),
            ('monthly', 'last_monthly', MONTHLY_COOLDOWN),
        ):
            last = record[column].replace(tzinfo=datetime.UTC) if record and record[column] else None
            entries.append((name, max(0.0, (last + span - now).total_seconds()) if last else 0.0))
        passive = await self.bot.db.economy.get_passive(ctx.author.id, ctx.guild.id)
        if passive:
            changed = passive['changed_at'].replace(tzinfo=datetime.UTC)
            entries.append(('passive', max(0.0, (changed + PASSIVE_COOLDOWN - now).total_seconds())))

        ready, waiting = split_cooldowns(entries)
        fields = [(
            '\N{WHITE HEAVY CHECK MARK} Ready',
            ', '.join(f'`{name}`' for name in ready) or 'Nothing right now.',
        )]
        if waiting:
            fields.append((
                '\N{HOURGLASS WITH FLOWING SAND} On cooldown',
                '\n'.join(
                    f'`{name}` {discord.utils.format_dt(now + datetime.timedelta(seconds=left), "R")}'
                    for name, left in waiting
                ),
            ))
        await ctx.send(view=make_notice(f"{ctx.author.display_name}'s Cooldowns", fields=fields))

    @command("multipliers", alias="multi", description="See what boosts your earnings.", guild_only=True)
    @describe(member="The member to look at (defaults to you).")
    async def multipliers(
        self, ctx: Context, member: Annotated[discord.Member | None, converter.MemberConverter] = None
    ) -> None:
        """Every factor behind your earnings: server payout, prestige, boosts and tools."""
        assert ctx.guild is not None
        user = member or ctx.author
        db = self.bot.db.economy
        settings = await self._settings(ctx.guild.id)
        prestige = prestige_multiplier(await db.get_prestige(user.id, ctx.guild.id))
        owned = {row['builtin_key']: row for row in await db.get_inventory(user.id, ctx.guild.id)}
        tools: list[tuple[str, int | None]] = []
        for activity in ('fish', 'hunt', 'dig'):
            tool = tool_for_activity(activity)
            assert tool is not None and tool.durability is not None
            row = owned.get(tool.key)
            tools.append((tool.name, tool_uses_left(row['quantity'], row['durability'], tool.durability) if row else None))
        lines = describe_multipliers(
            guild=settings.payout_multiplier,
            prestige=prestige,
            loot=await db.get_boost_multiplier(user.id, ctx.guild.id, 'loot'),
            xp=await db.get_boost_multiplier(user.id, ctx.guild.id, 'xp'),
            vote=await self.bot.db.votes.get_active_multiplier(user.id),
            tools=tools,
        )
        await ctx.send(view=make_notice(
            f"{user.display_name}'s Multipliers", '\n'.join(lines), thumbnail=get_asset_url(user)
        ))

    @command("collection", alias="book", description="See which item drops you have discovered.", guild_only=True)
    @describe(member="The member to look at (defaults to you).")
    async def collection(
        self, ctx: Context, member: Annotated[discord.Member | None, converter.MemberConverter] = None
    ) -> None:
        """Your collection book: every fish, hunt and dig drop, or ``???`` until you find it."""
        assert ctx.guild is not None
        user = member or ctx.author
        groups = collection_summary(await self.bot.db.economy.get_discoveries(user.id, ctx.guild.id))
        found = sum(g.found for g in groups)
        total = sum(g.total for g in groups)
        fields = [
            (f'{g.activity.title()} ({g.found}/{g.total})', '\n'.join(label for label, _ in g.entries))
            for g in groups
        ]
        await ctx.send(view=make_notice(
            f"{user.display_name}'s Collection", f'**{found}/{total}** drops discovered.',
            fields=fields, thumbnail=get_asset_url(user),
        ))

    @command("gift", description="Gift items from your inventory to another member.", guild_only=True, hybrid=True)
    @describe(member="The member to gift to.", name="The item to gift.", quantity="How many to gift.")
    @app_commands.autocomplete(name=owned_item_autocomplete)  # type: ignore
    async def gift(
        self,
        ctx: Context,
        member: Annotated[discord.Member, converter.MemberConverter],
        name: str,
        quantity: commands.Range[int, 1] = 1,
    ) -> None:
        """Gift items you own to another member (no cash involved)."""
        assert ctx.guild is not None and isinstance(ctx.author, discord.Member)
        item = await self.bot.db.economy.get_item(ctx.guild.id, name)
        if item is None:
            await ctx.send_error(f"No item named **{name}** exists in the shop.")
            return
        await self._send_result(ctx, await self.gift_item_action(ctx.author, member, item["id"], quantity))

    # -- prestige ------------------------------------------------------------

    @command("prestige", description="Reset your wealth for a permanent payout bonus.", guild_only=True)
    async def prestige(self, ctx: Context) -> None:
        """Prestige: trade your entire cash & bank balance for a permanent +10% payout bonus.

        Each level requires a higher net worth. The bonus applies to every
        earning activity (work, fish, hunt, beg, dig, search, daily/weekly/monthly).
        """
        assert ctx.guild is not None
        level = await self.bot.db.economy.get_prestige(ctx.author.id, ctx.guild.id)
        requirement = prestige_requirement(level)
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        # Deliberately cash + bank only (not item value): prestige resets exactly these two, so the requirement
        # must be something the member actually gives up; items are kept and would let them re-qualify instantly.

        if balance.total < requirement:
            await ctx.send_info(
                f"\N{GLOWING STAR} Prestige **{level}** — payout bonus **+{(prestige_multiplier(level) - 1) * 100:.0f}%**.\n"
                f"Next level requires a net worth of {Emojis.Economy.cash} **{fnumb(requirement)}** "
                f"({progress_bar(balance.total, requirement)} {fnumb(balance.total)}/{fnumb(requirement)})."
            )
            return

        confirm = await ctx.confirm(
            f"Prestige to level **{level + 1}**?\n"
            f"This **resets your cash and bank to 0** (inventory is kept) in exchange for a permanent "
            f"**+{PRESTIGE_STEP:.0%}** payout bonus."
        )
        if not confirm:
            await ctx.send_info("Prestige cancelled. Your fortune is safe.")
            return

        # A second prestige or a spend may have happened while the prompt was open; the repo re-checks atomically.
        new_level = await self.bot.db.economy.try_prestige(ctx.author.id, ctx.guild.id, level, requirement)
        if new_level is None:
            await ctx.send_error("Your balance or prestige changed while you were deciding. Try again.")
            return
        await ctx.send_success(
            f"\N{GLOWING STAR} You ascended to prestige **{new_level}**! "
            f"Permanent payout bonus: **+{(prestige_multiplier(new_level) - 1) * 100:.0f}%**."
        )
        await self._sync_achievements(ctx)

    # -- achievements ----------------------------------------------------------

    @command(
        "achievements", aliases=["badges"], description="View earned achievement badges.",
        guild_only=True, hybrid=True,
    )
    @describe(member="The member whose achievements to view.")
    async def achievements(
        self, ctx: Context, member: Annotated[discord.Member | None, converter.MemberConverter] = None
    ) -> None:
        """Show a member's achievement badges — earned and still locked."""
        if member is None or member.id == ctx.author.id:
            # Catch up on anything qualified but not yet awarded before displaying.
            await self._sync_achievements(ctx, announce=False)
        await self._open_hub(ctx, "achievements", member)

    # -- daily quests ------------------------------------------------------------

    @command("quests", aliases=["quest"], description="View your daily quest board.", guild_only=True, hybrid=True)
    async def quests(self, ctx: Context) -> None:
        """Show today's quests: three rotating tasks with cash rewards, reset daily (UTC)."""
        await self._open_hub(ctx, "quests")

    # -- profile -----------------------------------------------------------------

    # Text-only group: the bot sits at the 99/100 global slash-command budget, so no new slash slot.
    @group("profile", fallback="show", description="Show a member's economy profile.", guild_only=True)
    async def profile(
        self, ctx: Context, member: Annotated[discord.Member | None, converter.MemberConverter] = None
    ) -> None:
        """One card: net worth, rank, prestige, job, pet, achievements, title, best game and daily streak."""
        assert ctx.guild is not None
        if member and member.bot:
            await ctx.send_error("Bots have no profile.")
            return
        user = member or ctx.author
        db, gid = self.bot.db, ctx.guild.id
        if user.id == ctx.author.id:
            await self._sync_achievements(ctx, announce=False)
        balance = await db.get_user_balance(user.id, gid)
        everyone = await db.get_guild_balances(gid)
        worth = balance.total + await db.economy.inventory_value(user.id, gid)
        richer = sum(1 for other in everyone if other.user_id != user.id and other.total > balance.total)
        job_row = await db.economy.get_job(user.id, gid)
        job = get_job(job_row["job_id"] if job_row else None)
        pet_row = await db.economy.get_pet(user.id, gid)
        species = get_species(pet_row["species"]) if pet_row else None
        pet = f"{species.emoji if species else ''} {pet_row['name']}" + (f" ({species.name})" if species else "") if pet_row else "None"
        earned = len(await db.economy.get_achievements(user.id, gid))
        title = await db.economy.get_profile_title(user.id, gid)
        daily = await db.economy.get_daily(user.id, gid)
        totals = await db.game_stats.get_member_totals(gid, user.id)
        top = best_game(await db.game_stats.get_member_games(gid, user.id))
        best = f"{str(top['game']).replace('_', ' ').title()} ({fnumb(top['profit'])} profit)" if top else "None yet"
        cash = Emojis.Economy.cash
        await ctx.send(view=make_notice(
            f"{user.display_name}'s Profile" + (f" - {title}" if title else ""),
            f"{cash} **Net worth** {fnumb(worth)} (rank **#{richer + 1:,}** of {max(len(everyone), 1):,})\n"
            f"\N{GLOWING STAR} **Prestige** {await db.economy.get_prestige(user.id, gid)}\n"
            f"\N{BRIEFCASE} **Job** {job.name if job else 'Unemployed'}\n"
            f"\N{PAW PRINTS} **Pet** {pet}\n"
            f"\N{TROPHY} **Achievements** {earned}/{len(ACHIEVEMENTS)}\n"
            f"\N{VIDEO GAME} **Games** {fnumb(totals['played']) if totals else 0} played, best: {best}\n"
            f"\N{FIRE} **Daily streak** {daily['streak'] if daily else 0}",
            thumbnail=get_asset_url(user),
        ))

    @profile.command("title", description="Equip an earned achievement as your title; no argument clears it.", guild_only=True)
    async def profile_title(self, ctx: Context, *, achievement: str | None = None) -> None:
        """Equip the name of an earned achievement (by name or id) as your profile title."""
        assert ctx.guild is not None
        db = self.bot.db.economy
        if achievement is None:
            await db.set_profile_title(ctx.author.id, ctx.guild.id, None)
            await ctx.send_success("Title cleared.")
            return
        await self._sync_achievements(ctx, announce=False)
        owned = {row["achievement"] for row in await db.get_achievements(ctx.author.id, ctx.guild.id)}
        found = match_achievement(achievement, [a for a in ACHIEVEMENTS if a.id in owned])
        if found is None:
            await ctx.send_error(f"You haven't earned an achievement matching **{achievement}**.")
            return
        await db.set_profile_title(ctx.author.id, ctx.guild.id, found.name)
        await ctx.send_success(f"Title set to **{found.name}**.")

    # -- pets ----------------------------------------------------------------------

    @group("pet", fallback="info", description="Manage your pet companion.", guild_only=True, hybrid=True)
    async def pet(self, ctx: Context) -> None:
        """Show your pet: species, hunger and unclaimed earnings, with Feed and Claim buttons."""
        await self._open_hub(ctx, "pet")

    @pet.command("shop", description="Show the adoptable species.", guild_only=True)
    async def pet_shop(self, ctx: Context) -> None:
        """List every adoptable species with cost, earn rate and upkeep."""
        lines = [
            f"{species.emoji} **{species.name}** — {Emojis.Economy.cash} **{fnumb(species.cost)}**\n"
            f"\N{SMALL BLUE DIAMOND} Earns {fnumb(species.hourly_rate)}/h (stores {species.storage_hours}h), "
            f"feeding costs {fnumb(species.feed_cost)}"
            for species in PET_SPECIES
        ]
        await ctx.send(view=make_notice(
            "Pet Adoption Centre", "\n".join(lines),
            footer="Adopt with `pet adopt <species> [name]` — one pet per member.",
        ))

    @pet.command("adopt", description="Adopt a pet.", guild_only=True)
    @describe(species="The species to adopt.", name="Your pet's name (defaults to the species name).")
    async def pet_adopt(self, ctx: Context, species: PetSpeciesChoice, *, name: str | None = None) -> None:
        """Adopt a pet — it earns cash passively as long as you keep it fed."""
        assert ctx.guild is not None
        spec = get_species(species)
        assert spec is not None

        pet_name = (name or spec.name).strip()[:32]
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        if await self.bot.db.economy.debit_cash(ctx.author.id, ctx.guild.id, spec.cost) is None:
            await ctx.send_error(
                f"Adopting a {spec.name} costs {Emojis.Economy.cash} **{fnumb(spec.cost)}** — "
                f"you only have **{fnumb(balance.cash)}** in cash."
            )
            return

        record = await self.bot.db.economy.create_pet(ctx.author.id, ctx.guild.id, spec.id, pet_name)
        if record is None:
            await balance.add(cash=spec.cost)  # refund: the debit above already went through
            await ctx.send_error("You already have a pet. Abandon it first with `pet abandon` (how could you?).")
            return

        await ctx.send_success(
            f"{spec.emoji} You adopted **{pet_name}** the {spec.name}! "
            f"They earn {Emojis.Economy.cash} **{fnumb(spec.hourly_rate)}/h** while fed — collect with `pet claim`."
        )
        await self._sync_achievements(ctx)

    async def pet_feed_action(self, member: discord.Member) -> tuple[bool, str]:
        """Feed ``member``'s pet; returns ``(ok, message)``."""
        row = await self.bot.db.economy.get_pet(member.id, member.guild.id)
        if row is None:
            return False, "You don't have a pet to feed."
        species = get_species(row["species"])
        if species is None:
            return False, "Your pet's species no longer exists."

        balance = await self.bot.db.get_user_balance(member.id, member.guild.id)
        if await self.bot.db.economy.debit_cash(member.id, member.guild.id, species.feed_cost) is None:
            return False, (
                f"Feeding **{row['name']}** costs {Emojis.Economy.cash} **{fnumb(species.feed_cost)}** — "
                f"you only have **{fnumb(balance.cash)}** in cash."
            )

        await self.bot.db.economy.update_pet(
            member.id, member.guild.id, {"last_fed": discord.utils.utcnow().replace(tzinfo=None)})
        return True, f"{species.emoji} **{row['name']}** happily munches away. Fully fed!"

    async def pet_claim_action(self, member: discord.Member) -> tuple[bool, str]:
        """Collect ``member``'s pet earnings; returns ``(ok, message)``."""
        row = await self.bot.db.economy.get_pet(member.id, member.guild.id)
        if row is None:
            return False, "You don't have a pet."
        species = get_species(row["species"])
        if species is None:
            return False, "Your pet's species no longer exists."

        now = discord.utils.utcnow()
        claim = compute_pet_claim(
            species,
            row["last_claim"].replace(tzinfo=datetime.UTC),
            row["last_fed"].replace(tzinfo=datetime.UTC),
            now=now,
        )
        if claim.amount <= 0:
            if claim.hunger.value == "starving":
                return False, f"**{row['name']}** is starving and gathered nothing. Feed them first!"
            return False, f"**{row['name']}** hasn't gathered anything yet. Check back later."

        scale = await self._payout_scale(member.id, member.guild.id)
        amount = round(claim.amount * scale)
        balance = await self.bot.db.get_user_balance(member.id, member.guild.id)
        await balance.add(cash=amount)
        await self.bot.db.economy.update_pet(member.id, member.guild.id, {"last_claim": now.replace(tzinfo=None)})
        return True, (
            f"{species.emoji} **{row['name']}** brought you {Emojis.Economy.cash} "
            f"**{fnumb(amount)}** ({claim.hours:.1f}h of gathering).{self._scale_suffix(scale)}"
        )

    @pet.command("feed", description="Feed your pet.", guild_only=True)
    async def pet_feed(self, ctx: Context) -> None:
        """Feed your pet so it keeps earning at full rate."""
        assert isinstance(ctx.author, discord.Member)
        await self._send_result(ctx, await self.pet_feed_action(ctx.author))

    @pet.command("claim", description="Collect your pet's earnings.", guild_only=True)
    async def pet_claim(self, ctx: Context) -> None:
        """Collect the cash your pet gathered since the last claim."""
        assert isinstance(ctx.author, discord.Member)
        await self._send_result(ctx, await self.pet_claim_action(ctx.author))

    @pet.command("rename", description="Rename your pet.", guild_only=True)
    @describe(name="The new name.")
    async def pet_rename(self, ctx: Context, *, name: str) -> None:
        """Give your pet a new name."""
        assert ctx.guild is not None
        row = await self.bot.db.economy.get_pet(ctx.author.id, ctx.guild.id)
        if row is None:
            await ctx.send_error("You don't have a pet to rename.")
            return
        new_name = name.strip()[:32]
        await self.bot.db.economy.update_pet(ctx.author.id, ctx.guild.id, {"name": new_name})
        await ctx.send_success(f"Your pet is now called **{new_name}**.")

    @pet.command("abandon", description="Abandon your pet.", guild_only=True)
    async def pet_abandon(self, ctx: Context) -> None:
        """Abandon your pet (permanent — unclaimed earnings are lost)."""
        assert ctx.guild is not None
        row = await self.bot.db.economy.get_pet(ctx.author.id, ctx.guild.id)
        if row is None:
            await ctx.send_error("You don't have a pet.")
            return

        confirm = await ctx.confirm(
            f"Really abandon **{row['name']}**? Unclaimed earnings are lost and the adoption fee is not refunded."
        )
        if not confirm:
            await ctx.send_info(f"**{row['name']}** stays. They knew you couldn't do it.")
            return
        await self.bot.db.economy.delete_pet(ctx.author.id, ctx.guild.id)
        await ctx.send_success(f"You released **{row['name']}** into the wild. \N{CRYING FACE}")

    # -- guild configuration ------------------------------------------------------

    @group(
        "economy-config",
        aliases=["ecoconfig"],
        fallback="show",
        description="Configure the server economy.",
        guild_only=True,
        user_permissions=PermissionTemplate.admin,
    )
    async def economy_config(self, ctx: Context) -> None:
        """Show the server's economy settings."""
        assert ctx.guild is not None
        settings = await self._settings(ctx.guild.id)
        max_bet = f"{Emojis.Economy.cash} {fnumb(settings.max_bet)}" if settings.max_bet else "*uncapped*"
        icon = get_asset_url(ctx.guild)  # type: ignore[arg-type]
        await ctx.send(view=make_notice(
            "Economy Settings",
            f"**Payout multiplier** x{settings.payout_multiplier:g}\n"
            f"**Robbing** {'Enabled' if settings.rob_enabled else 'Disabled'}\n"
            f"**Daily base** {Emojis.Economy.cash} {fnumb(settings.daily_base)}\n"
            f"**Max casino bet** {max_bet}\n"
            f"**Bank cap** {'On' if settings.bank_cap else 'Off'}\n"
            f"**Chat drops** {'On' if settings.drops_enabled else 'Off'}",
            thumbnail=icon or None,
            footer="economy-config multiplier/rob/bank-cap/drops/daily-base/max-bet",
        ))

    @economy_config.command(
        "multiplier",
        description="Set the global payout multiplier.",
        guild_only=True,
        user_permissions=PermissionTemplate.admin,
    )
    @describe(value="The multiplier applied to every earning payout (0.1 - 10).")
    async def economy_config_multiplier(self, ctx: Context, value: commands.Range[float, 0.1, 10.0]) -> None:
        """Scale every earning activity's payout for this server."""
        assert ctx.guild is not None
        await self.bot.db.economy.update_settings(ctx.guild.id, {"payout_multiplier": float(value)})
        await ctx.send_success(f"Earning payouts are now scaled by **x{value:g}**.")

    @economy_config.command(
        "rob",
        description="Enable or disable robbing.",
        guild_only=True,
        user_permissions=PermissionTemplate.admin,
    )
    @describe(enabled="Whether members can rob each other.")
    async def economy_config_rob(self, ctx: Context, enabled: bool) -> None:
        """Toggle the `rob` command for this server."""
        assert ctx.guild is not None
        await self.bot.db.economy.update_settings(ctx.guild.id, {"rob_enabled": enabled})
        await ctx.send_success(f"Robbing is now **{'enabled' if enabled else 'disabled'}**.")

    @economy_config.command(
        "bank-cap",
        description="Limit how much members can keep in the bank.",
        guild_only=True,
        user_permissions=PermissionTemplate.admin,
    )
    @describe(enabled="Whether deposits are limited by each member's bank space.")
    async def economy_config_bank_cap(self, ctx: Context, enabled: bool) -> None:
        """Toggle the bank space limit for this server."""
        assert ctx.guild is not None
        await self.bot.db.economy.update_settings(ctx.guild.id, {"bank_cap": enabled})
        await ctx.send_success(f"The bank cap is now **{'on' if enabled else 'off'}**.")

    @economy_config.command(
        "drops",
        description="Turn random chat drops on or off.",
        guild_only=True,
        user_permissions=PermissionTemplate.admin,
    )
    @describe(enabled="Whether coin and item drops appear in chat now and then.")
    async def economy_config_drops(self, ctx: Context, enabled: bool) -> None:
        """Toggle random chat drops for this server."""
        assert ctx.guild is not None
        await self.bot.db.economy.update_settings(ctx.guild.id, {"drops_enabled": enabled})
        self._drops_cache.pop(ctx.guild.id, None)
        await ctx.send_success(f"Chat drops are now **{'on' if enabled else 'off'}**.")

    @economy_config.command(
        "daily-base",
        description="Set the base daily reward.",
        guild_only=True,
        user_permissions=PermissionTemplate.admin,
    )
    @describe(amount="The base daily payout before streak bonuses (10 - 100,000).")
    async def economy_config_daily_base(self, ctx: Context, amount: commands.Range[int, 10, 100_000]) -> None:
        """Set the base amount `daily` pays before streak bonuses."""
        assert ctx.guild is not None
        await self.bot.db.economy.update_settings(ctx.guild.id, {"daily_base": int(amount)})
        await ctx.send_success(f"The daily reward base is now {Emojis.Economy.cash} **{fnumb(int(amount))}**.")

    @economy_config.command(
        "max-bet",
        description="Cap casino bets (0 removes the cap).",
        guild_only=True,
        user_permissions=PermissionTemplate.admin,
    )
    @describe(amount="The maximum bet for casino games; 0 removes the cap.")
    async def economy_config_max_bet(self, ctx: Context, amount: commands.Range[int, 0, 100_000_000]) -> None:
        """Cap how much members can stake on casino games."""
        assert ctx.guild is not None
        await self.bot.db.economy.update_settings(ctx.guild.id, {"max_bet": int(amount) or None})
        if amount:
            await ctx.send_success(f"Casino bets are now capped at {Emojis.Economy.cash} **{fnumb(int(amount))}**.")
        else:
            await ctx.send_success("Casino bets are no longer capped.")


async def setup(bot: Bot) -> None:
    await bot.add_cog(Economy(bot))
