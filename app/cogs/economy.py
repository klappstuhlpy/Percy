import datetime
from typing import Annotated, Any, Literal

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands
from discord.ext.commands import Range

from app.core import Accent, Bot, Cog, converter, make_notice
from app.core.models import Context, PermissionTemplate, command, cooldown, describe, group
from app.core.pagination import LinePaginator
from app.core.timer import Timer
from app.services.economy import (
    FISHING_COOLDOWN,
    FISHING_TABLE,
    HUNTING_COOLDOWN,
    HUNTING_TABLE,
    boost_multiplier,
    compute_daily,
    describe_effect,
    pick_weighted_winner,
    roll_loot,
    roll_lootbox,
    sell_price,
    validate_item_effect,
)
from app.utils import fnumb, fuzzy, get_asset_url, helpers, pluralize, timetools
from config import Emojis

#: Display labels for active boost kinds.
BOOST_LABELS = {'xp': 'leveling XP', 'loot': 'fishing & hunting payouts'}


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

    @command(
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

    @command(
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
        self, ctx: Context, role: Annotated[discord.Role, commands.RoleConverter], amount: int, to: Literal["bank", "cash"]
    ) -> None:
        """Sets a user's balance"""
        for member in role.members:
            if member.bot:
                continue

            balance = await ctx.db.get_user_balance(member.id, ctx.guild.id)  # type: ignore[union-attr]
            if to == "bank":
                await balance.add(bank=amount)
            else:
                await balance.add(cash=amount)

        await ctx.send_success(
            f"Successfully added {Emojis.Economy.cash} **{fnumb(amount)}** to all users with the role **{role.name}**."
        )

    @command(
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
        amount: int,
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
    async def deposit(self, ctx: Context, amount: int) -> None:
        """Deposits money into your bank."""
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)  # type: ignore[union-attr]
        if balance.cash < amount:
            await ctx.send_error("Cannot deposit more than your balance.")
            return

        await balance.remove(cash=amount)
        await balance.add(bank=amount)
        await ctx.send_success(f"Successfully deposited {Emojis.Economy.cash} **{fnumb(amount)}** into your bank.")

    @command("withdraw", description="Withdraws money from your bank.", guild_only=True, hybrid=True)
    @describe(amount="The amount to withdraw.")
    async def withdraw(self, ctx: Context, amount: int) -> None:
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
        self, ctx: Context, member: Annotated[discord.Member, converter.MemberConverter], amount: int
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

        users = [
            f"**{index}.** {self.bot.get_user(balance.user_id).mention} • {Emojis.Economy.cash} **{fnumb(balance.total)}**"  # type: ignore[union-attr]
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

        last_claim = record["last_claim"].replace(tzinfo=datetime.UTC) if record else None
        streak = record["streak"] if record else 0
        now = ctx.message.created_at

        result = compute_daily(last_claim, streak, now=now)
        if not result.claimed:
            assert result.next_available is not None
            await ctx.send_error(
                f"You've already claimed your daily reward. Come back {discord.utils.format_dt(result.next_available, 'R')}."
            )
            return

        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        await balance.add(cash=result.amount)
        await self.bot.db.economy.set_daily(ctx.author.id, ctx.guild.id, now.replace(tzinfo=None), result.streak)

        await ctx.send_success(
            f"You claimed {Emojis.Economy.cash} **{fnumb(result.amount)}**! "
            f"\N{FIRE} Streak: **{pluralize(result.streak):day}**."
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
        effect: Literal["none", "cash", "lootbox", "role", "xp_boost", "loot_boost"] = "none",
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
        boost_lines = "".join(
            f"\N{HIGH VOLTAGE SIGN} **+{row['multiplier'] - 1.0:.0%} {BOOST_LABELS.get(row['kind'], row['kind'])}** "
            f"— ends {discord.utils.format_dt(row['expires_at'].replace(tzinfo=datetime.UTC), 'R')}\n"
            for row in boosts
        )

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

    @command("use", description="Use an item from your inventory.", guild_only=True, hybrid=True)
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
        amount = round(catch.amount * boost)
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        await balance.add(cash=amount)

        if catch.amount <= 5:
            await ctx.send_info(f"{catch.emoji} You reeled in **{catch.name}** — barely worth it.")
            return
        suffix = f" *(+{boost - 1.0:.0%} loot boost)*" if boost > 1.0 else ""
        await ctx.send_success(
            f"{catch.emoji} You caught **{catch.name}** and sold it for "
            f"{Emojis.Economy.cash} **{fnumb(amount)}**!{suffix}"
        )

    @command("hunt", description="Head out hunting for a bigger, riskier payout.", guild_only=True, hybrid=True)
    @cooldown(1, HUNTING_COOLDOWN)
    async def hunt(self, ctx: Context) -> None:
        """Go hunting — higher payouts and variance than fishing, on a longer cooldown."""
        assert ctx.guild is not None
        catch = roll_loot(HUNTING_TABLE)
        boost = await self.bot.db.economy.get_boost_multiplier(ctx.author.id, ctx.guild.id, "loot")
        amount = round(catch.amount * boost)
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        await balance.add(cash=amount)

        if catch.amount <= 10:
            await ctx.send_info(f"{catch.emoji} You found **{catch.name}** and came back empty-handed.")
            return
        suffix = f" *(+{boost - 1.0:.0%} loot boost)*" if boost > 1.0 else ""
        await ctx.send_success(
            f"{catch.emoji} You bagged **{catch.name}** worth "
            f"{Emojis.Economy.cash} **{fnumb(amount)}**!{suffix}"
        )

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
            ctx.guild.id, ctx.channel.id, int(ticket_price), int(ticket_price), when.replace(tzinfo=None)
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


async def setup(bot: Bot) -> None:
    await bot.add_cog(Economy(bot))
