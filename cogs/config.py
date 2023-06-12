from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Iterable, Optional, Union, List

import asyncpg
import discord
from discord.ext import commands

from cogs.utils.paginator import BasePaginator, LinePaginator
from . import command, command_permissions
from .utils import cache
from .utils.converters import aenumerate
from .utils.formats import plural, plonk_iterator

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
                raise commands.BadArgument(f'<:redTick:1079249771975413910> Command {lowered!r} is not valid.')

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
        from itertools import accumulate

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
        return discord.PartialEmoji(name="green_shield", id=1104493156088696883)

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

        is_plonked = await self.is_plonked(
            ctx.guild.id, ctx.author.id, channel=ctx.channel, check_bypass=False
        )

        return not is_plonked

    @cache.cache()
    async def get_command_permissions(
            self, guild_id: int, *, connection: Optional[Connection | Pool] = None
    ) -> ResolvedCommandPermissions:
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

        resolved = await self.get_command_permissions(ctx.guild.id)
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

    async def cog_command_error(self, ctx: Context, error: commands.CommandError):
        if isinstance(error, commands.BadArgument):
            await ctx.send(str(error))

    @command(
        commands.group,
        name='config',
        aliases=['conf', 'settings'],
        description='Configure the bot for your server.',
    )
    async def config(self, ctx: Context):
        """Handles the server or channel permission configuration for the bot."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help('config')
            ctx.message.reactions[0].users()

    @command(
        config.group,
        name='ignore',
        aliases=['plonk'],
        description='Ignores text channels or members from using the bot.',
        invoke_without_command=True,
    )
    @command_permissions(3, user=["ban_members", "manage_messages"])
    async def ignore(self, ctx: GuildContext,
                     *entities: Union[discord.TextChannel, discord.Member, discord.VoiceChannel]):
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

        await ctx.send(ctx.tick(True))

    @command(
        ignore.command,
        name='list',
        description='Tells you what channels or members are currently ignored in this server.',
    )
    @command_permissions(3, user=["ban_members", "manage_messages"])
    @commands.cooldown(2, 60.0, commands.BucketType.guild)
    async def ignore_list(self, ctx: GuildContext):
        """Tells you what channels or members are currently ignored in this server.
        To use this command you must have Ban Members and Manage Messages permissions.
        """

        query = "SELECT entity_id FROM plonks WHERE guild_id=$1;"

        guild = ctx.guild
        records = await ctx.db.fetch(query, guild.id)

        if len(records) == 0:
            return await ctx.send('<:redTick:1079249771975413910> There are no ignores set for this guild.')

        class PlonkedPaginator(BasePaginator[asyncpg.Record]):

            async def format_page(self, entries: List[asyncpg.Record], /) -> discord.Embed:
                entries = plonk_iterator(ctx.bot, ctx.guild, entries)
                embed = discord.Embed(timestamp=datetime.utcnow(), colour=0x2b2d31)
                embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.avatar.url)
                embed.set_author(name=f"Ignored Channels/Members", icon_url=ctx.guild.icon.url)
                pages = []
                async for index, entry in aenumerate(entries, start=self.numerate_start):
                    pages.append(f'{index + 1}. {entry}')

                embed.description = '\n'.join(pages)
                return embed

        await PlonkedPaginator.start(ctx, entries=records, per_page=15)

    @command(
        ignore.command,
        name='all',
        description='Ignores every channel in the server from being processed.',
    )
    @command_permissions(3, user=["ban_members", "manage_messages"])
    async def _all(self, ctx: GuildContext):
        """Ignores every channel in the server from being processed.
        This works by adding every channel that the server currently has into
        the ignore list. If more channels are added then they will have to be
        ignored by using the ignore command.
        To use this command you must have Ban Members and Manage Messages permissions.
        """
        await self._bulk_ignore_entries(ctx, ctx.guild.text_channels)
        await ctx.send('<:greenTick:1079249732364406854> Successfully blocking all channels here.')

    @command(
        ignore.command,
        name='clear',
        description='Clears all the currently set ignores.',
    )
    @command_permissions(3, user=["ban_members", "manage_messages"])
    async def ignore_clear(self, ctx: GuildContext):
        """Clears all the currently set ignores.
        To use this command you must have Ban Members and Manage Messages permissions.
        """

        query = "DELETE FROM plonks WHERE guild_id=$1;"
        await ctx.db.execute(query, ctx.guild.id)
        self.is_plonked.invalidate_containing(f'{ctx.guild.id!r}:')
        await ctx.send('<:greenTick:1079249732364406854> Successfully cleared all ignores.')

    @command(
        config.group,
        name='unignore',
        aliases=['unplonk'],
        description='Allows channels or members to use the bot again.',
        invoke_without_command=True,
    )
    @command_permissions(3, user=["ban_members", "manage_messages"])
    async def unignore(self, ctx: GuildContext,
                       *entities: Union[discord.TextChannel, discord.Member, discord.VoiceChannel]):
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
        await ctx.send(ctx.tick(True))

    @command(
        unignore.command,
        name='all',
        description='Unignores every channel in the server.',
    )
    @command_permissions(3, user=["ban_members", "manage_messages"])
    async def unignore_all(self, ctx: GuildContext):
        """An alias for ignore clear command."""
        await ctx.invoke(self.ignore_clear)  # type: ignore

    @command(
        config.group,
        name='server',
        description='Toggles a command on or off.',
        invoke_without_command=True,
    )
    @command_permissions(3, user=["ban_members", "manage_messages"])
    async def server(self, ctx: GuildContext):
        """Handles the server-specific permissions."""
        pass

    @command(
        config.group,
        name='channel',
        description='Toggles a command on or off for a specific channel.',
        invoke_without_command=True,
    )
    @command_permissions(3, user=["ban_members", "manage_messages"])
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
        self.get_command_permissions.invalidate(self, guild_id)

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
                    msg = '<:redTick:1079249771975413910> This command is already disabled.' if not whitelist else 'This command is already explicitly enabled.'
                    raise RuntimeError(msg)

    @command(
        channel.command,
        name='disable',
        description='Disables a command for this channel.',
    )
    async def channel_disable(self, ctx: GuildContext, *, command: CommandName):
        """Disables a command for this channel."""

        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, ctx.channel.id, command, whitelist=False)
        except RuntimeError as e:
            await ctx.send(str(e))
        else:
            await ctx.send('<:greenTick:1079249732364406854> Command successfully disabled for this channel.')

    @command(
        channel.command,
        name='enable',
        description='Enables a command for this channel.',
    )
    async def channel_enable(self, ctx: GuildContext, *, command: CommandName):
        """Enables a command for this channel."""

        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, ctx.channel.id, command, whitelist=True)
        except RuntimeError as e:
            await ctx.send(str(e))
        else:
            await ctx.send('<:greenTick:1079249732364406854> Command successfully enabled for this channel.')

    @command(
        server.command,
        name='disable',
        description='Disables a command for this server.',
    )
    async def server_disable(self, ctx: GuildContext, *, command: CommandName):
        """Disables a command for this server."""

        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, None, command, whitelist=False)
        except RuntimeError as e:
            await ctx.send(str(e))
        else:
            await ctx.send('<:greenTick:1079249732364406854> Command successfully disabled for this server')

    @command(
        server.command,
        name='enable',
        description='Enables a command for this server.',
    )
    async def server_enable(self, ctx: GuildContext, *, command: CommandName):
        """Enables a command for this server."""

        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, None, command, whitelist=True)
        except RuntimeError as e:
            await ctx.send(str(e))
        else:
            await ctx.send('<:greenTick:1079249732364406854>Command successfully enabled for this server.')

    @command(
        config.command,
        name='enable',
        description='Enables a command for the server or a channel.',
    )
    @command_permissions(3, user=["ban_members", "manage_messages"])
    async def config_enable(self, ctx: GuildContext, channel: Optional[discord.TextChannel], *, command: CommandName):
        """Enables a command the server or a channel."""

        channel_id = channel.id if channel else None
        human_friendly = channel.mention if channel else 'the server'
        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, channel_id, command, whitelist=True)
        except RuntimeError as e:
            await ctx.send(str(e))
        else:
            await ctx.send(f'<:greenTick:1079249732364406854> Command successfully enabled for {human_friendly}.')

    @command(
        config.command,
        name='disable',
        description='Disables a command for the server or a channel.',
    )
    @command_permissions(3, user=["ban_members", "manage_messages"])
    async def config_disable(self, ctx: GuildContext, channel: Optional[discord.TextChannel], *, command: CommandName):
        """Disables a command for the server or a channel."""

        channel_id = channel.id if channel else None
        human_friendly = channel.mention if channel else 'the server'
        try:
            await self.command_toggle(ctx.pool, ctx.guild.id, channel_id, command, whitelist=False)
        except RuntimeError as e:
            await ctx.send(str(e))
        else:
            await ctx.send(f'<:greenTick:1079249732364406854> Command successfully disabled for {human_friendly}.')

    @command(
        config.command,
        name='disabled',
        description='Shows the disabled commands for the channel given.',
    )
    @command_permissions(3, user=["ban_members", "manage_messages"])
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

        resolved = await self.get_command_permissions(ctx.guild.id)
        disabled = list(resolved.get_blocked_commands(channel_id))

        if not disabled:
            return await ctx.send('<:redTick:1079249771975413910> There are no disabled commands for this channel.')

        embed = discord.Embed(timestamp=datetime.utcnow(),
                              color=self.bot.colour.darker_red())
        embed.set_author(name=f'Disabled Commands', icon_url=ctx.guild.icon.url)
        await LinePaginator.start(ctx, entries=disabled, per_page=15, embed=embed, location='description')

    @command(
        config.group,
        name='global',
        description='Handles global bot configuration.',
        hidden=True
    )
    @commands.is_owner()
    async def _global(self, ctx: GuildContext):
        """Handles global bot configuration."""
        pass

    @command(
        _global.command,
        name='block',
        description='Blocks a user or guild globally.',
    )
    async def global_block(self, ctx: GuildContext, object_id: int):
        """Blocks a user or guild globally."""
        await self.bot.add_to_blacklist(object_id)
        await ctx.send(ctx.tick(True))

    @command(
        _global.command,
        name='unblock',
        description='Unblocks a user or guild globally.',
    )
    async def global_unblock(self, ctx: GuildContext, object_id: int):
        """Unblocks a user or guild globally."""
        await self.bot.remove_from_blacklist(object_id)
        await ctx.send(ctx.tick(True))


async def setup(bot):
    await bot.add_cog(Config(bot))
