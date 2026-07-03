import datetime
import random
from contextlib import nullcontext
from typing import Annotated, Any, Literal

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands
from discord.ext.commands import Range

from app.cogs.economy.ui import BOOST_LABELS, EconomyHub, SearchView, boost_display_line, progress_bar
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
    DIG_COOLDOWN,
    DIG_TABLE,
    FISHING_COOLDOWN,
    FISHING_TABLE,
    HUNTING_COOLDOWN,
    HUNTING_TABLE,
    JOB_LADDER,
    MONTHLY_AMOUNT,
    MONTHLY_COOLDOWN,
    PET_SPECIES,
    PRESTIGE_STEP,
    SEARCH_COOLDOWN,
    WEEKLY_AMOUNT,
    WEEKLY_COOLDOWN,
    WORK_COOLDOWN,
    EconomySnapshot,
    GuildEconomySettings,
    SearchLocation,
    boost_multiplier,
    compute_daily,
    compute_periodic,
    compute_pet_claim,
    compute_shift,
    describe_effect,
    evaluate_achievements,
    generate_daily_quests,
    get_achievement,
    get_job,
    get_species,
    pick_search_options,
    pick_weighted_winner,
    prestige_multiplier,
    prestige_requirement,
    resolve_search,
    roll_loot,
    roll_lootbox,
    sell_price,
    validate_item_effect,
)
from app.utils import fnumb, fuzzy, get_asset_url, helpers, pluralize, timetools
from config import Emojis

#: Species ids offered as slash choices by ``pet adopt``.
PetSpeciesChoice = Literal['hamster', 'cat', 'dog', 'parrot', 'fox', 'dragon']


class Economy(Cog):
    """Economy commands"""

    emoji = Emojis.Economy.cash

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

    async def _bump_quests(self, ctx: Context, kind: str, amount: int = 1) -> None:
        """Advance today's quests of ``kind``; pays and announces any that complete."""
        assert ctx.guild is not None
        today = datetime.datetime.now(datetime.UTC).date()
        await self._ensure_quests(ctx.author.id, ctx.guild.id, today)
        completed = await self.bot.db.economy.advance_quests(ctx.author.id, ctx.guild.id, today, kind, amount)
        if not completed:
            return

        board = {q.key: q for q in generate_daily_quests(ctx.guild.id, ctx.author.id, today)}
        total = sum(row['reward'] for row in completed)
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        await balance.add(cash=total)

        lines = '\n'.join(
            f'\N{SCROLL} **Quest complete:** {board[row["quest"]].description if row["quest"] in board else row["quest"]} '
            f'— {Emojis.Economy.cash} **{fnumb(row["reward"])}**'
            for row in completed
        )
        await ctx.send(lines)
        await self._sync_achievements(ctx)

    async def _sync_achievements(self, ctx: Context, *, announce: bool = True) -> None:
        """Award any newly qualified achievements for the invoker (idempotent).

        Assembles the :class:`EconomySnapshot` from the database, diffs the
        qualifying set against what is already earned, pays the rewards and —
        unless ``announce`` is off — posts an unlock notice.
        """
        assert ctx.guild is not None
        user_id, guild_id = ctx.author.id, ctx.guild.id
        db = self.bot.db

        balance = await db.get_user_balance(user_id, guild_id)
        daily = await db.economy.get_daily(user_id, guild_id)
        job_row = await db.economy.get_job(user_id, guild_id)
        snapshot = EconomySnapshot(
            net_worth=balance.total,
            daily_streak=daily['streak'] if daily else 0,
            shifts=job_row['shifts'] if job_row else 0,
            job_id=job_row['job_id'] if job_row else None,
            prestige=await db.economy.get_prestige(user_id, guild_id),
            has_pet=await db.economy.get_pet(user_id, guild_id) is not None,
            quests_completed=await db.economy.count_completed_quests(user_id, guild_id),
            items_owned=await db.economy.count_items(user_id, guild_id),
        )
        new_ids = await db.economy.award_achievements(user_id, guild_id, evaluate_achievements(snapshot))
        if not new_ids:
            return

        unlocked = [a for a in (get_achievement(aid) for aid in new_ids) if a is not None]
        reward = sum(a.reward for a in unlocked)
        if reward:
            await balance.add(cash=reward)
        if announce and unlocked:
            lines = '\n'.join(f'{a.emoji} **{a.name}** — {a.description}' for a in unlocked)
            view = make_notice(
                '\N{TROPHY} Achievement unlocked!',
                f'{lines}\n\nReward: {Emojis.Economy.cash} **{fnumb(reward)}**',
                accent=Accent.success,
            )
            await ctx.send(view=view)

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
        """Autocomplete over every item in the guild's shop."""
        assert interaction.guild_id is not None
        items = await self.bot.db.economy.get_items(interaction.guild_id)
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
        amount: int,
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
        self, ctx: Context, role: Annotated[discord.Role, commands.RoleConverter], amount: Range[int, 1], to: Literal["bank", "cash"]
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
        amount: Range[int, 1],
        to: Literal["bank", "cash"],
    ) -> None:
        """Removes from a user's balance"""
        if member.bot:
            await ctx.send_error("Cannot remove from a bot's balance.")
            return

        balance = await ctx.db.get_user_balance(member.id, ctx.guild.id)  # type: ignore[union-attr]

        if (to == "bank" and balance.bank < amount) or (to == "cash" and balance.cash < amount):
            await ctx.send_error("Cannot remove more than the user's balance.")
            return

        if to == "bank":
            await balance.remove(bank=amount)
        else:
            await balance.remove(cash=amount)

        await ctx.send_success(
            f"Successfully removed {Emojis.Economy.cash} **{fnumb(amount)}** from **{member.display_name}'s** {to}."
        )

    @command("deposit", aliases=["dep"], description="Deposits money into your bank.", guild_only=True, hybrid=True)
    @describe(amount="The amount to deposit.")
    async def deposit(self, ctx: Context, amount: Range[int, 1]) -> None:
        """Deposits money into your bank."""
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)  # type: ignore[union-attr]
        if balance.cash < amount:
            await ctx.send_error("Cannot deposit more than your balance.")
            return

        await balance.remove(cash=amount)
        await balance.add(bank=amount)
        await ctx.send_success(f"Successfully deposited {Emojis.Economy.cash} **{fnumb(amount)}** into your bank.")
        await self._bump_quests(ctx, "deposit", amount)

    @command("withdraw", description="Withdraws money from your bank.", guild_only=True, hybrid=True)
    @describe(amount="The amount to withdraw.")
    async def withdraw(self, ctx: Context, amount: Range[int, 1]) -> None:
        """Withdraws money from your bank."""
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)  # type: ignore[union-attr]
        if balance.bank < amount:
            await ctx.send_error("Cannot withdraw more than your bank balance.")
            return

        await balance.remove(bank=amount)
        await balance.add(cash=amount)
        await ctx.send_success(f"Successfully withdrew {Emojis.Economy.cash} **{fnumb(amount)}** from your bank.")

    @command("transfer", description="Transfers money to another user.", guild_only=True, hybrid=True)
    @describe(member="The user to transfer the money to.", amount="The amount to transfer.")
    async def transfer(
        self, ctx: Context, member: Annotated[discord.Member, converter.MemberConverter], amount: Range[int, 1]
    ) -> None:
        """Transfers money to another user."""
        if member.bot:
            await ctx.send_error("Cannot transfer to a bot.")
            return

        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)  # type: ignore[union-attr]
        if balance.cash < amount:
            await ctx.send_error("Cannot transfer more than your balance.")
            return

        await balance.remove(cash=amount)
        balance = await ctx.db.get_user_balance(member.id, ctx.guild.id)  # type: ignore[union-attr]
        await balance.add(cash=amount)
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
        embed = discord.Embed(description="Server Leaderboard Rank: x", colour=helpers.Colour.white())
        embed.set_author(name=f"{user.display_name}'s Balance", icon_url=get_asset_url(user))
        embed.add_field(name="Cash", value=f"{Emojis.Economy.cash} **{fnumb(balance.cash)}**")
        embed.add_field(name="Bank", value=f"{Emojis.Economy.cash} **{fnumb(balance.bank)}**")
        embed.add_field(name="Total", value=f"{Emojis.Economy.cash} **{fnumb(balance.total)}**")
        await ctx.send(embed=embed)

    @balance.command("leaderboard", alias="top", description="Shows the leaderboard of the server", guild_only=True)
    async def leaderboard(self, ctx: Context) -> None:
        """Shows the leaderboard of the server."""
        assert ctx.guild is not None
        balances = await ctx.db.get_guild_balances(ctx.guild.id)
        total = sum(balance.total for balance in balances)
        # The underlying query returns rows in arbitrary order, so rank richest-first here.
        balances = sorted(balances, key=lambda balance: balance.total, reverse=True)

        users = [
            f"**{index}.** <@{balance.user_id}> • {Emojis.Economy.cash} **{fnumb(balance.total)}**"
            for index, balance in enumerate(balances, 1)
        ]

        embed = discord.Embed(
            title="Economy Leaderboard", description="This is the server's leaderboard.\n\n", colour=helpers.Colour.white()
        )
        embed.set_author(name=ctx.guild.name, icon_url=get_asset_url(ctx.guild))  # type: ignore[arg-type]
        embed.set_footer(
            text=f"Total Server Money: {fnumb(total)}", icon_url=discord.PartialEmoji.from_str(Emojis.Economy.cash).url
        )
        await LinePaginator.start(ctx, entries=users, embed=embed, location='description')

    @command("daily", description="Claim your daily reward and build a streak.", guild_only=True, hybrid=True)
    async def daily(self, ctx: Context) -> None:
        """Claim your daily reward. Claim on consecutive days to grow your streak bonus."""
        assert ctx.guild is not None
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
        await balance.add(cash=amount)
        await self.bot.db.economy.set_daily(ctx.author.id, ctx.guild.id, now, result.streak)

        await ctx.send_success(
            f"You claimed {Emojis.Economy.cash} **{fnumb(amount)}**!{self._scale_suffix(scale)} "
            f"\N{FIRE} Streak: **{pluralize(result.streak):day}**."
        )
        await self._sync_achievements(ctx)

    @command("weekly", description="Claim your weekly reward.", guild_only=True, hybrid=True)
    async def weekly(self, ctx: Context) -> None:
        """Claim your weekly reward — a bigger payout on a 7-day cooldown."""
        assert ctx.guild is not None
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

    @command("monthly", description="Claim your monthly reward.", guild_only=True, hybrid=True)
    async def monthly(self, ctx: Context) -> None:
        """Claim your monthly reward — the biggest fixed payout, once every 30 days."""
        assert ctx.guild is not None
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

    @group("shop", fallback="list", description="Browse the server shop.", guild_only=True, hybrid=True)
    async def shop(self, ctx: Context) -> None:
        """Browse the items available in the server shop."""
        assert ctx.guild is not None
        items = await self.bot.db.economy.get_items(ctx.guild.id)
        if not items:
            await ctx.send_info("The shop is empty. Admins can add items with `shop add`.")
            return

        entries = []
        for item in items:
            line = (
                f"**{item['name']}** • {Emojis.Economy.cash} {fnumb(item['price'])}\n"
                f"{item['description'] or '*No description.*'}"
            )
            effect_line = self._effect_line(ctx.guild, item)
            if effect_line:
                line += f"\n\N{SMALL BLUE DIAMOND} {effect_line}"
            entries.append(line)
        embed = discord.Embed(title="Server Shop", description="", colour=helpers.Colour.white())
        embed.set_author(name=ctx.guild.name, icon_url=get_asset_url(ctx.guild))  # type: ignore[arg-type]
        await LinePaginator.start(ctx, entries=entries, embed=embed, location='description')

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

        error = validate_item_effect(effect, effect_value, duration)
        if error:
            await ctx.send_error(error)
            return

        record = await self.bot.db.economy.create_item(
            ctx.guild.id, name, description, price, effect, effect_value, duration
        )
        if record is None:
            await ctx.send_error(f"An item named **{name}** already exists.")
            return

        effect_line = self._effect_line(ctx.guild, record)
        await ctx.send_success(
            f"Added **{name}** to the shop for {Emojis.Economy.cash} **{fnumb(price)}**."
            + (f"\n\N{SMALL BLUE DIAMOND} {effect_line}" if effect_line else "")
        )

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
        record = await self.bot.db.economy.delete_item(ctx.guild.id, name)
        if record is None:
            await ctx.send_error(f"No item named **{name}** exists.")
            return
        await ctx.send_success(f"Removed **{record['name']}** from the shop.")

    @command("buy", description="Buy an item from the shop.", guild_only=True, hybrid=True)
    @describe(name="The item to buy.", quantity="How many to buy.")
    @app_commands.autocomplete(name=shop_item_autocomplete)  # type: ignore
    async def buy(self, ctx: Context, name: str, quantity: commands.Range[int, 1] = 1) -> None:
        """Buy an item from the shop with your cash."""
        assert ctx.guild is not None
        item = await self.bot.db.economy.get_item(ctx.guild.id, name)
        if item is None:
            await ctx.send_error(f"No item named **{name}** exists in the shop.")
            return

        total = item["price"] * quantity
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        if balance.cash < total:
            await ctx.send_error(
                f"You need {Emojis.Economy.cash} **{fnumb(total)}** but only have **{fnumb(balance.cash)}**."
            )
            return

        await balance.remove(cash=total)
        await self.bot.db.economy.add_to_inventory(ctx.author.id, ctx.guild.id, item["id"], quantity)
        await ctx.send_success(
            f"Bought **{quantity}× {item['name']}** for {Emojis.Economy.cash} **{fnumb(total)}**."
        )
        await self._sync_achievements(ctx)

    @command("sell", description="Sell an item back to the shop.", guild_only=True, hybrid=True)
    @describe(name="The item to sell.", quantity="How many to sell.")
    @app_commands.autocomplete(name=owned_item_autocomplete)  # type: ignore
    async def sell(self, ctx: Context, name: str, quantity: commands.Range[int, 1] = 1) -> None:
        """Sell an item from your inventory back for half its price."""
        assert ctx.guild is not None
        item = await self.bot.db.economy.get_item(ctx.guild.id, name)
        if item is None:
            await ctx.send_error(f"No item named **{name}** exists in the shop.")
            return

        owned = await self.bot.db.economy.get_quantity(ctx.author.id, ctx.guild.id, item["id"])
        if owned < quantity:
            await ctx.send_error(f"You only own **{owned}× {item['name']}**.")
            return

        payout = sell_price(item["price"]) * quantity
        await self.bot.db.economy.remove_from_inventory(ctx.author.id, ctx.guild.id, item["id"], quantity)
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        await balance.add(cash=payout)
        await ctx.send_success(
            f"Sold **{quantity}× {item['name']}** for {Emojis.Economy.cash} **{fnumb(payout)}**."
        )

    @command("inventory", aliases=["inv"], description="Show your or another member's items.", guild_only=True, hybrid=True)
    @describe(member="The member whose inventory to show.")
    async def inventory(
        self, ctx: Context, member: Annotated[discord.Member | None, converter.MemberConverter] = None
    ) -> None:
        """Show the items a member owns."""
        assert ctx.guild is not None
        user = member or ctx.author
        rows = await self.bot.db.economy.get_inventory(user.id, ctx.guild.id)
        if not rows:
            await ctx.send_info(f"**{user.display_name}** doesn't own any items.")
            return

        total_value = sum(sell_price(row["price"]) * row["quantity"] for row in rows)
        total_items = sum(row["quantity"] for row in rows)

        entries = []
        for row in rows:
            unit = sell_price(row["price"])
            line_value = unit * row["quantity"]
            line = (
                f"**{row['name']}**  ×{row['quantity']}\n"
                f"\N{SMALL ORANGE DIAMOND} {Emojis.Economy.cash} **{fnumb(line_value)}** "
                f"*(sell {fnumb(unit)} each)*"
            )
            effect_line = self._effect_line(ctx.guild, row)
            if effect_line:
                line += f"\n\N{SMALL BLUE DIAMOND} {effect_line}"
            entries.append(line)

        boosts = await self.bot.db.economy.get_active_boosts(user.id, ctx.guild.id)
        boost_lines = "".join(f"{boost_display_line(row)}\n" for row in boosts)

        embed = discord.Embed(
            description=(
                f"{Emojis.Economy.cash} Total sell value: **{fnumb(total_value)}**\n"
                f"\N{PACKAGE} **{pluralize(total_items):item}** across "
                f"**{pluralize(len(rows)):unique type}**\n"
                f"{boost_lines}\n"
            ),
            colour=helpers.Colour.white(),
        )
        embed.set_author(name=f"{user.display_name}'s Inventory", icon_url=get_asset_url(user))
        embed.set_thumbnail(url=get_asset_url(user))
        await LinePaginator.start(ctx, entries=entries, embed=embed, location="description")

    @command("use", description="Use an item from your inventory.", guild_only=True, hybrid=True, bot_permissions=["manage_roles"])
    @describe(name="The item to use.")
    @app_commands.autocomplete(name=owned_item_autocomplete)  # type: ignore
    async def use(self, ctx: Context, *, name: str) -> None:
        """Use (consume one of) an item you own — what happens depends on the item's effect.

        Vouchers redeem for cash, lootboxes roll a random payout, role items grant
        their role, and boost items activate a timed XP or loot multiplier.
        """
        assert ctx.guild is not None
        item = await self.bot.db.economy.get_item(ctx.guild.id, name)
        if item is None:
            await ctx.send_error(f"No item named **{name}** exists in the shop.")
            return

        owned = await self.bot.db.economy.get_quantity(ctx.author.id, ctx.guild.id, item["id"])
        if owned < 1:
            await ctx.send_error(f"You don't own any **{item['name']}**.")
            return

        effect: str = item.get("effect") or "none"
        value: int = item.get("effect_value") or 0
        duration: int = item.get("duration_minutes") or 0
        if validate_item_effect(effect, value or None, duration or None) is not None:
            # Misconfigured (e.g. pre-dates validation) - fall back to a plain collectible.
            effect = "none"

        left = f"You have **{owned - 1}** left."

        if effect == "cash":
            balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
            await balance.add(cash=value)
            message = f"You redeemed **{item['name']}** for {Emojis.Economy.cash} **{fnumb(value)}**. {left}"
        elif effect == "lootbox":
            payout = roll_lootbox(value)
            balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
            await balance.add(cash=payout)
            flavour = (
                "\N{PARTY POPPER} Jackpot!" if payout >= value * 2
                else "Unlucky..." if payout < value // 2
                else "Not bad!"
            )
            message = (
                f"You opened **{item['name']}** and found {Emojis.Economy.cash} **{fnumb(payout)}**. {flavour} {left}"
            )
        elif effect == "role":
            assert isinstance(ctx.author, discord.Member)
            role = ctx.guild.get_role(value)
            if role is None:
                await ctx.send_error("The role this item grants no longer exists — ask an admin to fix the item.")
                return
            if role in ctx.author.roles:
                await ctx.send_error(f"You already have the **{role.name}** role; the item was not consumed.")
                return
            try:
                await ctx.author.add_roles(role, reason=f"Used shop item {item['name']}")
            except discord.HTTPException:
                await ctx.send_error(f"I couldn't grant **{role.name}** — check my role hierarchy and permissions.")
                return
            message = f"You used **{item['name']}** and received the **{role.name}** role. {left}"
        elif effect in ("xp_boost", "loot_boost"):
            kind = "xp" if effect == "xp_boost" else "loot"
            expires = await self.bot.db.economy.add_boost(
                ctx.author.id, ctx.guild.id, kind, boost_multiplier(value), duration
            )
            when = discord.utils.format_dt(expires.replace(tzinfo=datetime.UTC), "R")
            message = (
                f"You activated **{item['name']}**: **+{value}%** {BOOST_LABELS[kind]}, ending {when}. {left}"
            )
        elif effect == "rob_shield":
            expires = await self.bot.db.economy.add_boost(ctx.author.id, ctx.guild.id, "shield", 1.0, duration)
            when = discord.utils.format_dt(expires.replace(tzinfo=datetime.UTC), "R")
            message = (
                f"\N{SHIELD} You activated **{item['name']}**: rob attempts against you are blocked until {when}. {left}"
            )
        else:
            message = f"You used **{item['name']}**. Nothing obvious happened — must be a collectible. {left}"

        await self.bot.db.economy.remove_from_inventory(ctx.author.id, ctx.guild.id, item["id"], 1)
        await ctx.send_success(message)

    # -- earning activities ----------------------------------------------

    @command("fish", description="Cast a line and earn a random catch.", guild_only=True, hybrid=True)
    @cooldown(1, FISHING_COOLDOWN)
    async def fish(self, ctx: Context) -> None:
        """Go fishing for a chance at cash — outcomes range from junk to a rare pearl."""
        assert ctx.guild is not None
        catch = roll_loot(FISHING_TABLE)
        boost = await self.bot.db.economy.get_boost_multiplier(ctx.author.id, ctx.guild.id, "loot")
        scale = await self._payout_scale(ctx.author.id, ctx.guild.id)
        amount = round(catch.amount * boost * scale)
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        await balance.add(cash=amount)

        if catch.amount <= 5:
            await ctx.send_info(f"{catch.emoji} You reeled in **{catch.name}** — barely worth it.")
        else:
            suffix = f" *(+{boost - 1.0:.0%} loot boost)*" if boost > 1.0 else ""
            await ctx.send_success(
                f"{catch.emoji} You caught **{catch.name}** and sold it for "
                f"{Emojis.Economy.cash} **{fnumb(amount)}**!{suffix}{self._scale_suffix(scale)}"
            )
        await self._bump_quests(ctx, "fish")

    @command("hunt", description="Head out hunting for a bigger, riskier payout.", guild_only=True, hybrid=True)
    @cooldown(1, HUNTING_COOLDOWN)
    async def hunt(self, ctx: Context) -> None:
        """Go hunting — higher payouts and variance than fishing, on a longer cooldown."""
        assert ctx.guild is not None
        catch = roll_loot(HUNTING_TABLE)
        boost = await self.bot.db.economy.get_boost_multiplier(ctx.author.id, ctx.guild.id, "loot")
        scale = await self._payout_scale(ctx.author.id, ctx.guild.id)
        amount = round(catch.amount * boost * scale)
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        await balance.add(cash=amount)

        if catch.amount <= 10:
            await ctx.send_info(f"{catch.emoji} You found **{catch.name}** and came back empty-handed.")
        else:
            suffix = f" *(+{boost - 1.0:.0%} loot boost)*" if boost > 1.0 else ""
            await ctx.send_success(
                f"{catch.emoji} You bagged **{catch.name}** worth "
                f"{Emojis.Economy.cash} **{fnumb(amount)}**!{suffix}{self._scale_suffix(scale)}"
            )
        await self._bump_quests(ctx, "hunt")

    @command("beg", description="Beg for a little spare change.", guild_only=True, hybrid=True)
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

    @command("dig", description="Dig around for buried valuables.", guild_only=True, hybrid=True)
    @cooldown(1, DIG_COOLDOWN)
    async def dig(self, ctx: Context) -> None:
        """Grab a shovel and dig — finds range from bottle caps to ancient relics."""
        assert ctx.guild is not None
        catch = roll_loot(DIG_TABLE)
        boost = await self.bot.db.economy.get_boost_multiplier(ctx.author.id, ctx.guild.id, "loot")
        scale = await self._payout_scale(ctx.author.id, ctx.guild.id)
        amount = round(catch.amount * boost * scale)
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        await balance.add(cash=amount)

        if catch.amount <= 8:
            await ctx.send_info(f"{catch.emoji} You dug up **{catch.name}** — hardly a treasure.")
        else:
            suffix = f" *(+{boost - 1.0:.0%} loot boost)*" if boost > 1.0 else ""
            await ctx.send_success(
                f"{catch.emoji} You unearthed **{catch.name}** worth "
                f"{Emojis.Economy.cash} **{fnumb(amount)}**!{suffix}{self._scale_suffix(scale)}"
            )
        await self._bump_quests(ctx, "dig")

    @command("search", description="Search one of three random spots for cash.", guild_only=True, hybrid=True)
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
                fine = min(outcome.fine, max(balance.cash, 0))
                if fine:
                    await balance.remove(cash=fine)
                text = (
                    f'{location.emoji} **{location.name}** — {outcome.flavor}\n'
                    f'You lost {Emojis.Economy.cash} **{fnumb(fine)}**.'
                )
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

    @command("work", description="Work a shift at your job.", guild_only=True, hybrid=True)
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
        scale = await self._payout_scale(ctx.author.id, ctx.guild.id)
        amount = round(result.amount * scale)
        shifts = await self.bot.db.economy.add_shift(ctx.author.id, ctx.guild.id, job.id)

        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        await balance.add(cash=amount)

        message = (
            f"{job.emoji} You worked a shift as **{job.name}** and earned "
            f"{Emojis.Economy.cash} **{fnumb(amount)}**.{self._scale_suffix(scale)}"
        )
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
        """Show your current job, lifetime shifts and progress to the next rung."""
        assert ctx.guild is not None
        row = await self.bot.db.economy.get_job(ctx.author.id, ctx.guild.id)
        current = get_job(row["job_id"] if row else None)
        shifts = row["shifts"] if row else 0

        embed = discord.Embed(colour=helpers.Colour.white())
        embed.set_author(name=f"{ctx.author.display_name}'s Career", icon_url=get_asset_url(ctx.author))
        embed.add_field(name="Job", value=f"{current.emoji} **{current.name}**")
        embed.add_field(name="Pay per Shift", value=f"{Emojis.Economy.cash} {fnumb(current.pay_min)}-{fnumb(current.pay_max)}")
        embed.add_field(name="Lifetime Shifts", value=f"**{fnumb(shifts)}**")

        nxt = next((j for j in JOB_LADDER if j.shifts_required > shifts), None)
        if nxt is not None:
            embed.add_field(
                name="Next Unlock",
                value=(
                    f"{nxt.emoji} **{nxt.name}** — {progress_bar(shifts, nxt.shifts_required)} "
                    f"{shifts}/{nxt.shifts_required} shifts"
                ),
                inline=False,
            )
        else:
            embed.add_field(name="Next Unlock", value="You've reached the top of the ladder. \N{ROCKET}", inline=False)
        await ctx.send(embed=embed)

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

        embed = discord.Embed(
            title="Career Ladder",
            description="\n".join(lines),
            colour=helpers.Colour.white(),
        )
        embed.set_footer(text=f"You have worked {fnumb(shifts)} shifts. Apply with `job apply <name>`.")
        await ctx.send(embed=embed)

    @job.command("apply", description="Apply for a job on the ladder.", guild_only=True)
    @describe(name="The job to apply for.")
    @app_commands.autocomplete(name=job_autocomplete)  # type: ignore
    async def job_apply(self, ctx: Context, *, name: str) -> None:
        """Apply for a job you've unlocked with lifetime shifts."""
        assert ctx.guild is not None
        wanted = name.strip().lower()
        target = next((j for j in JOB_LADDER if j.id == wanted or j.name.lower() == wanted), None)
        if target is None:
            await ctx.send_error(f"No job named **{name}** exists. See `job list`.")
            return

        row = await self.bot.db.economy.get_job(ctx.author.id, ctx.guild.id)
        shifts = row["shifts"] if row else 0
        if row and row["job_id"] == target.id:
            await ctx.send_error(f"You already work as **{target.name}**.")
            return
        if target.shifts_required > shifts:
            await ctx.send_error(
                f"**{target.name}** requires **{target.shifts_required}** lifetime shifts — you have **{shifts}**."
            )
            return

        await self.bot.db.economy.set_job(ctx.author.id, ctx.guild.id, target.id)
        await ctx.send_success(
            f"{target.emoji} You are now employed as **{target.name}** "
            f"({Emojis.Economy.cash} {fnumb(target.pay_min)}-{fnumb(target.pay_max)} per shift)."
        )
        await self._sync_achievements(ctx)

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
    async def lottery_start(self, ctx: Context, duration: str, ticket_price: Range[int, 1]) -> None:
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

        await self.bot.timers.create(when, "lottery", ctx.guild.id)
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
        if balance.cash < cost:
            await ctx.send_error(
                f"You need {Emojis.Economy.cash} **{fnumb(cost)}** but only have **{fnumb(balance.cash)}**."
            )
            return

        await balance.remove(cash=cost)
        total = await self.bot.db.economy.add_lottery_tickets(ctx.guild.id, ctx.author.id, amount, cost)
        await ctx.send_success(
            f"Bought **{pluralize(amount):ticket}** for {Emojis.Economy.cash} **{fnumb(cost)}**. "
            f"You now hold **{fnumb(total)}**."
        )

    @Cog.listener()
    async def on_lottery_timer_complete(self, timer: Timer) -> None:
        guild_id: int = timer.args[0]
        record = await self.bot.db.economy.get_lottery(guild_id)
        if record is None:
            return

        entries = await self.bot.db.economy.get_lottery_entries(guild_id)
        jackpot: int = record["jackpot"]
        channel = self.bot.get_channel(record["channel_id"])
        await self.bot.db.economy.delete_lottery(guild_id)

        if not isinstance(channel, discord.abc.Messageable):
            return

        winner_id = pick_weighted_winner([(e["user_id"], e["tickets"]) for e in entries])
        if winner_id is None or jackpot <= 0:
            view = make_notice(
                "Lottery Ended",
                "The lottery ended with no tickets sold. Better luck next time!",
                accent=Accent.warning,
            )
            await channel.send(view=view)
            return

        balance = await self.bot.db.get_user_balance(winner_id, guild_id)
        await balance.add(cash=jackpot)
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
        assert ctx.guild is not None
        user = member or ctx.author
        boosts = await self.bot.db.economy.get_active_boosts(user.id, ctx.guild.id)
        if not boosts:
            await ctx.send_info(f"**{user.display_name}** has no active perks.")
            return

        lines = [boost_display_line(row) for row in boosts]

        embed = discord.Embed(
            title=f"{user.display_name}'s Active Perks",
            description="\n".join(lines),
            colour=helpers.Colour.white(),
        )
        embed.set_thumbnail(url=get_asset_url(user))
        await ctx.send(embed=embed)

    # -- gifting ------------------------------------------------------------

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
        assert ctx.guild is not None
        if member.bot:
            await ctx.send_error("Cannot gift items to a bot.")
            return
        if member.id == ctx.author.id:
            await ctx.send_error("You cannot gift items to yourself.")
            return

        item = await self.bot.db.economy.get_item(ctx.guild.id, name)
        if item is None:
            await ctx.send_error(f"No item named **{name}** exists in the shop.")
            return

        moved = await self.bot.db.economy.transfer_item(ctx.author.id, member.id, ctx.guild.id, item["id"], quantity)
        if not moved:
            owned = await self.bot.db.economy.get_quantity(ctx.author.id, ctx.guild.id, item["id"])
            await ctx.send_error(f"You only own **{owned}× {item['name']}**.")
            return

        await ctx.send_success(
            f"\N{WRAPPED PRESENT} You gifted **{quantity}× {item['name']}** to **{member.display_name}**."
        )
        await self._bump_quests(ctx, "gift", quantity)

    # -- prestige ------------------------------------------------------------

    @command("prestige", description="Reset your wealth for a permanent payout bonus.", guild_only=True, hybrid=True)
    async def prestige(self, ctx: Context) -> None:
        """Prestige: trade your entire cash & bank balance for a permanent +10% payout bonus.

        Each level requires a higher net worth. The bonus applies to every
        earning activity (work, fish, hunt, beg, dig, search, daily/weekly/monthly).
        """
        assert ctx.guild is not None
        level = await self.bot.db.economy.get_prestige(ctx.author.id, ctx.guild.id)
        requirement = prestige_requirement(level)
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)

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

        await balance.update(cash=0, bank=0)
        new_level = await self.bot.db.economy.increment_prestige(ctx.author.id, ctx.guild.id)
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
        assert ctx.guild is not None
        user = member or ctx.author
        if user.id == ctx.author.id:
            # Catch up on anything qualified but not yet awarded before displaying.
            await self._sync_achievements(ctx, announce=False)

        earned_rows = await self.bot.db.economy.get_achievements(user.id, ctx.guild.id)
        earned = {row["achievement"]: row["earned_at"] for row in earned_rows}

        entries = []
        for achievement in ACHIEVEMENTS:
            if achievement.id in earned:
                when = discord.utils.format_dt(earned[achievement.id].replace(tzinfo=datetime.UTC), "d")
                entries.append(f"{achievement.emoji} **{achievement.name}** — {achievement.description} *(earned {when})*")
            else:
                entries.append(f"\N{LOCK} *{achievement.name}* — {achievement.description}")

        embed = discord.Embed(
            description=f"Unlocked **{len(earned)}/{len(ACHIEVEMENTS)}** badges.\n\n",
            colour=helpers.Colour.white(),
        )
        embed.set_author(name=f"{user.display_name}'s Achievements", icon_url=get_asset_url(user))
        await LinePaginator.start(ctx, entries=entries, embed=embed, location="description")

    # -- daily quests ------------------------------------------------------------

    @command("quests", aliases=["quest"], description="View your daily quest board.", guild_only=True, hybrid=True)
    async def quests(self, ctx: Context) -> None:
        """Show today's quests: three rotating tasks with cash rewards, reset daily (UTC)."""
        assert ctx.guild is not None
        today = datetime.datetime.now(datetime.UTC).date()
        rows = await self._ensure_quests(ctx.author.id, ctx.guild.id, today)
        board = {q.key: q for q in generate_daily_quests(ctx.guild.id, ctx.author.id, today)}

        lines = []
        for row in rows:
            quest = board.get(row["quest"])
            description = quest.description if quest else row["quest"]
            if row["completed"]:
                lines.append(f"\N{WHITE HEAVY CHECK MARK} ~~{description}~~ — {Emojis.Economy.cash} **{fnumb(row['reward'])}**")
            else:
                lines.append(
                    f"\N{SCROLL} **{description}**\n"
                    f"`{progress_bar(row['progress'], row['goal'])}` {fnumb(row['progress'])}/{fnumb(row['goal'])} "
                    f"— {Emojis.Economy.cash} **{fnumb(row['reward'])}**"
                )

        resets = datetime.datetime.combine(
            today + datetime.timedelta(days=1), datetime.time(), tzinfo=datetime.UTC)
        embed = discord.Embed(
            title="Daily Quests",
            description="\n".join(lines),
            colour=helpers.Colour.white(),
        )
        embed.set_footer(text="New quests every day (UTC).")
        embed.add_field(name="Resets", value=discord.utils.format_dt(resets, "R"))
        await ctx.send(embed=embed)

    # -- pets ----------------------------------------------------------------------

    @group("pet", fallback="info", description="Manage your pet companion.", guild_only=True, hybrid=True)
    async def pet(self, ctx: Context) -> None:
        """Show your pet: species, hunger and unclaimed passive earnings."""
        assert ctx.guild is not None
        row = await self.bot.db.economy.get_pet(ctx.author.id, ctx.guild.id)
        if row is None:
            await ctx.send_info(
                "You don't have a pet. Adopt one with `pet adopt` — see the species and prices with `pet shop`.")
            return

        species = get_species(row["species"])
        if species is None:
            await ctx.send_error("Your pet's species no longer exists — ask the developer what happened.")
            return

        now = ctx.message.created_at
        last_fed = row["last_fed"].replace(tzinfo=datetime.UTC)
        last_claim = row["last_claim"].replace(tzinfo=datetime.UTC)
        claim = compute_pet_claim(species, last_claim, last_fed, now=now)
        hunger_labels = {
            "fed": "\N{SMILING FACE WITH SMILING EYES} Well fed",
            "hungry": "\N{FACE WITH OPEN MOUTH} Hungry (earning half rate)",
            "starving": "\N{FACE SCREAMING IN FEAR} Starving (earning nothing!)",
        }

        embed = discord.Embed(colour=helpers.Colour.white())
        embed.set_author(name=f"{ctx.author.display_name}'s Pet", icon_url=get_asset_url(ctx.author))
        embed.add_field(name="Pet", value=f"{species.emoji} **{row['name']}** ({species.name})")
        embed.add_field(name="Hunger", value=hunger_labels[claim.hunger.value])
        embed.add_field(
            name="Earnings",
            value=(
                f"{Emojis.Economy.cash} **{fnumb(claim.amount)}** unclaimed "
                f"*(rate {fnumb(species.hourly_rate)}/h, stores up to {species.storage_hours}h)*"
            ),
            inline=False,
        )
        embed.add_field(name="Feeding Cost", value=f"{Emojis.Economy.cash} {fnumb(species.feed_cost)}")
        embed.add_field(name="Adopted", value=discord.utils.format_dt(row["adopted_at"].replace(tzinfo=datetime.UTC), "d"))
        embed.set_footer(text="Feed with `pet feed`, collect with `pet claim`.")
        await ctx.send(embed=embed)

    @pet.command("shop", description="Show the adoptable species.", guild_only=True)
    async def pet_shop(self, ctx: Context) -> None:
        """List every adoptable species with cost, earn rate and upkeep."""
        lines = [
            f"{species.emoji} **{species.name}** — {Emojis.Economy.cash} **{fnumb(species.cost)}**\n"
            f"\N{SMALL BLUE DIAMOND} Earns {fnumb(species.hourly_rate)}/h (stores {species.storage_hours}h), "
            f"feeding costs {fnumb(species.feed_cost)}"
            for species in PET_SPECIES
        ]
        embed = discord.Embed(title="Pet Adoption Centre", description="\n".join(lines), colour=helpers.Colour.white())
        embed.set_footer(text="Adopt with `pet adopt <species> [name]` — one pet per member.")
        await ctx.send(embed=embed)

    @pet.command("adopt", description="Adopt a pet.", guild_only=True)
    @describe(species="The species to adopt.", name="Your pet's name (defaults to the species name).")
    async def pet_adopt(self, ctx: Context, species: PetSpeciesChoice, *, name: str | None = None) -> None:
        """Adopt a pet — it earns cash passively as long as you keep it fed."""
        assert ctx.guild is not None
        spec = get_species(species)
        assert spec is not None

        pet_name = (name or spec.name).strip()[:32]
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        if balance.cash < spec.cost:
            await ctx.send_error(
                f"Adopting a {spec.name} costs {Emojis.Economy.cash} **{fnumb(spec.cost)}** — "
                f"you only have **{fnumb(balance.cash)}** in cash."
            )
            return

        record = await self.bot.db.economy.create_pet(ctx.author.id, ctx.guild.id, spec.id, pet_name)
        if record is None:
            await ctx.send_error("You already have a pet. Abandon it first with `pet abandon` (how could you?).")
            return

        await balance.remove(cash=spec.cost)
        await ctx.send_success(
            f"{spec.emoji} You adopted **{pet_name}** the {spec.name}! "
            f"They earn {Emojis.Economy.cash} **{fnumb(spec.hourly_rate)}/h** while fed — collect with `pet claim`."
        )
        await self._sync_achievements(ctx)

    @pet.command("feed", description="Feed your pet.", guild_only=True)
    async def pet_feed(self, ctx: Context) -> None:
        """Feed your pet so it keeps earning at full rate."""
        assert ctx.guild is not None
        row = await self.bot.db.economy.get_pet(ctx.author.id, ctx.guild.id)
        if row is None:
            await ctx.send_error("You don't have a pet to feed.")
            return
        species = get_species(row["species"])
        if species is None:
            await ctx.send_error("Your pet's species no longer exists.")
            return

        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        if balance.cash < species.feed_cost:
            await ctx.send_error(
                f"Feeding **{row['name']}** costs {Emojis.Economy.cash} **{fnumb(species.feed_cost)}** — "
                f"you only have **{fnumb(balance.cash)}** in cash."
            )
            return

        await balance.remove(cash=species.feed_cost)
        now = ctx.message.created_at.replace(tzinfo=None)
        await self.bot.db.economy.update_pet(ctx.author.id, ctx.guild.id, {"last_fed": now})
        await ctx.send_success(f"{species.emoji} **{row['name']}** happily munches away. Fully fed!")

    @pet.command("claim", description="Collect your pet's earnings.", guild_only=True)
    async def pet_claim(self, ctx: Context) -> None:
        """Collect the cash your pet gathered since the last claim."""
        assert ctx.guild is not None
        row = await self.bot.db.economy.get_pet(ctx.author.id, ctx.guild.id)
        if row is None:
            await ctx.send_error("You don't have a pet.")
            return
        species = get_species(row["species"])
        if species is None:
            await ctx.send_error("Your pet's species no longer exists.")
            return

        now = ctx.message.created_at
        claim = compute_pet_claim(
            species,
            row["last_claim"].replace(tzinfo=datetime.UTC),
            row["last_fed"].replace(tzinfo=datetime.UTC),
            now=now,
        )
        if claim.amount <= 0:
            if claim.hunger.value == "starving":
                await ctx.send_error(f"**{row['name']}** is starving and gathered nothing. Feed them first!")
            else:
                await ctx.send_info(f"**{row['name']}** hasn't gathered anything yet. Check back later.")
            return

        scale = await self._payout_scale(ctx.author.id, ctx.guild.id)
        amount = round(claim.amount * scale)
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        await balance.add(cash=amount)
        await self.bot.db.economy.update_pet(
            ctx.author.id, ctx.guild.id, {"last_claim": now.replace(tzinfo=None)})
        await ctx.send_success(
            f"{species.emoji} **{row['name']}** brought you {Emojis.Economy.cash} "
            f"**{fnumb(amount)}** ({claim.hours:.1f}h of gathering).{self._scale_suffix(scale)}"
        )

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

        embed = discord.Embed(title="Economy Settings", colour=helpers.Colour.white())
        embed.set_author(name=ctx.guild.name, icon_url=get_asset_url(ctx.guild))  # type: ignore[arg-type]
        embed.add_field(name="Payout Multiplier", value=f"**x{settings.payout_multiplier:g}**")
        embed.add_field(name="Robbing", value="Enabled" if settings.rob_enabled else "Disabled")
        embed.add_field(name="Daily Base", value=f"{Emojis.Economy.cash} {fnumb(settings.daily_base)}")
        embed.add_field(name="Max Casino Bet", value=max_bet)
        embed.set_footer(text="economy-config multiplier/rob/daily-base/max-bet")
        await ctx.send(embed=embed)

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
