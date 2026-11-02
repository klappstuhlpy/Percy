from __future__ import annotations

import re
from collections import defaultdict
from itertools import accumulate
from typing import TYPE_CHECKING, Annotated

import asyncpg
import discord
from discord.ext import commands

from app.core import Cog, Context
from app.core.errors import CommandDisabled
from app.core.models import PermissionSpec, PermissionTemplate, cooldown, describe, group
from app.core.pagination import LinePaginator
from app.core.permissions import check_rules, custom_command_name, normalize_rule_subject
from app.services.command_rules import Decision
from app.services.moderation_settings import DISABLED_MESSAGE_VARIABLES, check_disabled_message, render_disabled_message
from app.utils import cache, get_asset_url, helpers

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable

    from app.core.permissions import CommandOverride
    from app.database import Database


#: Tokens that clear a command's custom permission requirement (fall back to its default gate).
_CLEAR_TOKENS = frozenset({"none", "default", "clear", "reset", "off"})


def parse_permission_names(raw: str) -> tuple[int | None, list[str]]:
    """Parse a space/comma-separated list of permission names into a bitmask.

    Returns ``(value, invalid)`` where ``value`` is the combined :class:`discord.Permissions`
    value (or ``None`` to clear the requirement — an empty list or a lone clear-token like
    ``"default"``) and ``invalid`` lists any unrecognised names.
    """
    tokens = [t for t in re.split(r"[,\s]+", raw.strip()) if t]
    if not tokens or (len(tokens) == 1 and tokens[0].lower() in _CLEAR_TOKENS):
        return None, []

    kwargs: dict[str, bool] = {}
    invalid: list[str] = []
    for token in tokens:
        key = token.lower().replace(" ", "_").replace("-", "_")
        if key in discord.Permissions.VALID_FLAGS:
            kwargs[key] = True
        else:
            invalid.append(token)

    if invalid:
        return None, invalid
    return (discord.Permissions(**kwargs).value if kwargs else None), []


class CommandName(commands.Converter[str]):
    """A converter that converts the command name to a lowercase string."""

    async def convert(self, ctx: Context, argument: str) -> str:
        lowered = argument.lower()

        valid_commands = {c.qualified_name for c in ctx.bot.walk_commands() if c.cog_name not in ("Config", "Dev")}

        if lowered not in valid_commands:
            raise commands.BadArgument(f"Command {lowered!r} is not valid.")

        return lowered


class CommandOrCustomName(CommandName):
    """A command name, or this server's custom command (by name or alias; returns its name)."""

    async def convert(self, ctx: Context, argument: str) -> str:
        try:
            return await super().convert(ctx, argument)
        except commands.BadArgument:
            name = await custom_command_name(ctx.bot.db, ctx.guild, argument)
            if name is None:
                raise
            return name


class RuleTarget(commands.Converter["tuple[str, int]"]):
    """A member, role, channel or category (mention/id/name), or the literal ``server``, as ``(target_type, id)``."""

    async def convert(self, ctx: Context, argument: str) -> tuple[str, int]:
        if argument.lower() in ("server", "guild", "everyone"):
            return "guild", ctx.guild.id
        for converter, kind in (
            (commands.MemberConverter, "user"),
            (commands.RoleConverter, "role"),
            (commands.GuildChannelConverter, "channel"),
        ):
            try:
                obj = await converter().convert(ctx, argument)
            except commands.BadArgument:
                continue
            return ("category" if isinstance(obj, discord.CategoryChannel) else kind), obj.id
        raise commands.BadArgument(f"{argument!r} is not a member, role, channel, category or `server`.")


def describe_target(target_type: str, target_id: int) -> str:
    return {
        "user": f"<@{target_id}>",
        "role": f"<@&{target_id}>",
        "channel": f"<#{target_id}>",
        "category": f"<#{target_id}> (category)",
        "guild": "the whole server",
    }[target_type]


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

        decision = await check_rules(
            self.bot.db, ctx.guild, ctx.author, ctx.channel, ctx.command.qualified_name, ctx.command.cog_name
        ) if isinstance(ctx.author, discord.Member) else Decision(None)
        if decision.allowed is False:
            return ctx.author.id == ctx.guild.owner_id  # silent deny; the guild owner bypasses
        if decision.allowed:
            return True  # an explicit allow beats a channel/server disable

        resolved = await self.get_commands_configuration(guild_id=ctx.guild.id)
        if not resolved.is_blocked(ctx):
            return True
        # Blocked: silent unless the server set a reply for it.
        message = (await self.bot.db.get_guild_config(ctx.guild.id)).disabled_command_message
        if message:
            raise CommandDisabled(render_disabled_message(message, ctx.command.qualified_name, ctx.channel.mention))
        return False

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

        await ctx.send_success(f"Successfully ignored **{len(entities)}** entities.")

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
        try:
            await db.guilds.set_command_config(guild_id, channel_id, name, whitelist=whitelist)
        except asyncpg.UniqueViolationError:
            raise commands.BadArgument(
                "This command is already disabled." if not whitelist else "This command is already explicitly enabled."
            )
        finally:
            # After the write: invalidating first lets a concurrent bot_check re-cache the old rows.
            self.get_commands_configuration.invalidate(guild_id)

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
    async def channel_disable(self, ctx: Context, *, command: Annotated[str, CommandOrCustomName]) -> None:
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
    async def channel_enable(self, ctx: Context, *, command: Annotated[str, CommandOrCustomName]) -> None:
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
    async def server_disable(self, ctx: Context, *, command: Annotated[str, CommandOrCustomName]) -> None:
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
    async def server_enable(self, ctx: Context, *, command: Annotated[str, CommandOrCustomName]) -> None:
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
        self, ctx: Context, channel: discord.TextChannel | None, *, command: Annotated[str, CommandOrCustomName]
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
        self, ctx: Context, channel: discord.TextChannel | None, *, command: Annotated[str, CommandOrCustomName]
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

    @config.command(
        "disabledmessage",
        aliases=["disabledmsg"],
        description="Sets the reply members get when they use a command disabled here; blank turns it off.",
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    @describe(text="The reply. {command} and {channel} are filled in. Leave blank to stay silent.")
    async def config_disabledmessage(self, ctx: Context, *, text: str | None = None) -> None:
        """Sets what Percy answers (for 10 seconds) when someone runs a command that `config` disabled.
        Placeholders: `{command}`, `{channel}`. Without text the reply is removed and Percy stays silent.
        """
        assert ctx.guild is not None
        try:
            message = check_disabled_message(text)
        except ValueError as e:
            await ctx.send_error(str(e))
            return
        config = await self.bot.db.get_guild_config(ctx.guild.id)
        await config.update(disabled_command_message=message)
        if message is None:
            await ctx.send_success("Disabled commands now fail silently.")
            return
        names = ", ".join(f"`{{{name}}}`" for name in DISABLED_MESSAGE_VARIABLES)
        await ctx.send_success(f"Members who use a disabled command now get that reply. Placeholders: {names}.")

    # -- command permission overrides ------------------------------------

    async def _get_override(self, guild_id: int, command: str) -> CommandOverride | None:
        """Return the current :class:`CommandOverride` for a command, or ``None``."""
        overrides = await self.bot.db.get_command_overrides(guild_id)
        return overrides.get(command)

    @staticmethod
    def _describe_override(command: str, permissions: int | None, allowed_roles: Iterable[int]) -> str:
        """Human-readable one-liner for an override, for the list/confirmation messages."""
        parts: list[str] = []
        if permissions is not None:
            names = [PermissionSpec.permission_as_str(p) for p, on in discord.Permissions(permissions) if on]
            parts.append("requires " + (", ".join(names) if names else "no permissions"))
        roles = list(allowed_roles)
        if roles:
            parts.append("allowed roles: " + ", ".join(f"<@&{r}>" for r in roles))
        return f"**{command}** — {'; '.join(parts) if parts else 'default permissions'}"

    @config.group(
        "perms",
        aliases=["permissions"],
        description="Customize which permissions a command requires in this server.",
        invoke_without_command=True,
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    async def perms(self, ctx: Context) -> None:
        """Show every command permission override configured for this server.

        Overrides change *who may run a command* — for both prefix and slash invocations.
        The bot's own required permissions are always still enforced, and owner-only commands
        cannot be overridden.
        """
        overrides = await ctx.db.get_command_overrides(ctx.guild.id)
        if not overrides:
            await ctx.send_info(
                "No command permission overrides are set. Add one with "
                f"`{ctx.clean_prefix}config perms set <command> <permissions>`."
            )
            return

        entries = [
            self._describe_override(name, ov.permissions, ov.allowed_roles)
            for name, ov in sorted(overrides.items())
        ]
        embed = discord.Embed(
            title="Command Permission Overrides", timestamp=discord.utils.utcnow(), color=helpers.Colour.white()
        )
        embed.set_thumbnail(url=get_asset_url(ctx.guild))
        await LinePaginator.start(ctx, entries=entries, per_page=15, embed=embed, location="description", numerate=True)

    @perms.command(
        "set",
        description="Set the permission(s) a command requires (or 'default' to clear).",
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(
        command="The command to override.",
        permissions="Space/comma separated permission names (e.g. `manage_messages`), or `default` to clear.",
    )
    async def perms_set(
        self, ctx: Context, command: Annotated[str, CommandName], *, permissions: str
    ) -> None:
        """Replace the permission(s) required to run a command in this server."""
        value, invalid = parse_permission_names(permissions)
        if invalid:
            await ctx.send_error(
                f"Unknown permission(s): {', '.join(f'`{name}`' for name in invalid)}. "
                "Use Discord permission names like `manage_messages` or `ban_members`."
            )
            return

        existing = await self._get_override(ctx.guild.id, command)
        roles = list(existing.allowed_roles) if existing else []

        if value is None and not roles:
            # Nothing left to store — drop the override so the command uses its own default.
            await ctx.db.guilds.delete_command_permission_override(ctx.guild.id, command)
            await ctx.send_success(f"**{command}** now uses its default permissions.")
            return

        await ctx.db.guilds.upsert_command_permission_override(ctx.guild.id, command, value, roles)
        await ctx.send_success("Updated: " + self._describe_override(command, value, roles))

    @perms.command(
        "allow",
        description="Let a role always use a command, regardless of permissions.",
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(command="The command to override.", role="The role to always allow.")
    async def perms_allow(self, ctx: Context, command: Annotated[str, CommandName], role: discord.Role) -> None:
        """Add a role to a command's allow-list (they bypass the permission requirement)."""
        existing = await self._get_override(ctx.guild.id, command)
        roles = set(existing.allowed_roles) if existing else set()
        permissions = existing.permissions if existing else None

        if role.id in roles:
            await ctx.send_error(f"{role.mention} is already allowed to use **{command}**.")
            return

        roles.add(role.id)
        await ctx.db.guilds.upsert_command_permission_override(ctx.guild.id, command, permissions, list(roles))
        await ctx.send_success(f"{role.mention} may now use **{command}** regardless of permissions.")

    @perms.command(
        "unallow",
        aliases=["disallow", "deny"],
        description="Remove a role from a command's allow-list.",
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(command="The command to override.", role="The role to remove from the allow-list.")
    async def perms_unallow(self, ctx: Context, command: Annotated[str, CommandName], role: discord.Role) -> None:
        """Remove a role from a command's allow-list."""
        existing = await self._get_override(ctx.guild.id, command)
        if existing is None or role.id not in existing.allowed_roles:
            await ctx.send_error(f"{role.mention} is not on the allow-list for **{command}**.")
            return

        roles = set(existing.allowed_roles)
        roles.discard(role.id)

        if not roles and existing.permissions is None:
            await ctx.db.guilds.delete_command_permission_override(ctx.guild.id, command)
        else:
            await ctx.db.guilds.upsert_command_permission_override(
                ctx.guild.id, command, existing.permissions, list(roles)
            )
        await ctx.send_success(f"{role.mention} can no longer bypass permissions for **{command}**.")

    @perms.command(
        "reset",
        aliases=["clear", "remove"],
        description="Remove a command's permission override entirely.",
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(command="The command whose override to remove.")
    async def perms_reset(self, ctx: Context, *, command: Annotated[str, CommandName]) -> None:
        """Delete a command's override so it reverts to its built-in permission gate."""
        status = await ctx.db.guilds.delete_command_permission_override(ctx.guild.id, command)
        if status.endswith("0"):
            await ctx.send_error(f"**{command}** has no permission override.")
            return
        await ctx.send_success(f"Removed the permission override for **{command}**; it now uses its default gate.")

    # -- command rules ---------------------------------------------------

    async def _set_rule(self, ctx: Context, subject: str, target: tuple[str, int], allow: bool) -> None:
        canonical = normalize_rule_subject(self.bot, subject) or await custom_command_name(self.bot.db, ctx.guild, subject)
        if canonical is None:
            await ctx.send_error(
                f"`{subject}` is not a command, `cog:<Name>` or `*` (Config commands cannot be restricted)."
            )
            return
        await ctx.db.command_rules.set_rule(ctx.guild.id, canonical, target[0], target[1], allow)
        verb = "allowed" if allow else "denied"
        await ctx.send_success(f"`{canonical}` is now **{verb}** for {describe_target(*target)}.")

    @config.group(
        "rule",
        description="Allow or deny a command, cog or `*` for a member, role, channel, category or the server.",
        invoke_without_command=True,
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    async def rule(self, ctx: Context) -> None:
        """Rules beat the enable/disable and permission checks: an allow overrides a disable and the default
        permission gate (never the bot's own permissions), a deny blocks silently. The most specific subject
        wins (command > parent group > `cog:<Name>` > `*`), then user > role > channel > category > server.
        """
        await ctx.send_help("config rule")

    @rule.command(
        "allow", description="Allow a command for a target.", guild_only=True, user_permissions=PermissionTemplate.manager
    )
    @describe(subject="A command, `cog:<Name>` or `*`.", target="A member, role, channel, category or `server`.")
    async def rule_allow(self, ctx: Context, subject: str, target: Annotated[tuple[str, int], RuleTarget]) -> None:
        """Allow `subject` for `target`; quote a multi-word command (`"config perms set"`)."""
        await self._set_rule(ctx, subject, target, True)

    @rule.command(
        "deny", description="Deny a command for a target.", guild_only=True, user_permissions=PermissionTemplate.manager
    )
    @describe(subject="A command, `cog:<Name>` or `*`.", target="A member, role, channel, category or `server`.")
    async def rule_deny(self, ctx: Context, subject: str, target: Annotated[tuple[str, int], RuleTarget]) -> None:
        """Deny `subject` for `target`; quote a multi-word command (`"config perms set"`)."""
        await self._set_rule(ctx, subject, target, False)

    @rule.command(
        "remove",
        aliases=["reset", "clear"],
        description="Remove a rule.",
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(subject="The rule's subject.", target="The rule's target.")
    async def rule_remove(self, ctx: Context, subject: str, target: Annotated[tuple[str, int], RuleTarget]) -> None:
        """Remove the rule for `subject` and `target`."""
        canonical = normalize_rule_subject(self.bot, subject) or subject
        if not await ctx.db.command_rules.remove_rule(ctx.guild.id, canonical, target[0], target[1]):
            await ctx.send_error("There is no such rule.")
            return
        await ctx.send_success(f"Removed the rule for `{canonical}` on {describe_target(*target)}.")

    @config.command(
        "rules",
        description="Lists the command rules, optionally for one subject.",
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(subject="Only show rules for this command, `cog:<Name>` or `*`.")
    async def rules(self, ctx: Context, *, subject: str | None = None) -> None:
        """Lists every command rule of this server."""
        rules = await ctx.db.get_command_rules(ctx.guild.id)
        if subject:
            wanted = normalize_rule_subject(self.bot, subject) or subject
            rules = tuple(r for r in rules if r.subject == wanted)
        if not rules:
            await ctx.send_info("No command rules are set.")
            return
        entries = [
            f"{'allow' if r.allow else 'deny'} `{r.subject}` - {describe_target(r.target_type, r.target_id)}"
            for r in rules
        ]
        embed = discord.Embed(title="Command Rules", timestamp=discord.utils.utcnow(), color=helpers.Colour.white())
        embed.set_thumbnail(url=get_asset_url(ctx.guild))
        await LinePaginator.start(ctx, entries=entries, per_page=15, embed=embed, location="description", numerate=True)

    @config.command(
        "explain",
        description="Shows why a member can or cannot run a command, layer by layer.",
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(member="The member.", command="The command.", channel="The channel (default: this one).")
    async def explain(
        self,
        ctx: Context,
        member: discord.Member,
        command: Annotated[str, CommandOrCustomName],
        channel: discord.TextChannel | discord.VoiceChannel | None = None,
    ) -> None:
        """Walks the ignore list, disable list, permission override and rules for `member` running `command`."""
        channel = channel or ctx.channel
        cmd = self.bot.get_command(command)
        name, cog_name = (cmd.qualified_name, cmd.cog_name) if cmd else (command, None)
        guild = ctx.guild
        lines: list[str] = []
        verdict: str | None = None

        if await self.bot.is_owner(member):
            lines.append("**Bot owner:** bypasses every check.")
            verdict = "allowed (bot owner)"

        plonked = await self.is_plonked(guild.id, member.id, channel=channel, check_bypass=True)
        lines.append(f"**Ignore list:** {'ignored here' if plonked else 'not ignored'}.")
        if plonked and verdict is None:
            verdict = "blocked by the ignore list (rules cannot override it)"

        decision = await check_rules(self.bot.db, guild, member, channel, name, cog_name)
        lines.append(f"**Rules:** {decision.reason}")
        if decision.allowed is False and member.id == guild.owner_id:
            lines.append("**Server owner:** bypasses denies.")
        elif decision.allowed is False and verdict is None:
            verdict = "blocked by a deny rule"

        resolved = await self.get_commands_configuration(guild_id=guild.id)
        blocked = resolved.is_command_blocked(name, channel.id)
        bypass = member.guild_permissions.manage_guild
        state = ("disabled here" + (" (Manage Server bypasses)" if bypass else "")) if blocked else "enabled"
        lines.append(f"**Enable/disable:** {state}{' - overridden by an allow rule' if blocked and decision.allowed else ''}.")
        if blocked and not bypass and not decision.allowed and verdict is None:
            verdict = "blocked because the command is disabled here"

        override = (await self.bot.db.get_command_overrides(guild.id)).get(name)
        if cmd is None:
            lines.append("**Custom command:** its own permission/role gate applies (no permission override).")
        elif override is None:
            lines.append("**Permission override:** none (default gate).")
        else:
            lines.append(
                "**Permission override:** "
                + self._describe_override(name, override.permissions, override.allowed_roles)
            )
        gate = (
            "bypassed by an allow rule (the bot's own permissions still apply)."
            if decision.allowed
            else "the command's own (or overridden) permission requirement applies."
        )
        lines.append(f"**Permission gate:** {gate}")

        embed = discord.Embed(
            title=f"{member.display_name} -> {name} in #{channel.name}",
            description="\n".join(lines),
            color=helpers.Colour.white(),
        )
        embed.add_field(name="Result", value=verdict.capitalize() if verdict else "Allowed by every layer above.")
        await ctx.send(embed=embed)

    @config.group("global", description="Handles global bot configuration.", hidden=True)
    @commands.is_owner()
    async def _global(self, ctx: Context) -> None:
        """Handles global bot configuration."""
        pass

    @_global.command("show", description="Shows all globally blocked entities.", hidden=True)
    @commands.is_owner()
    async def global_show(self, ctx: Context) -> None:
        """Shows all globally blocked users and guilds."""
        blacklist = self.bot.blacklist
        if not blacklist.all():
            await ctx.send_info("No globally blocked entities.")
            return

        entries: list[str] = []
        for raw_id in blacklist:
            raw_str = str(raw_id)
            match = re.search(r'(\d+)', raw_str)
            entity_id = int(match.group(1)) if match else int(raw_str)
            user = self.bot.get_user(entity_id)
            guild = self.bot.get_guild(entity_id)
            if user:
                entries.append(f"`{entity_id}` — {user} (user)")
            elif guild:
                entries.append(f"`{entity_id}` — {guild.name} (guild)")
            else:
                entries.append(f"`{entity_id}` — *unknown*")

        embed = discord.Embed(title="Global Blacklist", color=helpers.Colour.white())
        await LinePaginator.start(ctx, entries=entries, per_page=15, embed=embed, location="description", numerate=True)

    @_global.command("block", description="Blocks a user or guild globally.", hidden=True)
    @describe(object_id="The user or guild ID to block.")
    @commands.is_owner()
    async def global_block(self, ctx: Context, object_id: int) -> None:
        """Blocks a user or guild globally."""
        await self.bot.add_to_blacklist(discord.Object(id=object_id))
        await ctx.send_success('User or guild blocked globally.')

    @_global.command(
        'unblock',
        description='Unblocks a user or guild globally.',
        hidden=True
    )
    @describe(object_id='The user or guild ID to unblock.')
    @commands.is_owner()
    async def global_unblock(self, ctx: Context, object_id: int) -> None:
        """Unblocks a user or guild globally."""
        await self.bot.remove_from_blacklist(discord.Object(id=object_id))
        await ctx.send_success('User or guild unblocked globally.')


async def setup(bot) -> None:
    await bot.add_cog(Config(bot))
