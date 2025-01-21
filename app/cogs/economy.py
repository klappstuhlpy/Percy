import logging
from typing import Annotated, Literal

import discord
from discord.ext import commands

from app.core import Cog, converter
from app.core.models import Context, PermissionTemplate, command, describe, group
from app.utils import get_asset_url, helpers, fnumb
from app.utils.pagination import LinePaginator
from config import Emojis

log = logging.getLogger(__name__)


class Economy(Cog):
    """Economy commands"""

    emoji = Emojis.Economy.cash

    @command(
        'set-money',
        aliases=['setbal', 'set-balance'],
        description='Sets a user\'s balance',
        guild_only=True,
        user_permissions=PermissionTemplate.admin
    )
    @describe(
        member='The user to set the balance for.',
        amount='The amount to set the balance to.',
        to='Whether to set the balance to the bank or cash.'
    )
    async def set_money(
            self,
            ctx: Context,
            member: Annotated[discord.Member, converter.MemberConverter],
            amount: int,
            to: Literal['bank', 'cash']
    ) -> None:
        """Sets a user's balance"""
        if member.bot:
            await ctx.send_error('Cannot set a bot\'s balance.')
            return

        balance = await ctx.db.get_user_balance(member.id, ctx.guild.id)
        if to == 'bank':
            await balance.update(bank=amount)
        else:
            await balance.update(cash=amount)

        await ctx.send_success(f'Successfully set **{member.display_name}\'s** {to} to {Emojis.Economy.cash} **{fnumb(amount)}**.')

    @command(
        'add-money-role',
        description='Adds a certain amount of money to all users with the specified role.',
        guild_only=True,
        user_permissions=PermissionTemplate.admin
    )
    @describe(
        role='The role to add the money to.',
        amount='The amount to add to the balance.',
        to='Whether to add the balance to the bank or cash.'
    )
    async def add_money_role(
            self,
            ctx: Context,
            role: Annotated[discord.Role, commands.RoleConverter],
            amount: int,
            to: Literal['bank', 'cash']
    ) -> None:
        """Sets a user's balance"""
        for member in role.members:
            if member.bot:
                continue

            balance = await ctx.db.get_user_balance(member.id, ctx.guild.id)
            if to == 'bank':
                await balance.add(bank=amount)
            else:
                await balance.add(cash=amount)

        await ctx.send_success(f'Successfully added {Emojis.Economy.cash} **{fnumb(amount)}** to all users with the role **{role.name}**.')

    @command(
        'remove-money',
        aliases=['rmbal', 'rm-money'],
        description='Removes from a user\'s balance',
        guild_only=True,
        user_permissions=PermissionTemplate.admin
    )
    @describe(
        member='The user to remove the balance from.',
        amount='The amount to remove from the balance.',
        to='Whether to remove the balance from the bank or cash.'
    )
    async def remove_money(
            self,
            ctx: Context,
            member: Annotated[discord.Member, converter.MemberConverter],
            amount: int,
            to: Literal['bank', 'cash']
    ) -> None:
        """Removes from a user's balance"""
        if member.bot:
            await ctx.send_error('Cannot remove from a bot\'s balance.')
            return

        balance = await ctx.db.get_user_balance(member.id, ctx.guild.id)

        if (to == 'bank' and balance.bank < amount) or (to == 'cash' and balance.cash < amount):
            await ctx.send_error('Cannot remove more than the user\'s balance.')
            return

        if to == 'bank':
            await balance.remove(bank=amount)
        else:
            await balance.remove(cash=amount)

        await ctx.send_success(f'Successfully removed {Emojis.Economy.cash} **{fnumb(amount)}** from **{member.display_name}\'s** {to}.')

    @command(
        'deposit',
        aliases=['dep'],
        description='Deposits money into your bank.',
        guild_only=True,
        hybrid=True
    )
    @describe(amount='The amount to deposit.')
    async def deposit(self, ctx: Context, amount: int) -> None:
        """Deposits money into your bank."""
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        if balance.cash < amount:
            await ctx.send_error('Cannot deposit more than your balance.')
            return

        await balance.remove(cash=amount)
        await balance.add(bank=amount)
        await ctx.send_success(f'Successfully deposited {Emojis.Economy.cash} **{fnumb(amount)}** into your bank.')

    @command(
        'withdraw',
        description='Withdraws money from your bank.',
        guild_only=True,
        hybrid=True
    )
    @describe(amount='The amount to withdraw.')
    async def withdraw(self, ctx: Context, amount: int) -> None:
        """Withdraws money from your bank."""
        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        if balance.bank < amount:
            await ctx.send_error('Cannot withdraw more than your bank balance.')
            return

        await balance.remove(bank=amount)
        await balance.add(cash=amount)
        await ctx.send_success(f'Successfully withdrew {Emojis.Economy.cash} **{fnumb(amount)}** from your bank.')

    @command(
        'transfer',
        description='Transfers money to another user.',
        guild_only=True,
        hybrid=True
    )
    @describe(
        member='The user to transfer the money to.',
        amount='The amount to transfer.'
    )
    async def transfer(self, ctx: Context, member: Annotated[discord.Member, converter.MemberConverter], amount: int) -> None:
        """Transfers money to another user."""
        if member.bot:
            await ctx.send_error('Cannot transfer to a bot.')
            return

        balance = await ctx.db.get_user_balance(ctx.author.id, ctx.guild.id)
        if balance.cash < amount:
            await ctx.send_error('Cannot transfer more than your balance.')
            return

        await balance.remove(cash=amount)
        balance = await ctx.db.get_user_balance(member.id, ctx.guild.id)
        await balance.add(cash=amount)
        await ctx.send_success(f'Successfully transferred {Emojis.Economy.cash} **{fnumb(amount)}** to **{member.display_name}**.')

    @group(
        'balance',
        alias='bal',
        description='Shows a user\'s balance',
        guild_only=True,
        hybrid=True
    )
    @describe(user='The user to show the balance for.')
    async def balance(self, ctx: Context, member: Annotated[discord.Member, converter.MemberConverter] = None) -> None:
        """Shows your balance"""
        if member and member.bot:
            await ctx.send_error('Cannot get a bot\'s balance.')
            return

        user = member or ctx.author
        balance = await ctx.db.get_user_balance(user.id, ctx.guild.id)
        embed = discord.Embed(
            description='Server Leaderboard Rank: x',
            colour=helpers.Colour.white()
        )
        embed.set_author(name=f'{user.display_name}\'s Balance', icon_url=get_asset_url(user))
        embed.add_field(name='Cash', value=f'{Emojis.Economy.cash} **{fnumb(balance.cash)}**')
        embed.add_field(name='Bank', value=f'{Emojis.Economy.cash} **{fnumb(balance.bank)}**')
        embed.add_field(name='Total', value=f'{Emojis.Economy.cash} **{fnumb(balance.total)}**')
        await ctx.send(embed=embed)

    @balance.command(
        'leaderboard',
        alias='top',
        description='Shows the leaderboard of the server',
        guild_only=True
    )
    async def leaderboard(self, ctx: Context) -> None:
        """Shows the leaderboard of the server."""
        balances = await ctx.db.get_guild_balances(ctx.guild.id)
        total = sum(balance.total for balance in balances)

        users = [
            f'**{index}.** {self.bot.get_user(balance.user_id).mention} • {Emojis.Economy.cash} **{fnumb(balance.total)}**'
            for index, balance in enumerate(balances, 1)]

        embed = discord.Embed(
            title='Economy Leaderboard',
            description='This is the server\'s leaderboard.\n\n',
            colour=helpers.Colour.white()
        )
        embed.set_author(name=ctx.guild.name, icon_url=get_asset_url(ctx.guild))
        embed.set_footer(text=f'Total Server Money: {fnumb(total)}',
                         icon_url=discord.PartialEmoji.from_str(Emojis.Economy.cash).url)
        await LinePaginator.start(ctx, entries=users, embed=embed, location='description')


async def setup(bot) -> None:
    await bot.add_cog(Economy(bot))
