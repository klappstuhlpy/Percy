from typing import Literal, Optional, Annotated, List

import discord

from bot import Percy
from cogs.utils import commands, constants, errors, helpers
from cogs.utils.commands import PermissionTemplate
from cogs.utils.context import Context
from cogs.utils.helpers import PostgresItem
from cogs.utils.paginator import BasePaginator, T
from launcher import get_logger

log = get_logger(__name__)
cash_emoji = constants.cash_emoji


class BalanceError(errors.BadArgument):
    """Base error for balance errors"""
    pass


class Balance(PostgresItem):
    """Represents a user's balance"""

    user_id: int
    guild_id: int
    cash: int
    bank: int

    __slots__ = ('bot', 'user_id', 'guild_id', 'cash', 'bank')

    def __init__(self, bot: Percy, **kwargs):
        super().__init__(**kwargs)
        self.bot: Percy = bot

    @property
    def total(self) -> int:
        """Gets the total amount of money a user has"""
        return self.cash + self.bank

    async def set(self, balance: int, to: Literal['bank', 'cash']) -> None:
        """Sets a user's balance"""

        if balance < 0:
            raise BalanceError("Balance cannot be negative")

        query = f"UPDATE economy SET {to} = $1 WHERE user_id = $2 AND guild_id = $3;"
        await self.bot.pool.execute(query, balance, self.user_id, self.guild_id)

    async def add(self, amount: int, to: Literal['bank', 'cash']) -> None:
        """Adds to a user's balance"""
        balance = self.cash if to == 'cash' else self.bank
        await self.set(balance + amount, to)

    async def remove(self, amount: int, to: Literal['bank', 'cash']) -> None:
        """Removes from a user's balance"""
        balance = self.cash if to == 'cash' else self.bank
        await self.set(balance - amount, to)


class Economy(commands.Cog):
    """Economy commands"""
    def __init__(self, bot: Percy):
        self.bot: Percy = bot

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='cash', id=1195034729083326504)

    async def get_balance(self, user_id: int, guild_id: int) -> Optional[Balance]:
        """Gets the balance of a user"""
        query = "SELECT * FROM economy WHERE user_id = $1 AND guild_id = $2;"
        record = await self.bot.pool.fetchrow(query, user_id, guild_id)
        if not record:
            query = "INSERT INTO economy (user_id, guild_id, cash, bank) VALUES ($1, $2, 0, 0) RETURNING *;"
            record = await self.bot.pool.fetchrow(query, user_id, guild_id)
        return Balance(self.bot, record=record)

    async def get_server_balances(self, guild_id: int) -> list[Balance]:
        """Gets the balances of all users in a guild"""
        query = "SELECT * FROM economy WHERE guild_id = $1;"
        records = await self.bot.pool.fetch(query, guild_id)
        return [Balance(self.bot, record=record) for record in records]

    @commands.command(
        commands.core_command,
        name='set-money',
        aliases=['setbal', 'set-balance'],
        description='Sets a user\'s balance',
    )
    @commands.permissions(user=PermissionTemplate.admin)
    async def set_money(
            self,
            ctx: Context,
            user: Annotated[discord.User, commands.UserConverter],
            amount: int,
            to: Literal['bank', 'cash']
    ):
        """Sets a user's balance"""
        balance = await self.get_balance(user.id, ctx.guild.id)
        await balance.set(amount, to)
        await ctx.stick(True, f'Successfully set **{user.display_name}\'s** {to} to {cash_emoji} **{amount:,}**.')

    @commands.command(
        commands.core_command,
        name='add-money-role',
        description='Adds a certain amount of money to all users with the specified role.',
    )
    @commands.permissions(user=PermissionTemplate.admin)
    async def add_money_role(
            self,
            ctx: Context,
            role: Annotated[discord.Role, commands.RoleConverter],
            amount: int,
            to: Literal['bank', 'cash']
    ):
        """Sets a user's balance"""
        for member in role.members:
            balance = await self.get_balance(member.id, ctx.guild.id)
            await balance.add(amount, to)

        await ctx.stick(True, f'Successfully added {cash_emoji} **{amount:,}** to all users with the role **{role.name}**.')

    @commands.command(
        commands.core_command,
        name='remove-money',
        aliases=['rmbal', 'rm-money'],
        description='Removes from a user\'s balance',
    )
    @commands.permissions(user=PermissionTemplate.admin)
    async def remove_money(
            self,
            ctx: Context,
            user: Annotated[discord.User, commands.UserConverter],
            amount: int,
            to: Literal['bank', 'cash']
    ):
        """Removes from a user's balance"""
        balance = await self.get_balance(user.id, ctx.guild.id)

        if (to == 'bank' and balance.bank < amount) or (to == 'cash' and balance.cash < amount):
            raise BalanceError('Cannot remove more than the user\'s balance.')

        await balance.remove(amount, to)
        await ctx.stick(True, f'Successfully removed {cash_emoji} **{amount:,}** from **{user.display_name}\'s** {to}.')

    @commands.command(
        name='deposit',
        aliases=['dep'],
        description='Deposits money into your bank.',
    )
    async def deposit(self, ctx: Context, amount: int):
        """Deposits money into your bank."""
        balance = await self.get_balance(ctx.author.id, ctx.guild.id)
        if balance.cash < amount:
            raise BalanceError('Cannot deposit more than your balance.')

        await balance.remove(amount, 'cash')
        await balance.add(amount, 'bank')
        await ctx.stick(True, f'Successfully deposited {cash_emoji} **{amount:,}** into your bank.')

    @commands.command(
        name='withdraw',
        description='Withdraws money from your bank.',
    )
    async def withdraw(self, ctx: Context, amount: int):
        """Withdraws money from your bank."""
        balance = await self.get_balance(ctx.author.id, ctx.guild.id)
        if balance.bank < amount:
            raise BalanceError('Cannot withdraw more than your balance.')

        await balance.remove(amount, 'bank')
        await balance.add(amount, 'cash')
        await ctx.stick(True, f'Successfully withdrew {cash_emoji} **{amount:,}** from your bank.')

    @commands.command(
        name='transfer',
        description='Transfers money to another user.',
    )
    async def transfer(self, ctx: Context, user: Annotated[discord.User, commands.UserConverter], amount: int):
        """Transfers money to another user."""
        balance = await self.get_balance(ctx.author.id, ctx.guild.id)
        if balance.cash < amount:
            raise BalanceError('Cannot transfer more than your balance.')

        await balance.remove(amount, 'cash')
        balance = await self.get_balance(user.id, ctx.guild.id)
        await balance.add(amount, 'cash')
        await ctx.stick(True, f'Successfully transferred {cash_emoji} **{amount:,}** to **{user.display_name}**.')

    @commands.command(
        name='leaderboard',
        description='Shows the leaderboard of the server',
    )
    async def leaderboard(self, ctx: Context):
        """Shows the leaderboard of the server."""
        balances = await self.get_server_balances(ctx.guild.id)
        total = sum(balance.total for balance in balances)

        users = [f'**{index}.** {self.bot.get_user(balance.user_id).mention} • {cash_emoji} **{balance.total:,}**'
                 for index, balance in enumerate(balances, 1)]

        class LeaderboardPaginator(BasePaginator):
            async def format_page(self, entries: List[T], /) -> discord.Embed:
                embed = discord.Embed(
                    description='This is the server\'s leaderboard.\n\n',
                    colour=helpers.Colour.darker_red()
                )
                embed.description += '\n'.join(entries)
                embed.set_author(name=f'{ctx.guild.name}\'s Economy Leaderboard', icon_url=ctx.guild.icon.url)
                embed.set_footer(text=f'Total Server Money: {total:,}', icon_url=cash_emoji.url)
                return embed

        await LeaderboardPaginator.start(ctx, entries=users)

    @commands.command(
        name='balance',
        aliases=['bal'],
        description='Shows a user\'s balance',
    )
    async def balance(self, ctx: Context, user: Annotated[discord.User, commands.UserConverter] = None):
        """Shows your balance"""
        user = user or ctx.author
        balance = await self.get_balance(user.id, ctx.guild.id)
        embed = discord.Embed(
            description='Server Leaderboard Rank: x',
            colour=self.bot.colour.darker_red()
        )
        embed.set_author(name=f'{user.display_name}\'s Balance', icon_url=user.avatar.url)
        embed.add_field(name='Cash', value=f'{cash_emoji} **{balance.cash:,}**')
        embed.add_field(name='Bank', value=f'{cash_emoji} **{balance.bank:,}**')
        embed.add_field(name='Total', value=f'{cash_emoji} **{balance.total:,}**')
        await ctx.send(embed=embed)


async def setup(bot: Percy):
    await bot.add_cog(Economy(bot))
