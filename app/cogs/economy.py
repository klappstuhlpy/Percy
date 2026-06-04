import datetime
from typing import Annotated, Literal

import discord
from discord.ext import commands

from app.core import Bot, Cog, converter
from app.core.models import Context, PermissionTemplate, command, describe, group
from app.core.pagination import LinePaginator
from app.services.economy import compute_daily, sell_price
from app.utils import fnumb, get_asset_url, helpers, pluralize
from config import Emojis


class Economy(Cog):
    """Economy commands"""

    emoji = Emojis.Economy.cash

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

        entries = [
            f"**{item['name']}** • {Emojis.Economy.cash} {fnumb(item['price'])}\n"
            f"{item['description'] or '*No description.*'}"
            for item in items
        ]
        embed = discord.Embed(title="Server Shop", description="", colour=helpers.Colour.white())
        embed.set_author(name=ctx.guild.name, icon_url=get_asset_url(ctx.guild))  # type: ignore[arg-type]
        await LinePaginator.start(ctx, entries=entries, embed=embed, location='description')

    @shop.command(
        "add",
        description="Add an item to the shop.",
        guild_only=True,
        user_permissions=PermissionTemplate.admin,
    )
    @describe(name="The item name (quote it if it has spaces).", price="The purchase price.", description="A description.")
    async def shop_add(
        self, ctx: Context, name: str, price: commands.Range[int, 1], *, description: str | None = None
    ) -> None:
        """Add an item to the server shop."""
        assert ctx.guild is not None
        record = await self.bot.db.economy.create_item(ctx.guild.id, name, description, price)
        if record is None:
            await ctx.send_error(f"An item named **{name}** already exists.")
            return
        await ctx.send_success(f"Added **{name}** to the shop for {Emojis.Economy.cash} **{fnumb(price)}**.")

    @shop.command(
        "remove",
        aliases=["delete", "rm"],
        description="Remove an item from the shop.",
        guild_only=True,
        user_permissions=PermissionTemplate.admin,
    )
    @describe(name="The item to remove.")
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
            entries.append(
                f"**{row['name']}**  ×{row['quantity']}\n"
                f"\N{SMALL ORANGE DIAMOND} {Emojis.Economy.cash} **{fnumb(line_value)}** "
                f"*(sell {fnumb(unit)} each)*"
            )

        embed = discord.Embed(
            description=(
                f"{Emojis.Economy.cash} Total sell value: **{fnumb(total_value)}**\n"
                f"\N{PACKAGE} **{pluralize(total_items):item}** across "
                f"**{pluralize(len(rows)):unique type}**\n\n"
            ),
            colour=helpers.Colour.white(),
        )
        embed.set_author(name=f"{user.display_name}'s Inventory", icon_url=get_asset_url(user))
        embed.set_thumbnail(url=get_asset_url(user))
        await LinePaginator.start(ctx, entries=entries, embed=embed, location="description")

    @command("use", description="Use an item from your inventory.", guild_only=True, hybrid=True)
    @describe(name="The item to use.")
    async def use(self, ctx: Context, *, name: str) -> None:
        """Use (consume one of) an item you own."""
        assert ctx.guild is not None
        item = await self.bot.db.economy.get_item(ctx.guild.id, name)
        if item is None:
            await ctx.send_error(f"No item named **{name}** exists in the shop.")
            return

        owned = await self.bot.db.economy.get_quantity(ctx.author.id, ctx.guild.id, item["id"])
        if owned < 1:
            await ctx.send_error(f"You don't own any **{item['name']}**.")
            return

        await self.bot.db.economy.remove_from_inventory(ctx.author.id, ctx.guild.id, item["id"], 1)
        await ctx.send_success(f"You used **{item['name']}**. You have **{owned - 1}** left.")


async def setup(bot: Bot) -> None:
    await bot.add_cog(Economy(bot))
