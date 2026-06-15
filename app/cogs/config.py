from __future__ import annotations

from collections import defaultdict
from itertools import accumulate
from typing import TYPE_CHECKING, Annotated

import asyncpg
import discord
from discord.ext import commands

from app.core import Cog, Context
from app.core.models import PermissionTemplate, cooldown, describe, group
from app.core.pagination import LinePaginator
from app.utils import cache, get_asset_url, helpers

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable

    from app.database import Database


class CommandName(commands.Converter[str]):
    """A converter that converts the command name to a lowercase string."""

    async def convert(self, ctx: Context, argument: str) -> str:
        lowered = argument.lower()

        valid_commands = {c.qualified_name for c in ctx.bot.walk_commands() if c.cog_name not in ("Config", "Admin")}

        if lowered not in valid_commands:
            raise commands.BadArgument(f"Command {lowered!r} is not valid.")

        return lowered


async def plonk_iterator(ctx: commands.Context, records: list[asyncpg.Record]) -> AsyncIterator[str]:
    """Iterates over a list of records and resolves them to a mention or a name."""
    for record in records:
        entity_id = record[0]
        assert ctx.guild is not None
        resolved = ctx.guild.get_channel(entity_id) or await ctx.bot.get_or_fetch_member(ctx.guild, entity_id)
        if resolved is None:
            yield f"<Not Found: {entity_id}>"
            continue
        yield str(resolved)


class GuildCommandsConfiguration:
    """A class that represents the resolved command permissions for a guild."""

    class _Entry:
        __slots__ = ("allow", "deny")

        def __init__(self) -> None:
            self.allow: set[str] = set()
            self.deny: set[str] = set()

    def __init__(self, guild_id: int, records: list[asyncpg.Record]) -> None:
        self.guild_id: int = guild_id

        self._lookup: defaultdict[int | None, GuildCommandsConfiguration._Entry] = defaultdict(self._Entry)

        for name, channel_id, whitelist in records:
            entry = self._lookup[channel_id]
            if whitelist:
                entry.allow.add(name)
                continue
            entry.deny.add(name)

    @staticmethod
    def _split(obj: str) -> list[str]:
        """Splits a string into a list of strings."""
        return list(accumulate(obj.split(), lambda x, y: f"{x} {y}"))

    def get_blocked_commands(self, channel_id: int) -> set[str]:
        """Returns the blocked commands for a channel.

        Parameters
        ----------
        channel_id: :class:`int`
            The channel ID to get the blocked commands for.

        Returns
        -------
        set[str]
            The blocked commands for the channel.
        """
        if len(self._lookup) == 0:
            return set()

        guild = self._lookup[None]
        channel = self._lookup[channel_id]

        ret = guild.deny - guild.allow

        return ret | (channel.deny - channel.allow)

    def _is_command_blocked(self, name: str, channel_id: int) -> bool | None:
        """Checks if a command is blocked in the entire guild or a channel.

        Parameters
        ----------
        name: :class:`str`
            The name of the command to check.
        channel_id: :class:`int`
            The channel ID to check.

        Returns
        -------
        bool | None
            Whether the command is blocked.
        """
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

    def is_command_blocked(self, name: str, channel_id: int) -> bool | None:
        """Checks if a command is blocked in the entire guild or a channel.

        This implements to first check if the cache is populated or not.

        Parameters
        ----------
        name: :class:`str`
            The name of the command to check.
        channel_id: :class:`int`
            The channel ID to check.

        Returns
        -------
        bool | None
            Whether the command is blocked.
        """
        if len(self._lookup) == 0:
            return False
        return self._is_command_blocked(name, channel_id)

    def is_blocked(self, ctx: Context) -> bool | None:
        """Checks if a command is blocked in the entire guild or a channel from the context."""
        if len(self._lookup) == 0:
            return False

        assert ctx.author is not None
        if isinstance(ctx.author, discord.Member) and ctx.author.guild_permissions.manage_guild:
            return False

        return self._is_command_blocked(ctx.command.qualified_name, ctx.channel.id)


class Config(Cog):
    """Handles the Command Configuration for the bot.
    Enable or disable commands for specific users, channels or guild.
    """

    emoji = "<:green_shield:1322354653991796816>"

    @cache.cache(maxsize=1024, strategy=cache.Strategy.LRU, ignore_kwargs=True)
    async def is_plonked(
        self,
        guild_id: int,
        member_id: int,
        channel: discord.VoiceChannel | discord.TextChannel | discord.Thread | None = None,
        *,
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
        channel: :class:`discord.VoiceChannel` | :class:`discord.TextChannel` | :class:`discord.Thread`
            The channel to check.
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

        entity_ids = [member_id]
        if channel is not None:
            entity_ids.append(channel.id)
            if isinstance(channel, discord.Thread):
                entity_ids.append(channel.parent_id)

        return await self.bot.db.guilds.is_plonked(guild_id, entity_ids)

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

        return not await self.is_plonked(ctx.guild.id, ctx.author.id, channel=ctx.channel, check_bypass=False)

    @cache.cache()
    async def get_commands_configuration(self, guild_id: int) -> GuildCommandsConfiguration:
        """|coro| @cached

        Returns the resolved command permissions for the given guild.

        Parameters
        ----------
        guild_id: :class:`int`
            The guild ID to get the command permissions for.

        Returns
        -------
        :class:`GuildCommandsConfiguration`
            The resolved command permissions for the given guild.
        """
        records = await self.bot.db.guilds.get_command_config(guild_id)
        return GuildCommandsConfiguration(guild_id, records)

    async def bot_check(self, ctx: Context) -> bool:
        if ctx.guild is None:
            return True

        if await ctx.bot.is_owner(ctx.author):
            return True

        resolved = await self.get_commands_configuration(guild_id=ctx.guild.id)
        return not resolved.is_blocked(ctx)

    async def _bulk_ignore_entries(self, ctx: Context, entries: Iterable[discord.abc.Snowflake]) -> None:
        await self.bot.db.guilds.bulk_add_plonks(ctx.guild.id, [e.id for e in entries])
        self.is_plonked.invalidate_containing(str(ctx.guild.id))

    @group("config", alias="conf", description="Configure the bot for your server.", guild_only=True)
    async def config(self, ctx: Context) -> None:
        """Handles the server or channel permission configuration for the bot."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help("config")

    @config.group(
        "ignore",
        aliases=["plonk"],
        description="Ignores text channels or members from using the bot.",
        invoke_without_command=True,
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    @describe(entities="The entities to ignore.")
    async def ignore(self, ctx: Context, *entities: discord.TextChannel | discord.Member | discord.VoiceChannel) -> None:
        """Ignores text channels or members from using the bot.
        If no channel or member is specified, the current channel is ignored.

        Notes
        -----
        Users with Administrator can still use the bot, regardless of ignore status.
        """
        if len(entities) == 0:
            entities = [0]
            await ctx.db.guilds.add_plonk(ctx.guild.id, ctx.channel.id)
            self.is_plonked.invalidate_containing(f"{ctx.guild.id!r}:")
        else:
            await self._bulk_ignore_entries(ctx, entities)

        await ctx.send_success(f"Successfully ingored **{len(entities)}** entities.")

    @ignore.command(
        "list",
        description="Tells you what channels or members are currently ignored in this server.",
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    @cooldown(1, 5, commands.BucketType.guild)
    async def ignore_list(self, ctx: Context) -> None:
        """Tells you what channels or members are currently ignored in this server."""
        records = await ctx.db.guilds.get_plonks(ctx.guild.id)

        if len(records) == 0:
            await ctx.send_error("There are no ignored channels or members in this server.")
            return

        sync_list = [gen async for gen in plonk_iterator(ctx, records)]
        embed = discord.Embed(title="Ignored Entities", timestamp=discord.utils.utcnow(), color=helpers.Colour.white())
        embed.set_thumbnail(url=get_asset_url(ctx.guild))
        await LinePaginator.start(ctx, entries=sync_list, per_page=15, embed=embed, location="description", numerate=True)

    @ignore.command(
        "all",
        description="Ignores every channel in the server from being processed.",
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    async def _all(self, ctx: Context) -> None:
        """Ignores every channel in the server from being processed.
        This works by adding every channel that the server currently has into
        the ignore list. If more channels are added, then they will have to be
        ignored by using the ignore command.
        """
        await self._bulk_ignore_entries(ctx, ctx.guild.text_channels)
        await ctx.send_success("Successfully ignored every channel in the server.")

    @ignore.command(
        "clear",
        description="Clears all the currently set ignores.",
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    async def ignore_clear(self, ctx: Context) -> None:
        """Clears all the currently set ignores."""
        await ctx.db.guilds.clear_plonks(ctx.guild.id)
        self.is_plonked.invalidate_containing(f"{ctx.guild.id!r}:")
        await ctx.send_success("Successfully cleared all the ignores.")

    @config.group(
        "unignore",
        aliases=["unplonk"],
        description="Allows channels or members to use the bot again.",
        invoke_without_command=True,
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    @describe(entities="The entities to unignore.")
    async def unignore(self, ctx: Context, *entities: discord.TextChannel | discord.Member | discord.VoiceChannel) -> None:
        """Allows channels or members to use the bot again.
        If nothing is specified, it unignores the current channel.
        """
        if len(entities) == 0:
            await ctx.db.guilds.remove_plonks(ctx.guild.id, [ctx.channel.id])
        else:
            await ctx.db.guilds.remove_plonks(ctx.guild.id, [c.id for c in entities])

        self.is_plonked.invalidate_containing(f"{ctx.guild.id!r}:")
        await ctx.send_success(f"Successfully unignored **{len(entities)}** entities.")

    @unignore.command(
        "all", description="Unignores every channel in the server.", guild_only=True, user_permissions=PermissionTemplate.mod
    )
    async def unignore_all(self, ctx: Context) -> None:
        """An alias for ignore clear command."""
        await ctx.invoke(self.ignore_clear)  # type: ignore

    async def command_toggle(
        self,
        db: Database,
        guild_id: int,
        channel_id: int | None,
        name: str,
        *,
        whitelist: bool = True,
    ) -> None:
        """Toggles a command on or off for a specific channel.

        Parameters
        ----------
        db: :class:`Database`
            The database to use.
        guild_id: :class:`int`
            The guild ID to toggle the command for.
        channel_id: :class:`int`
            The channel ID to toggle the command for.
        name: :class:`str`
            The name of the command to toggle.
        whitelist: :class:`bool`
            Whether to whitelist the command or not.

        Raises
        ------
        :exc:`commands.BadArgument`
            The command is already disabled.
        """
        self.get_commands_configuration.invalidate(guild_id)

        try:
            await db.guilds.set_command_config(guild_id, channel_id, name, whitelist=whitelist)
        except asyncpg.UniqueViolationError:
            raise commands.BadArgument(
                "This command is already disabled." if not whitelist else "This command is already explicitly enabled."
            )

    @config.group(
        "channel",
        description="Toggles a command on or off for a specific channel.",
        invoke_without_command=True,
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    async def channel(self, ctx: Context) -> None:
        """Handles the channel-specific permissions."""
        pass

    @channel.command(
        "disable",
        description="Disables a command for this channel.",
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    @describe(command="The command to disable.")
    async def channel_disable(self, ctx: Context, *, command: Annotated[str, CommandName]) -> None:
        """Disables a command for this channel."""
        try:
            await self.command_toggle(ctx.db, ctx.guild.id, ctx.channel.id, command, whitelist=False)
        except commands.BadArgument as e:
            await ctx.send_error(str(e))
        else:
            await ctx.send_success("Command successfully disabled for this channel.")

    @channel.command(
        "enable", description="Enables a command for this channel.", guild_only=True, user_permissions=PermissionTemplate.mod
    )
    @describe(command="The command to enable.")
    async def channel_enable(self, ctx: Context, *, command: Annotated[str, CommandName]) -> None:
        """Enables a command for this channel."""
        try:
            await self.command_toggle(ctx.db, ctx.guild.id, ctx.channel.id, command, whitelist=True)
        except commands.BadArgument as e:
            await ctx.send_error(str(e))
        else:
            await ctx.send_success("Command successfully enabled for this channel.")

    @config.group(
        "server",
        description="Toggles a command on or off.",
        invoke_without_command=True,
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    async def server(self, ctx: Context) -> None:
        """Handles the server-specific permissions."""
        pass

    @server.command(
        "disable",
        description="Disables a command for this server.",
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    @describe(command="The command to disable.")
    async def server_disable(self, ctx: Context, *, command: Annotated[str, CommandName]) -> None:
        """Disables a command for this server."""
        try:
            await self.command_toggle(ctx.db, ctx.guild.id, None, command, whitelist=False)
        except commands.BadArgument as e:
            await ctx.send_error(str(e))
        else:
            await ctx.send_success("Command successfully disabled for this server.")

    @server.command(
        "enable", description="Enables a command for this server.", guild_only=True, user_permissions=PermissionTemplate.mod
    )
    @describe(command="The command to enable.")
    async def server_enable(self, ctx: Context, *, command: Annotated[str, CommandName]) -> None:
        """Enables a command for this server."""
        try:
            await self.command_toggle(ctx.db, ctx.guild.id, None, command, whitelist=True)
        except commands.BadArgument as e:
            await ctx.send_error(str(e))
        else:
            await ctx.send_success("Command successfully enabled for this server.")

    @config.command(
        "enable",
        description="Enables a command for the server or a channel.",
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    @describe(channel="The channel to enable the command for.", command="The command to enable.")
    async def config_enable(
        self, ctx: Context, channel: discord.TextChannel | None, *, command: Annotated[str, CommandName]
    ) -> None:
        """Enables a command the server or a channel."""
        channel_id = channel.id if channel else None
        human_friendly = channel.mention if channel else "the server"
        try:
            await self.command_toggle(ctx.db, ctx.guild.id, channel_id, command, whitelist=True)
        except commands.BadArgument as e:
            await ctx.send_error(str(e))
        else:
            await ctx.send_success(f"Command successfully enabled for {human_friendly}.")

    @config.command(
        "disable",
        description="Disables a command for the server or a channel.",
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    @describe(channel="The channel to disable the command for.", command="The command to disable.")
    async def config_disable(
        self, ctx: Context, channel: discord.TextChannel | None, *, command: Annotated[str, CommandName]
    ) -> None:
        """Disables a command for the server or a channel."""
        channel_id = channel.id if channel else None
        human_friendly = channel.mention if channel else "the server"
        try:
            await self.command_toggle(ctx.db, ctx.guild.id, channel_id, command, whitelist=False)
        except commands.BadArgument as e:
            await ctx.send_error(str(e))
        else:
            await ctx.send_success(f"Command successfully disabled for {human_friendly}.")

    @config.command(
        "disabled",
        description="Shows the disabled commands for the channel given.",
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    @describe(channel="The channel to show the disabled commands for.")
    async def config_disabled(
        self, ctx: Context, *, channel: discord.TextChannel | discord.VoiceChannel | None = None
    ) -> None:
        """Shows the disabled commands for the channel given."""
        channel_id: int
        if channel is None:
            channel_id = ctx.channel.parent_id if isinstance(ctx.channel, discord.Thread) else ctx.channel.id
        else:
            channel_id = channel.id

        resolved = await self.get_commands_configuration(guild_id=ctx.guild.id)
        disabled = list(resolved.get_blocked_commands(channel_id))

        if not disabled:
            await ctx.send_error("There are no disabled commands for this channel.")
            return

        embed = discord.Embed(title="Disabled Commands", timestamp=discord.utils.utcnow(), color=helpers.Colour.white())
        guild = ctx.guild
        assert guild is not None
        embed.set_thumbnail(url=get_asset_url(guild))
        await LinePaginator.start(ctx, entries=disabled, per_page=15, embed=embed, location="description", numerate=True)

    @config.group("global", description="Handles global bot configuration.", hidden=True)
    @commands.is_owner()
    async def _global(self, ctx: Context) -> None:
        """Handles global bot configuration."""
        pass

    @_global.command("block", description="Blocks a user or guild globally.", hidden=True)
    @describe(object_id="The user or guild ID to block.")
    @commands.is_owner()
    async def global_block(self, ctx: Context, object_id: discord.abc.Snowflake) -> None:
        """Blocks a user or guild globally."""
        await self.bot.add_to_blacklist(object_id)
        await ctx.send_success('User or guild blocked globally.')

    @_global.command(
        'unblock',
        description='Unblocks a user or guild globally.',
        hidden=True
    )
    @describe(object_id='The user or guild ID to unblock.')
    @commands.is_owner()
    async def global_unblock(self, ctx: Context, object_id: discord.abc.Snowflake) -> None:
        """Unblocks a user or guild globally."""
        await self.bot.remove_from_blacklist(object_id)
        await ctx.send_success('User or guild unblocked globally.')


async def setup(bot) -> None:
    await bot.add_cog(Config(bot))
