from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Iterable, Optional, Union, List

import asyncpg
import discord

from .utils.paginator import BasePaginator, LinePaginator
from .utils import cache, commands, helpers
from .utils.converters import aenumerate, get_asset_url
from .utils.formats import plonk_iterator
from itertools import accumulate

if TYPE_CHECKING:
    from typing_extensions import TypeAlias
    from bot import Percy
    from .utils.context import Context, GuildContext
    from asyncpg import Record, Connection, Pool

if TYPE_CHECKING:
    CommandName: TypeAlias = str
else:
    class CommandName(commands.Converter):
        async def convert(self, ctx: Context, argument: str) -> str:
            lowered = argument.lower()

            valid_commands = {
                c.qualified_name
                for c in ctx.bot.walk_commands()
                if c.cog_name not in ('Config', 'System')
            }

            if lowered not in valid_commands:
                raise commands.BadArgument(f'Command {lowered!r} is not valid.')

            return lowered


class ResolvedCommandPermissions:
    class _Entry:
        __slots__ = ('allow', 'deny')

        def __init__(self):
            self.allow: set[str] = set()
            self.deny: set[str] = set()

    def __init__(self, guild_id: int, records: list[tuple[str, int, bool]]):
        self.guild_id: int = guild_id

        self._lookup: defaultdict[Optional[int], ResolvedCommandPermissions._Entry] = defaultdict(self._Entry)

        for name, channel_id, whitelist in records:
            entry = self._lookup[channel_id]
            if whitelist:
                entry.allow.add(name)
            else:
                entry.deny.add(name)

    def _split(self, obj: str) -> list[str]:
        return list(accumulate(obj.split(), lambda x, y: f'{x} {y}'))

    def get_blocked_commands(self, channel_id: int) -> set[str]:
        if len(self._lookup) == 0:
            return set()

        guild = self._lookup[None]
        channel = self._lookup[channel_id]

        ret = guild.deny - guild.allow

        return ret | (channel.deny - channel.allow)

    def _is_command_blocked(self, name: str, channel_id: int) -> Optional[bool]:
        command_names = self._split(name)

        guild = self._lookup[None]
        channel = self._lookup[channel_id]

        blocked = None

        for command in command_names:
            if command in guild.deny:
                blocked = True

            if command in guild.allow:
                blocked = False

        for command in command_names:
            if command in channel.deny:
                blocked = True

            if command in channel.allow:
                blocked = False

        return blocked

    def is_command_blocked(self, name: str, channel_id: int) -> Optional[bool]:
        if len(self._lookup) == 0:
            return False
        return self._is_command_blocked(name, channel_id)

    def is_blocked(self, ctx: Context) -> Optional[bool]:
        if len(self._lookup) == 0:
            return False

        if isinstance(ctx.author, discord.Member) and ctx.author.guild_permissions.manage_guild:
            return False

        return self._is_command_blocked(ctx.command.qualified_name, ctx.channel.id)


class Config(commands.Cog):
    """Handles the Command Configuration for the bot.
    Enable or disable commands for specific users, channels or guild.
    """

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='green_shield', id=1104493156088696883)

    @cache.cache(strategy=cache.Strategy.LRU, maxsize=1024, ignore_kwargs=True)
    async def is_plonked(
            self,
            guild_id: int,
            member_id: int,
            channel: Optional[discord.VoiceChannel | discord.TextChannel | discord.Thread] = None,
            *,
            connection: Optional[Connection | Pool] = None,
            check_bypass: bool = True,
    ) -> bool:
        """|coro| @cached

        Checks if a member is plonked in a guild or channel.

        Parameters
        ----------
        guild_id: :class:`int`
            The guild ID to check.
        member_id: :class:`int`
            The member ID to check.
        channel: Optional[Union[:class:`discord.VoiceChannel`, :class:`discord.TextChannel`, :class:`discord.Thread`]]
            The channel to check.
        connection: Optional[Union[:class:`asyncpg.Connection`, :class:`asyncpg.Pool`]]
            The connection to use. Defaults to ``None``.
        check_bypass: :class:`bool`
            Whether to check if the member has the ``manage_guild`` permission.
            Defaults to ``True``.

        Returns
        -------
        :class:`bool`
            Whether the member is plonked.
        """
        if member_id in self.bot.blacklist or guild_id in self.bot.blacklist:
            return True

        if check_bypass:
            guild = self.bot.get_guild(guild_id)
            if guild is not None:
                member = await self.bot.get_or_fetch_member(guild, member_id)
                if member is not None and member.guild_permissions.manage_guild:
                    return False

        connection = connection or self.bot.pool

        if channel is None:
            query = "SELECT 1 FROM plonks WHERE guild_id=$1 AND entity_id=$2;"
            row = await connection.fetchrow(query, guild_id, member_id)
        else:
            if isinstance(channel, discord.Thread):
                query = "SELECT 1 FROM plonks WHERE guild_id=$1 AND entity_id IN ($2, $3, $4);"
                row = await connection.fetchrow(query, guild_id, member_id, channel.id, channel.parent_id)
            else:
                query = "SELECT 1 FROM plonks WHERE guild_id=$1 AND entity_id IN ($2, $3);"
                row = await connection.fetchrow(query, guild_id, member_id, channel.id)

        return row is not None

    async def bot_check_once(self, ctx: Context) -> bool:
        if ctx.guild is None:
            return True

        is_owner = await ctx.bot.is_owner(ctx.author)
        if is_owner:
            return True

        if isinstance(ctx.author, discord.Member):
            bypass = ctx.author.guild_permissions.manage_guild
            if bypass:
                return True

        return not await self.is_plonked(
            ctx.guild.id, ctx.author.id, channel=ctx.channel, check_bypass=False)

    @cache.cache()
    async def get_permissions(
            self, guild_id: int, *, connection: Optional[Connection | Pool] = None
    ) -> ResolvedCommandPermissions:
        """|coro| @cached

        Returns the resolved command permissions for the given guild.

        Parameters
        ----------
        guild_id: :class:`int`
            The guild ID to get the command permissions for.
        connection: Optional[Connection | Pool]
            The connection to use for the query.

        Returns
        -------
        :class:`ResolvedCommandPermissions`
            The resolved command permissions for the given guild.
        """
        connection = connection or self.bot.pool
        query = "SELECT name, channel_id, whitelist FROM command_config WHERE guild_id=$1;"

        records = await connection.fetch(query, guild_id)
        return ResolvedCommandPermissions(guild_id, records)

    async def bot_check(self, ctx: Context) -> bool:
        if ctx.guild is None:
            return True

        is_owner = await ctx.bot.is_owner(ctx.author)
        if is_owner:
            return True

        resolved = await self.get_permissions(ctx.guild.id)
        return not resolved.is_blocked(ctx)

    async def _bulk_ignore_entries(self, ctx: GuildContext, entries: Iterable[discord.abc.Snowflake]) -> None:
        async with ctx.db.acquire() as con:
            async with con.transaction():
                query = "SELECT entity_id FROM plonks WHERE guild_id=$1;"
                records = await con.fetch(query, ctx.guild.id)

                current_plonks = {r[0] for r in records}
                guild_id = ctx.guild.id
                to_insert = [(guild_id, e.id) for e in entries if e.id not in current_plonks]

                await con.copy_records_to_table('plonks', columns=['guild_id', 'entity_id'], records=to_insert)

                self.is_plonked.invalidate_containing(f'{ctx.guild.id!r}:')

    @commands.command(
        commands.group,
        name='config',
        aliases=['conf', 'settings'],
        description='Configure the bot for your server.',
        guild_only=True
    )
    async def config(self, ctx: GuildContext):
        """Handles the server or channel permission configuration for the bot."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help('config')

    @commands.command(
        config.group,
        name='ignore',
        aliases=['plonk'],
        description='Ignores text channels or members from using the bot.',
        invoke_without_command=True,
        guild_only=True
    )
    @commands.permissions(user=commands.PermissionTemplate.mod)
    async def ignore(
            self, ctx: GuildContext, *entities: Union[discord.TextChannel, discord.Member, discord.VoiceChannel]
    ):
        """Ignores text channels or members from using the bot.
        If no channel or member is specified, the current channel is ignored.
        Users with Administrator can still use the bot, regardless of ignore
        status.
        To use this command you must have Ban Members and Manage Messages permissions.
        """

        if len(entities) == 0:
            # shortcut for a single insert
            query = "INSERT INTO plonks (guild_id, entity_id) VALUES ($1, $2) ON CONFLICT DO NOTHING;"
            await ctx.db.execute(query, ctx.guild.id, ctx.channel.id)

            self.is_plonked.invalidate_containing(f'{ctx.guild.id!r}:')
        else:
            await self._bulk_ignore_entries(ctx, entities)

        await ctx.stick(True, f'Successfully ingored **{len(entities)}** entities.')

    @commands.command(
        ignore.command,
        name='list',
        description='Tells you what channels or members are currently ignored in this server.',
        cooldown=commands.CooldownMap(rate=2, per=60.0, type=commands.BucketType.guild),
        guild_only=True
    )
    @commands.permissions(user=commands.PermissionTemplate.mod)
    async def ignore_list(self, ctx: GuildContext):
        """Tells you what channels or members are currently ignored in this server.
        To use this command you must have Ban Members and Manage Messages permissions.
        """

        query = "SELECT entity_id FROM plonks WHERE guild_id=$1;"

        guild = ctx.guild
        records = await ctx.db.fetch(query, guild.id)

        if len(records) == 0:
            raise commands.CommandError('There are no ignored channels or members in this server.')

        class PlonkedPaginator(BasePaginator[asyncpg.Record]):
            async def format_page(self, entries: List[asyncpg.Record], /) -> discord.Embed:
                entries = plonk_iterator(ctx.bot, ctx.guild, entries)
                embed = discord.Embed(timestamp=discord.utils.utcnow(), colour=helpers.Colour.white())
                embed.set_footer(text=f'Requested by {ctx.author}', icon_url=get_asset_url(ctx.author))
                embed.set_author(name=f'Ignored Channels/Members', icon_url=get_asset_url(ctx.guild))
                pages = []
                async for index, entry in aenumerate(entries, start=self.numerate_start):
                    pages.append(f'{index + 1}. {entry}')

                embed.description = '\n'.join(pages)
                return embed

        await PlonkedPaginator.start(ctx, entries=records, per_page=15)

    @commands.command(
        ignore.command,
        name='all',
        description='Ignores every channel in the server from being processed.',
        guild_only=True
    )
    @commands.permissions(user=commands.PermissionTemplate.mod)
    async def _all(self, ctx: GuildContext):
        """Ignores every channel in the server from being processed.
        This works by adding every channel that the server currently has into
        the ignore list. If more channels are added then they will have to be
        ignored by using the ignore command.
        To use this command you must have Ban Members and Manage Messages permissions.
        """
        await self._bulk_ignore_entries(ctx, ctx.guild.text_channels)
        await ctx.stick(True, 'Successfully blocking all channels here.')

    @commands.command(
        ignore.command,
        name='clear',
        description='Clears all the currently set ignores.',
        guild_only=True
    )
    @commands.permissions(user=commands.PermissionTemplate.mod)
    async def ignore_clear(self, ctx: GuildContext):
        """Clears all the currently set ignores.
        To use this command you must have Ban Members and Manage Messages permissions.
        """

        query = "DELETE FROM plonks WHERE guild_id=$1;"
        await ctx.db.execute(query, ctx.guild.id)
        self.is_plonked.invalidate_containing(f'{ctx.guild.id!r}:')
        await ctx.stick(True, 'Successfully cleared all ignores.')

    @commands.command(
        config.group,
        name='unignore',
        aliases=['unplonk'],
        description='Allows channels or members to use the bot again.',
        invoke_without_command=True,
        guild_only=True
    )
    @commands.permissions(user=commands.PermissionTemplate.mod)
    async def unignore(
            self, ctx: GuildContext, *entities: Union[discord.TextChannel, discord.Member, discord.VoiceChannel]):
        """Allows channels or members to use the bot again.
        If nothing is specified, it unignores the current channel.
        To use this command you must have Ban Members and Manage Messages permissions.
        """

        if len(entities) == 0:
            query = "DELETE FROM plonks WHERE guild_id=$1 AND entity_id=$2;"
            await ctx.db.execute(query, ctx.guild.id, ctx.channel.id)
        else:
            query = "DELETE FROM plonks WHERE guild_id=$1 AND entity_id = ANY($2::bigint[]);"
            entity_ids = [c.id for c in entities]
            await ctx.db.execute(query, ctx.guild.id, entity_ids)

        self.is_plonked.invalidate_containing(f'{ctx.guild.id!r}:')
        await ctx.stick(True, f'Successfully unignored **{len(entities)}** entities.')

    @commands.command(
        unignore.command,
        name='all',
        description='Unignores every channel in the server.',
        guild_only=True
    )
    @commands.permissions(user=commands.PermissionTemplate.mod)
    async def unignore_all(self, ctx: GuildContext):
        """An alias for ignore clear command."""
        await ctx.invoke(self.ignore_clear)  # type: ignore

    @commands.command(
        config.group,
        name='server',
        description='Toggles a command on or off.',
        invoke_without_command=True,
        guild_only=True
    )
    @commands.permissions(user=commands.PermissionTemplate.mod)
    async def server(self, ctx: GuildContext):
        """Handles the server-specific permissions."""
        pass

    @commands.command(
        config.group,
        name='channel',
        description='Toggles a command on or off for a specific channel.',
        invoke_without_command=True,
        guild_only=True
    )
    @commands.permissions(user=commands.PermissionTemplate.mod)
    async def channel(self, ctx: GuildContext):
        """Handles the channel-specific permissions."""
        pass

    async def command_toggle(
            self,
            pool: Pool,
            guild_id: int,
            channel_id: Optional[int],
            name: str,
            *,
            whitelist: bool = True,
    ) -> None:
        # clear the cache
        self.get_permissions.invalidate(self, guild_id)

        if channel_id is None:
            subcheck = 'channel_id IS NULL'
            args = (guild_id, name)
        else:
            subcheck = 'channel_id=$3'
            args = (guild_id, name, channel_id)

        async with pool.acquire() as connection:
            async with connection.transaction():
                # delete the previous entry regardless of what it was
                query = f"DELETE FROM command_config WHERE guild_id=$1 AND name=$2 AND {subcheck};"

                # DELETE <num>
                await connection.execute(query, *args)

                query = "INSERT INTO command_config (guild_id, channel_id, name, whitelist) VALUES ($1, $2, $3, $4);"

                try:
                    await connection.execute(query, guild_id, channel_id, name, whitelist)
                except asyncpg.UniqueViolationError:
                    raise commands.CommandError(
                        'This command is already disabled.' if not whitelist else 'This command is already explicitly enabled.')

    @commands.command(
        channel.command,
        name='disable',
        description='Disables a command for this channel.',
        guild_only=True
    )
    @commands.permissions(user=commands.PermissionTemplate.mod)
    async def channel_disable(self, ctx: GuildContext, *, command: CommandName):
        """Disables a command for this channel."""

        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, ctx.channel.id, command, whitelist=False)
        except:  # noqa
            pass
        else:
            await ctx.stick(True, 'Command successfully disabled for this channel.')

    @commands.command(
        channel.command,
        name='enable',
        description='Enables a command for this channel.',
        guild_only=True
    )
    @commands.permissions(user=commands.PermissionTemplate.mod)
    async def channel_enable(self, ctx: GuildContext, *, command: CommandName):
        """Enables a command for this channel."""

        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, ctx.channel.id, command, whitelist=True)
        except:  # noqa
            pass
        else:
            await ctx.stick(True, 'Command successfully enabled for this channel.')

    @commands.command(
        server.command,
        name='disable',
        description='Disables a command for this server.',
        guild_only=True
    )
    @commands.permissions(user=commands.PermissionTemplate.mod)
    async def server_disable(self, ctx: GuildContext, *, command: CommandName):
        """Disables a command for this server."""

        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, None, command, whitelist=False)
        except:  # noqa
            pass
        else:
            await ctx.stick(True, 'Command successfully disabled for this server')

    @commands.command(
        server.command,
        name='enable',
        description='Enables a command for this server.',
        guild_only=True
    )
    @commands.permissions(user=commands.PermissionTemplate.mod)
    async def server_enable(self, ctx: GuildContext, *, command: CommandName):
        """Enables a command for this server."""

        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, None, command, whitelist=True)
        except:  # noqa
            pass
        else:
            await ctx.stick(True, 'Command successfully enabled for this server.')

    @commands.command(
        config.command,
        name='enable',
        description='Enables a command for the server or a channel.',
        guild_only=True
    )
    @commands.permissions(user=commands.PermissionTemplate.mod)
    async def config_enable(self, ctx: GuildContext, channel: Optional[discord.TextChannel], *, command: CommandName):
        """Enables a command the server or a channel."""

        channel_id = channel.id if channel else None
        human_friendly = channel.mention if channel else 'the server'
        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, channel_id, command, whitelist=True)
        except:  # noqa
            pass
        else:
            await ctx.stick(True, f'Command successfully enabled for {human_friendly}.')

    @commands.command(
        config.command,
        name='disable',
        description='Disables a command for the server or a channel.',
        guild_only=True
    )
    @commands.permissions(user=commands.PermissionTemplate.mod)
    async def config_disable(self, ctx: GuildContext, channel: Optional[discord.TextChannel], *, command: CommandName):
        """Disables a command for the server or a channel."""

        channel_id = channel.id if channel else None
        human_friendly = channel.mention if channel else 'the server'
        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, channel_id, command, whitelist=False)
        except:  # noqa
            pass
        else:
            await ctx.stick(True, f'Command successfully disabled for {human_friendly}.')

    @commands.command(
        config.command,
        name='disabled',
        description='Shows the disabled commands for the channel given.',
        guild_only=True
    )
    @commands.permissions(user=commands.PermissionTemplate.mod)
    async def config_disabled(
            self, ctx: GuildContext, *, channel: Optional[Union[discord.TextChannel, discord.VoiceChannel]] = None
    ):
        """Shows the disabled commands for the channel given."""

        channel_id: int
        if channel is None:
            if isinstance(ctx.channel, discord.Thread):
                channel_id = ctx.channel.parent_id
            else:
                channel_id = ctx.channel.id
        else:
            channel_id = channel.id

        resolved = await self.get_permissions(ctx.guild.id)
        disabled = list(resolved.get_blocked_commands(channel_id))

        if not disabled:
            raise commands.CommandError('There are no disabled commands for this channel.')

        embed = discord.Embed(timestamp=discord.utils.utcnow(),
                              color=self.bot.colour.white())
        embed.set_author(name=f'Disabled Commands', icon_url=get_asset_url(ctx.guild))
        await LinePaginator.start(ctx, entries=disabled, per_page=15, embed=embed, location='description')

    @commands.command(
        config.group,
        name='global',
        description='Handles global bot configuration.',
        hidden=True
    )
    @commands.is_owner()
    async def _global(self, ctx: GuildContext):
        """Handles global bot configuration."""
        pass

    @commands.command(
        _global.command,
        name='block',
        description='Blocks a user or guild globally.',
        hidden=True
    )
    @commands.is_owner()
    async def global_block(self, ctx: GuildContext, object_id: int):
        """Blocks a user or guild globally."""
        await self.bot.add_to_blacklist(object_id)
        await ctx.stick(True, 'User or guild blocked globally.')

    @commands.command(
        _global.command,
        name='unblock',
        description='Unblocks a user or guild globally.',
        hidden=True
    )
    @commands.is_owner()
    async def global_unblock(self, ctx: GuildContext, object_id: int):
        """Unblocks a user or guild globally."""
        await self.bot.remove_from_blacklist(object_id)
        await ctx.stick(True, 'User or guild unblocked globally.')


async def setup(bot):
    await bot.add_cog(Config(bot))
