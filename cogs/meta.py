from __future__ import annotations

import datetime
import inspect
import io
import itertools
import os
import re
import time
import traceback
from collections import Counter
from dataclasses import dataclass
from typing import (
    Optional, Union, TYPE_CHECKING, Mapping, List, Annotated, Dict,
    Type, Iterable, Callable, Literal, Any
)

import discord
import psutil
import unicodedata
from dateutil.relativedelta import relativedelta
from discord import app_commands, Interaction
from lru import LRU

from .utils import fuzzy, helpers, commands
from .utils.converters import Prefix, get_asset_url
from .utils.formats import plural, format_date, truncate
from .utils.paginator import BasePaginator, TextSource, LinePaginator
from .utils.constants import (
    PH_HELP_FORUM, PH_SOLVED_TAG, PartialCommand,
    PartialCommandGroup, App, PH_GUILD_ID
)
from .utils.timetools import mean_stddev, RelativeDelta

if TYPE_CHECKING:
    from bot import Percy
    from .utils.context import GuildContext, Context, tick

COMMAND_ICON_URL = 'https://images.klappstuhl.me/gallery/rWgaVHMMpl.png'
INFO_ICON_URL = 'https://images.klappstuhl.me/gallery/zxfezkjkSp.png'

INLINE_DOCSTRING = re.compile(r'\n(#+)')


def cleanup_docstring(s1: Optional[str], s2: Optional[str]) -> str:
    if not s1 and not s2:
        return '*Command undocumented.*'

    if s1:
        s1 = INLINE_DOCSTRING.sub(r'\1', s1)
    if s2:
        s2 = INLINE_DOCSTRING.sub(r'\1', s2)

    if s1 == s2:
        return inspect.cleandoc(s1)
    if s1 and s2:
        # Check if there are duplicate lines and remove them
        s1 = s1.split('\n')
        s2 = s2.split('\n')
        s1 = '\n'.join([line for line in s1 if line not in s2])
        s2 = '\n'.join([line for line in s2 if line not in s1])
        return inspect.cleandoc(f'{s1}\n\n{s2}')
    if s1 or s2:
        return inspect.cleandoc(s1 or s2)


def cooldown_key(s: str) -> str:
    try:
        KEYWORD_REGEX = re.compile(r'(guild|user|member|author)')
        matches = KEYWORD_REGEX.findall(s.lower())
        return ', '.join(t.title() for t in sorted(matches)) or 'N/A'
    except:  # noqa
        return 'N/A'


def can_close_threads(ctx: GuildContext) -> bool:
    if not isinstance(ctx.channel, discord.Thread):
        return False

    permissions = ctx.channel.permissions_for(ctx.author)
    return ctx.channel.parent_id == PH_HELP_FORUM and (
            permissions.manage_threads or ctx.channel.owner_id == ctx.author.id
    )


def is_help_thread():
    def predicate(ctx: GuildContext) -> bool:
        return isinstance(ctx.channel, discord.Thread) and ctx.channel.parent_id == PH_HELP_FORUM

    return commands.check(predicate)


class UnsolvedFlags(commands.FlagConverter, delimiter=' ', prefix='--'):
    messages: int = commands.flag(
        default=5,
        description='The maximum number of messages the thread needs to be considered active. Defaults to 5.')
    threshold: relativedelta = commands.flag(
        default=relativedelta(minutes=5),
        description='How old the thread needs to be (e.g. "10m" or "22m"). Defaults to 5 minutes.',
        converter=RelativeDelta())


class HelpPaginator(BasePaginator[PartialCommand]):
    async def format_page(self, entries: List[PartialCommand]) -> discord.Embed:
        _temp = PaginatedHelpCommand.temporary(self.ctx)

        if self.current_page == 1 and isinstance(self.entries, dict):
            return await _temp.get_front_page_embed()

        if not (group := self.extras.get('group')):
            raise commands.CommandError('The group attribute is missing.')

        emoji = getattr(group, 'display_emoji', None) or ''
        embed = discord.Embed(
            title=f'{emoji} {group.qualified_name}',
            description=group.description,
            colour=helpers.Colour.coral()
        )

        for cmd in entries:
            prefix = f'{_temp.locked_emoji} | ' if getattr(cmd, 'is_locked', False) else ''
            signature = _temp.get_command_signature(cmd, shortened_signature=True, with_prefix=False)
            embed.add_field(name=f'{prefix}**`{signature}`**', value=cmd.description or '…', inline=False)

        if any(getattr(cmd, 'is_locked', False) is True for cmd in entries):
            embed.add_field(
                name='\u200b',
                value=f'{_temp.locked_emoji} » This command expects certain permissions from the user to be run.',
                inline=False
            )

        embed.set_author(name=f'{plural(len(self.entries)):command}', icon_url=COMMAND_ICON_URL)

        embed.set_footer(
            text=f'{self.ctx.user} | Use the components below for navigation. '
                 f'This menu shows only the available commands and categories.')
        return embed

    @classmethod
    async def start(
            cls: Type[HelpPaginator],
            context: Context | discord.Interaction,
            /,
            *,
            entries: List[PartialCommand] | Dict[commands.Cog, list[PartialCommand]],
            per_page: int = 6,
            clamp_pages: bool = True,
            timeout: int = 180,
            search_for: bool = False,
            ephemeral: bool = False,
            **kwargs: Any,
    ) -> HelpPaginator[PartialCommand]:
        """Overwritten to add the view to the message and edit message, not send new."""
        self = cls(entries=entries, per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)
        self.ctx = context

        if kwargs.get('extras', None):
            self.extras.update(kwargs.pop('extras'))
        self.extras.update(kwargs)

        if len(self.pages) == 1:
            self.clear_items()

        def prepare_select(items: Union[dict[commands.Cog, list[PartialCommand]], list[PartialCommand]]):
            return CategorySelect(context.client, entries=items, with_index=self.extras.get('with_index', True))

        if isinstance(entries, dict):
            self.extras['groups'] = entries
            self.add_item(prepare_select(entries))
        elif isinstance(entries, list):
            if (groups := self.extras.get('groups')) is not None:
                self.add_item(prepare_select(groups))
        else:
            raise commands.CommandError('The entries attribute is missing.')

        page: discord.Embed = await self.format_page(self.pages[0])
        self.update_buttons()

        func = self._edit if kwargs.pop('edit', False) else self._send
        view = None if (self.total_pages <= 1 and not self.current_page == 1) and func == self._send else self
        await func(ctx=context, embed=page, view=view, ephemeral=ephemeral)
        return self


class CategorySelect(discord.ui.Select):
    """A select menu for the HelpPaginator to navigate through categories."""

    def __init__(self, bot: Percy, entries: dict[commands.Cog, list[commands.Command]], with_index: bool = True):
        super().__init__(placeholder='Select a category to view...')
        self.bot: Percy = bot
        self.entries: dict[commands.Cog, list[commands.Command] | list[app_commands.AppCommand]] = entries
        self.with_index: bool = with_index

        self.__fill_options()

    def __fill_options(self) -> None:
        if self.with_index:
            self.add_option(
                label='Start Page',
                emoji=discord.PartialEmoji(name='vegaleftarrow', id=1066024601332748389),
                value='__index',
                description='The front page of the Help Menu.',
            )

        for cog, cmds in filter(lambda x: x[1], self.entries.items()):
            description = cog.description.split('\n', 1)[0] or None
            emoji = getattr(cog, 'display_emoji', None)
            self.add_option(label=cog.qualified_name, value=cog.qualified_name, description=description, emoji=emoji)

    async def callback(self, interaction: discord.Interaction):
        assert self.view is not None
        await interaction.response.defer()

        value = self.values[0]
        if value == '__index':
            await HelpPaginator.start(interaction, entries=self.entries, edit=True, extras=self.view.extras)
        else:
            cog = self.bot.get_cog(value)
            if cog is None:
                return await interaction.response.send_message('Somehow this category does not exist?', ephemeral=True)

            cmds = self.entries[cog]
            if not cmds:
                return await interaction.response.send_message('This category has no commands for you', ephemeral=True)

            await HelpPaginator.start(interaction, entries=cmds, edit=True, group=cog, extras=self.view.extras)


class PaginatedHelpCommand(commands.HelpCommand):
    """A subclass of the default help command that implements support for Application/Hybrid Commands."""

    context: Context

    def __init__(self):
        super().__init__(
            show_hidden=False,
            verify_checks=False,
            command_attrs={
                'cooldown': commands.CooldownMapping.from_cooldown(1, 3.0, commands.BucketType.member),
                'hidden': True,
                'aliases': ['h'],
                'usage': '[command|category]',
                'description': 'Get help for a module or a command.'
            }
        )

    @property
    def locked_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='locked', id=1208405196334567474)

    def get_commands(self) -> set[PartialCommand]:
        """Returns all commands of the bot."""
        return set(self.context.client.commands) | set(self.context.client.tree.get_commands())

    @staticmethod
    def get_cog_commands(cog: commands.Cog) -> set[PartialCommand]:
        """Returns all commands of a cog."""
        return set(cog.get_commands()) | set(cog.get_app_commands())

    async def total_commands_invoked(self) -> int:
        """Returns the total amount of commands invoked."""
        query = "SELECT COUNT(*) as total FROM commands;"
        return await self.context.client.pool.fetchval(query)  # type: ignore

    async def command_callback(self, ctx: Context, /, *, command: Optional[str] = None):
        """|coro|

        The actual implementation of the help command.
        Responsible for getting the requested command and
        calling the necessary helpers.

        This is a modified version of the original command_callback.

        Parameters
        -----------
        ctx: :class:`.Context`
            The invocation context.
        command: Optional[:class:`str`]
            The command to show the help for. If empty, the default help
            command implementation is executed.

        Raises
        -------
        :exc:`.CommandNotFound`
            If the command is not found.
        """
        if command is None:
            mapping = self.get_bot_mapping()
            return await self.send_bot_help(mapping)

        if command == 'flags':
            return await self.context.send(embed=self.get_flag_help_embed(), silent=True)

        cog = ctx.bot.get_cog(command)
        if cog is not None:
            return await self.send_cog_help(cog)

        cmd = self.context.bot.resolve_command(command)
        if cmd is None:
            return await super().command_callback(ctx, command=command)

        if isinstance(cmd, PartialCommandGroup):
            return await self.send_group_help(cmd)
        else:
            return await self.send_command_help(cmd)

    @staticmethod
    def command_permissions_can_run(command: PartialCommand, /) -> bool:
        """Checks if a command can be executed by checking if a command has set required permissions for the user.

        Returns `True` if the command has no permission set, otherwise `False`.

        Parameters
        -----------
        command: :class:`.commands.PartialCommand`
            The command to check.

        Returns
        --------
        :class:`bool`
            Whether the command can be executed or not.
        """
        if isinstance(command, app_commands.Group):
            return True

        command_permissions: Optional[list[str]] = getattr(command.callback, '__user_permissions__', None)
        if command_permissions is None:
            return True

        # channel_permissions = self.context.channel.permissions_for(self.context.author)
        #
        # if all(getattr(channel_permissions, perm, False) is True for perm in command_permissions):
        #     return True

        return False

    async def command_is_locked(self, command: PartialCommand, /) -> bool:
        """|coro|

        Checks whether a command is locked or not.

        Parameters
        -----------
        command: :class:`.commands.PartialCommand`
            The command to check.

        Returns
        --------
        :class:`bool`
            Whether the command is locked or not.
        """
        perms_can_run = self.command_permissions_can_run(command)
        try:
            context_can_run = await command.can_run(self.context)
        except AttributeError:
            context_can_run = True

        return not (perms_can_run and context_can_run)

    @staticmethod
    def command_is_hidden(command: PartialCommand) -> bool:
        """Checks whether a command can run in the current context.

        Returns `True` if the command is hidden, otherwise `False`.

        Parameters
        -----------
        command: :class:`.commands.PartialCommand`
            The command to check.

        Returns
        --------
        :class:`bool`
            Whether the command is hidden or not.
        """
        try:
            return command.hidden
        except AttributeError:
            return False

    async def _get_all_subcommands(
            self, command: PartialCommand | PartialCommandGroup, names: set[str]
    ) -> set[PartialCommand]:
        """|coro|

        Returns all subcommands of a command.

        Parameters
        -----------
        command: Union[:class:`.commands.PartialCommand`, :class:`.commands.PartialCommandGroup`]
            The command to get the subcommands from.
        names: set[:class:`str`]
            A set of already used command names.
            This is essential to avoid duplicates with, for example, hybrid commands.

        Returns
        --------
        set[:class:`.commands.PartialCommand`]
            The subcommands of the command.
        """
        subcommands: set[PartialCommand] = set()

        async def add_subcommand(cmd: PartialCommand):
            nonlocal subcommands, names
            if not self.command_is_hidden(cmd) and cmd.qualified_name not in names:
                setattr(cmd, 'is_locked', await self.command_is_locked(cmd))

                subcommands.add(cmd)
                names.add(cmd.qualified_name)

        if isinstance(command, PartialCommandGroup):
            for subcommand in command.walk_commands():
                await add_subcommand(subcommand)
        else:
            await add_subcommand(command)

        return subcommands

    async def filter_commands(
            self,
            commands: Iterable[PartialCommand],  # noqa
            /,
            *,
            sort: bool = False,
            key: Optional[Callable[[PartialCommand], Any]] = None
    ) -> List[PartialCommand]:
        """|coro|

        This is a Helper Function to filter the bots Application Commands, Hybrid Commands and Core Commands.

        Parameters
        ------------
        commands: Iterable[:class:`PartialCommand`]
            An iterable of commands that are getting filtered.
        sort: :class:`bool`
            Whether to sort the result.
        key: Optional[Callable[[`PartialCommand`], Any]]
            An optional key function to pass to :func:`py:sorted` that
            takes a :class:`Command` as its sole parameter. If ``sort`` is
            passed as ``True`` then this will default as the command name.

        Returns
        -------
        List[`PartialCommand`]
            The filtered Commands.
        """
        if sort and key is None:
            key = lambda c: c.name  # noqa

        iterator = commands if self.show_hidden else filter(lambda c: not self.command_is_hidden(c), commands)

        if getattr(self.context, 'guild', None) is None:
            iterator = filter(lambda c: not getattr(c, 'guild_only', False), iterator)

        ret = []
        used_names: set[str] = set()
        for command in iterator:
            ret.extend(await self._get_all_subcommands(command, used_names))

        if sort:
            ret.sort(key=key)
        return ret

    def get_command_signature(
            self,
            command: PartialCommand,
            *,
            no_signature: bool = False,
            shortened_signature: bool = False,
            with_prefix: bool = False
    ) -> str:
        """Takes an :class:`.PartialCommand` and returns a POSIX-like signature useful for help command output.

        This is a modified version of the original get_command_signature.

        Parameters
        ----------
        command: :class:`.PartialCommand`
            The command to get the signature for.
        no_signature: :class:`bool`
            Whether to return only the command name without signature.
        shortened_signature: :class:`bool`
            Whether to return the command with a shortened_signature signature.
        with_prefix: :class:`bool`
            Whether to include the prefix in the signature.

        Returns
        -------
        :class:`str`
            The command signature.
        """
        prefix = ('/' if isinstance(command, App) else self.context.clean_prefix) if with_prefix else ''

        if isinstance(command, App):
            if no_signature:
                return f'{prefix}{command.qualified_name}'.strip()

            if isinstance(command, app_commands.commands.Group):
                return f'{prefix}{command.qualified_name} <subcommand>'.strip()

            signature = ' '.join(
                f'<{option.name}>' if option.required else f'[{option.name}]' for option in command.parameters)
            return f'{prefix}{command.qualified_name} {signature}'.strip()

        signature = command.signature

        flags = self.get_command_flag_formatting(command)
        if flags:
            # If we have flags, we need to remove the flags from the signature
            # because this might be confusing for the user
            signature = re.sub(r'(.)flags(.)', '', signature).strip()
            # Also sub the multiple spaces with one space
            signature = re.sub(r'\s+', ' ', f'{signature} {flags}')

        parent = command.full_parent_name if command.parent else None
        alias = f'{parent} {command.name}' if parent else command.name

        if no_signature:
            return f'{prefix}{alias}'.strip()

        if shortened_signature and len(flags) > 3:
            signature = f'<flags...>'

        final = f'{prefix}{alias} {signature}' + (' [!]' if getattr(command, 'hidden', False) else '')
        return final.strip()

    async def send_bot_help(self, mapping: Mapping[commands.Cog | None, list[PartialCommand]]):
        """|coro|

        Sends the help command for the whole bot.
        This is a modified version of the original send_bot_help.

        Parameters
        ----------
        mapping: Mapping[Union[:class:`.commands.Cog`, None], List[:class:`.commands.PartialCommand`]]
            The mapping of the commands.
        """

        def key(cmd: PartialCommand) -> str:
            try:
                if isinstance(cmd, app_commands.commands.Group):
                    return cmd.parent.qualified_name
                elif isinstance(cmd, app_commands.commands.Command):
                    return cmd.binding.qualified_name
                else:
                    return cmd.cog.qualified_name
            except (AttributeError, IndexError):
                return 'No Category'

        entries = await self.filter_commands(self.get_commands(), sort=True, key=key)

        grouped: dict[commands.Cog, list[PartialCommand]] = {}
        for command in entries:
            cog = self.context.bot.get_cog(key(command))
            if cog and not self.command_is_hidden(command):
                grouped.setdefault(cog, []).append(command)

        grouped = {cog: cmds for cog, cmds in sorted(grouped.items(), key=lambda x: x[0].qualified_name)}
        await HelpPaginator.start(self.context, entries=grouped, per_page=1)

    async def get_front_page_embed(self) -> discord.Embed:
        """|coro|

        Returns the front page of the help command.

        Returns
        -------
        :class:`discord.Embed`
            The front page of the help command.
        """
        prefix = getattr(self.context, 'clean_prefix', '/')
        embed = discord.Embed(
            title=f'{self.context.client.user.name} Help',
            description='**```\nPlease use the Select Menu below to explore the corresponding category.```**',
            colour=helpers.Colour.coral()
        )
        embed.set_thumbnail(url=get_asset_url(self.context.guild))
        embed.add_field(
            name='More Help',
            value=(
                'Alternatively you can use the following commands to get information about a specific command or category:\n'
                f'- `{prefix}help <command>`\n'
                f'- `{prefix}help <category>`\n\n'
                f'You can also use `{prefix}help flags` to get an overview of how to use flags *(special command arguments)*.'
            ),
            inline=False
        )
        embed.add_field(
            name='Stats',
            value=(
                f'**Total Commands:** `{len(self.get_commands())}`\n'
                f'**Total Commands Invoked:** `{await self.total_commands_invoked()}`'
            ),
        )
        embed.set_author(name=self.context.client.user, icon_url=get_asset_url(self.context.client.user))
        embed.set_footer(text='I was created at')
        embed.timestamp = self.context.client.user.created_at
        return embed

    def get_flag_help_embed(self) -> discord.Embed:
        """|coro|

        Returns the flag help page of the help command.

        Returns
        -------
        :class:`discord.Embed`
            The front page of the help command.
        """
        prefix = getattr(self.context, 'clean_prefix', '/')
        embed = discord.Embed(
            title='Command Argument Overview',
            description='**```\nType command arguments without the brackets shown here!```**',
            colour=helpers.Colour.coral()
        )
        embed.set_thumbnail(url=get_asset_url(self.context.guild))
        embed.add_field(name='`<argument>`', value='This argument is **required**.', inline=False)
        embed.add_field(name='`[argument]`', value='This argument is **optional**.', inline=False)
        embed.add_field(name='`<A|B>`',
                        value='This means **multiple choice**, you can choose by using one. Although it must be A or B.', inline=False)
        embed.add_field(name='`<argument...>`', value='There are multiple arguments.', inline=False)
        embed.add_field(name='`<"argument">`',
                        value='This argument is case-sensitive and should be typed exactly as shown.', inline=False)
        embed.add_field(name='`<argument="A">`',
                        value='The default value if you dont provide one of this argument is **A**.', inline=False)

        embed.add_field(
            name='**Command Flags**',
            value='Flags are mostly available for commands with many arguments.\n'
                  'They can provide a better overview and are not required to be typed in.\n\n'
                  'Flags are prefixed with `--` and can be used like this:\n'
                  f'- `{prefix}command --flag1 argument1 --flag2 argument2`\n'
                  f'- `{prefix}command --flag1 argument1 --flag2 argument2 --flag3 argument3`\n'
                  'Some **first** flag may be used without the `--` prefix:\n'
                  f'- `{prefix}command argument1 --flag2 argument2`\n\n'
                  'Flag values can also be more than one word long, they end with the next flag you type (`--`):\n'
                  f'- `{prefix}command --flag1 my first argument --flag2 \"argument 2\"`', inline=False)

        embed.set_author(name=self.context.client.user, icon_url=get_asset_url(self.context.client.user))
        return embed

    async def send_cog_help(self, cog: commands.Cog):
        """|coro|

        Sends the help command for a cog.
        This is a modified version of the original send_cog_help.

        Parameters
        ----------
        cog: :class:`.commands.Cog`
            The cog to send the help for.
        """
        entries = await self.filter_commands(self.get_cog_commands(cog), sort=True)
        if not entries:
            return await self.context.send(self.command_not_found(cog.qualified_name), silent=True)

        await HelpPaginator.start(self.context, entries=entries, group=cog, with_index=False)

    @staticmethod
    def get_command_flag_formatting(command: PartialCommand, descripted: bool = False) -> str | list[dict]:
        """Returns a string with the command flag formatting.

        Parameters
        ----------
        command: PartialCommand
            The command to get the flag formatting from.
        descripted: bool
            Whether to include the flag description or not.

        Returns
        -------
        str
            The command flag formatting.
        """
        if isinstance(command, App):
            return [] if descripted else ''

        flags = command.clean_params.get('flags')

        if not flags:
            return [] if descripted else ''

        resolved: list[str] = []

        if descripted:
            for flag in flags.converter.get_flags().values():
                fmt = f'`--{flag.name}` - {flag.description}'
                resolved.append(fmt)

            chunked = list(discord.utils.as_chunks(resolved, 15))
            to_fields = []
            for i, chunk in enumerate(chunked):
                to_fields.append({'name': 'Flags' if i == 0 else '\u200b', 'value': chunk, 'inline': False})
            return to_fields
        else:
            for flag in flags.converter.get_flags().values():
                default = ""
                if flag.default is not None:
                    default = ' ' + (
                        f'{flag.default!r}' if (flag.annotation is str or Literal or Optional[str])
                        else str(flag.default)
                    )

                fmt = f'<--{flag.name}{default}>' if flag.required else f'[--{flag.name}{default}]'
                resolved.append(fmt)

            return ' '.join(resolved)

    @staticmethod
    def get_command_permission_formatting(command: PartialCommand, stringified: bool = False) -> str | dict:
        """Returns a string with the command permission formatting.

        Parameters
        ----------
        command: PartialCommand
            The command to get the permission formatting from.
        stringified: bool
            Whether to stringify the permissions or not.

        Returns
        -------
        str | dict
            The command permission formatting as a string or a dict.
        """
        if isinstance(command, app_commands.commands.Group):
            return '' if stringified else {}

        user_permissions: dict[str, bool] = getattr(command.callback, '__user_permissions__', None)
        bot_permissions: dict[str, bool] = getattr(command.callback, '__bot_permissions__', None)

        def fmt(p: str) -> str:
            return p.replace('_', ' ').title().replace('Guild', 'Server')

        resolved: dict[str, list] = {}

        if user_permissions:
            resolved.setdefault('user', [])
            for perm in user_permissions:
                resolved['user'].append(perm)

        if bot_permissions:
            resolved.setdefault('bot', [])
            for perm in bot_permissions:
                resolved['bot'].append(perm)

        if stringified:
            string: str = ''
            for group, perms in resolved.items():
                string += f'{group.title()}: {', '.join(fmt(p) for p in perms)}\n'
            return string
        else:
            return resolved

    async def command_formatting(self, command: PartialCommand) -> discord.Embed:
        """|coro|

        Returns an Embed with the command formatting.
        This is a modified version of the original command_formatting.

        Parameters
        ----------
        command: :class:`.commands.PartialCommand`
            The command to format.

        Returns
        -------
        :class:`discord.Embed`
            The formatted command.
        """
        embed = discord.Embed(colour=helpers.Colour.coral())
        embed.set_author(name='Command Help', icon_url=COMMAND_ICON_URL)

        embed.description = (
            f'**```py\n{self.get_command_signature(command)}```**\n'
            f'{cleanup_docstring(command.description, getattr(command, 'help', None))}'
        )

        if getattr(command, 'aliases', None):
            embed.add_field(
                name='<:equal:1208433651868504085> | **Aliases**',
                value=' '.join(f'`{alias}`' for alias in command.aliases),
                inline=False
            )

        if isinstance(command, commands.hybrid.HybridGroup):
            embed.add_field(
                name='<:very_cool:1208430876069724230> | **Hybrid Command**',
                value='Command can be used as a slash and text command.',
                inline=False
            )

        if isinstance(command, App):
            embed.add_field(
                name='<:ad:1072925284300496946> | **Slash Command**',
                value='Can only be used as a slash command.',
                inline=False
            )

        if getattr(command, 'cooldown', None) is not None:
            try:
                # Hybrid/Text Commands
                for_type = command._buckets._type.name.title()  # noqa
            except AttributeError:
                # App Commands
                for_type = cooldown_key(str(command.cooldown.key))  # noqa
            embed.add_field(
                name='\N{HOURGLASS} | **Cooldown**',
                value=f'**{command.cooldown.rate}x** per **{plural(command.cooldown.per):second}** for {for_type}',
                inline=False
            )

        if getattr(command, 'commands', None):
            resolved_sub_commands = [
                f'- `{self.get_command_signature(cmd)}`' for cmd in command.walk_commands() if
                not self.command_is_hidden(cmd)
            ]
            if resolved_sub_commands:
                embed.add_field(
                    name='<:command:1116734689999343637> | **Subcommands**',
                    value='\n'.join(resolved_sub_commands),
                    inline=False
                )

        if permissions := self.get_command_permission_formatting(command, stringified=True):
            embed.add_field(
                name=f'{self.locked_emoji} | **Required Permissions**',
                value=permissions,
                inline=False
            )

        if examples := command.extras.get('examples'):
            command_signature = self.get_command_signature(command, no_signature=True)
            embed.add_field(
                name='<:script:1208429751027372103> | **Examples**',
                value='\n'.join(f'* `{command_signature} {example}`' for example in examples),
                inline=False
            )

        for field in self.get_command_flag_formatting(command, descripted=True):
            embed.add_field(**field)

        return embed

    async def send_command_help(self, command: PartialCommand):
        """|coro|

        Sends the help command for a command.
        This is a modified version of the original send_command_help.

        Parameters
        ----------
        command: :class:`.commands.PartialCommand`
            The command to send the help for.
        """
        if self.command_is_hidden(command):
            return await self.context.send(self.command_not_found(command.name), silent=True)

        embed = await self.command_formatting(command)
        await self.context.send(embed=embed, silent=True)

    async def send_group_help(self, group: PartialCommandGroup):
        """|coro|

        Sends the help command for a group.
        This is a modified version of the original send_group_help.

        Parameters
        ----------
        group: :class:`.commands.PartialCommandGroup`
            The group to send the help for.
        """
        await self.send_command_help(group)

    @classmethod
    def temporary(cls, context: Context | discord.Interaction) -> 'PaginatedHelpCommand':
        """Returns a temporary instance of the help command.

        Useful for helper functions that require a help command instance.

        Parameters
        ----------
        context: Union[:class:`Context`, :class:`discord.Interaction`]
            The context to use for the temporary help command.

        Returns
        -------
        :class:`PaginatedHelpCommand`
            The temporary help command instance.
        """
        self = cls()
        self.context = context
        return self


class UserJoinView(discord.ui.View):
    def __init__(self, user: discord.Member, author: discord.Member):
        super().__init__(timeout=60.0)
        self.user = user
        self.author = author

    async def interaction_check(self, interaction: Interaction, /) -> bool:
        if interaction.user.id == self.author.id:
            return True

        await interaction.response.send_message(
            f'{tick(False)} You are not the author of this interaction.', ephemeral=True)
        return False

    @discord.ui.button(label='Join Position', style=discord.ButtonStyle.blurple,
                       emoji=discord.PartialEmoji(name='join', id=1096930367522472037))
    async def join_position(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        chunked_users = sorted(await interaction.guild.chunk(), key=lambda m: m.joined_at)

        def fmt(p, u, j):  # noqa
            if u == interaction.user:
                return f'<a:arrow_right:1113018784651956334> `{p}.` **{u}** <a:arrow_left:1113018813244518451>'
            return f'`{p}.` **{u}** ({discord.utils.format_dt(j, style='f')})'

        source = TextSource(prefix=None, suffix=None, max_size=4000)
        author_index = chunked_users.index(self.user)

        for p, u in enumerate(chunked_users[author_index - 6 if author_index - 6 > 0 else 0:author_index],
                              start=author_index - 5 if author_index - 5 > 0 else 1):
            source.add_line(fmt(p, u, u.joined_at))

        for p, u in enumerate(chunked_users[author_index:author_index + 6], start=author_index + 1):
            source.add_line(fmt(p, u, u.joined_at))

        embed = discord.Embed(title=f'Join Position in {interaction.guild}', color=0x2b2d31)
        embed.add_field(name='Joined',
                        value=f'{format_date(self.user.joined_at)}\n'
                              f'╰ **Join Position:** {author_index + 1}',
                        inline=False)
        embed.add_field(name='Position', value=source.pages[0], inline=False)
        embed.set_author(name=self.user, icon_url=get_asset_url(self.user))

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(embed=embed)
        self.stop()


class GuildUserJoinView(discord.ui.View):
    def __init__(self, author: discord.Member):
        super().__init__(timeout=60.0)
        self.author = author

    async def interaction_check(self, interaction: Interaction, /) -> bool:
        if interaction.user.id == self.author.id:
            return True
        await interaction.response.send_message(
            '<:redTick:1079249771975413910> You are not the author of this interaction.', ephemeral=True)
        return False

    @discord.ui.button(label='Join List', style=discord.ButtonStyle.blurple,
                       emoji=discord.PartialEmoji(name='join', id=1096930367522472037))
    async def join_list(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        chunked_users = sorted(await interaction.guild.chunk(), key=lambda m: m.joined_at)
        chunked = [[position, user, user.joined_at]
                   for position, user in enumerate(chunked_users, start=1)]

        def fmt(p, u, j):
            return f'`{p}.` **{u}** ({discord.utils.format_dt(j, style='f')})'

        source = TextSource(prefix=None, suffix=None, max_size=4000)
        for line in chunked:
            source.add_line(fmt(*line))

        class EmbedPaginator(BasePaginator[str]):

            async def format_page(self, entries: List[str], /) -> discord.Embed:
                embed = discord.Embed(title=f'Join List in {interaction.guild}', color=helpers.Colour.coral())
                embed.set_author(name=interaction.guild, icon_url=get_asset_url(interaction.guild))
                embed.set_footer(text=f'{plural(len(chunked_users)):entry|entries}')

                embed.description = '\n'.join(entries)

                return embed

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(view=self)
        await EmbedPaginator.start(interaction, entries=source.pages, per_page=1)
        self.stop()


@dataclass
class SnipedMessage:
    timestamp: datetime
    before: Optional[discord.Message] = None
    after: Optional[discord.Message] = None


class Meta(commands.Cog):
    """Commands for utilities related to Discord or the Percy itself."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot
        self.process = psutil.Process()

        self.old_help_command: Optional[commands.HelpCommand] = bot.help_command
        bot.help_command = PaginatedHelpCommand()
        bot.help_command.cog = self

        self.snipe_del_chache: Dict[int, Dict[int, List[SnipedMessage]]] = LRU(1024)  # noqa
        self.snipe_edit_chache: Dict[int, Dict[int, List[SnipedMessage]]] = LRU(1024)  # noqa

        if not hasattr(self, '_help_autocomplete_cache'):
            self.bot.loop.create_task(self._fill_autocomplete())

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='staff_animated', id=1076911514193231974, animated=True)

    def cog_unload(self):
        self.bot.help_command = self.old_help_command

    async def _fill_autocomplete(self) -> None:
        def key(command: PartialCommand) -> str:  # noqa
            return command.cog.qualified_name if command.cog else '\U0010ffff'

        entries: list[PartialCommand] = await self.bot.help_command.filter_commands(
            self.bot.commands, sort=True, key=key)

        _commands: dict[commands.Cog, list[PartialCommand]] = {}
        for name, children in itertools.groupby(entries, key=key):
            if name == '\U0010ffff':
                continue

            cog = self.bot.get_cog(name)
            assert cog is not None
            _commands[cog] = sorted(children, key=lambda c: c.qualified_name)

        self._help_autocomplete_cache: Dict[commands.Cog, List[PartialCommand]] = _commands

    @staticmethod
    async def mark_as_solved(thread: discord.Thread, user: discord.abc.User) -> None:
        tags: list[discord.ForumTag] = thread.applied_tags

        if not any(tag.id == PH_SOLVED_TAG for tag in tags):
            tags.append(discord.Object(id=PH_SOLVED_TAG))  # noqa

        await thread.edit(
            locked=True,
            archived=True,
            applied_tags=tags[:5],
            reason=f'Marked as solved by {user} (ID: {user.id})',
        )

    @commands.command(
        commands.hybrid_command,
        name='solved',
        description='Marks a thread as solved.',
        guild_only=True,
        cooldown=commands.CooldownMap(rate=1, per=20.0, type=commands.BucketType.channel),
    )
    @is_help_thread()
    @commands.guilds(PH_GUILD_ID)
    async def solved(self, ctx: GuildContext):
        """Marks a thread as solved."""
        assert isinstance(ctx.channel, discord.Thread)

        if can_close_threads(ctx):
            await ctx.message.add_reaction(tick(True))
            await self.mark_as_solved(ctx.channel, ctx.author._user)  # noqa
        else:
            msg = f'<@!{ctx.channel.owner_id}>, would you like to mark this thread as solved? This has been requested by {ctx.author.mention}.'
            confirm = await ctx.prompt(msg, author_id=ctx.channel.owner_id, timeout=300.0)

            if ctx.channel.locked:
                return

            if confirm:
                await ctx.stick(
                    True,
                    f'Marking as solved. Note that next time, you can mark the thread as solved yourself with `?solved`.'
                )
                await self.mark_as_solved(ctx.channel, ctx.channel.owner._user or ctx.user)  # noqa
            elif confirm is None:
                await ctx.stick(False, f'Timed out waiting for a response. Not marking as solved.')
            else:
                await ctx.stick(False, f'Not marking as solved.')

    @solved.error
    async def solved_error(self, ctx: GuildContext, error: commands.CommandError):
        if isinstance(error, AssertionError):
            await ctx.stick(False, 'This command can only be used in help threads.')

    @commands.command(
        commands.core_command,
        aliases=['src'],
        description='Shows parts of the Bots Source Command.'
    )
    async def source(self, ctx: Context, *, command: Optional[str] = None):
        """Displays my full source code or for a specific command.

        To display the source code of a subcommand, you can separate it by
        periods, e.g., tag.create for the creation subcommand of the tag command
        or by spaces.
        """
        source_url = 'https://github.com/klappstuhlpy/Percy'
        if command is None:
            return await ctx.send(source_url)

        obj = self.bot.resolve_command(command)
        if obj is None:
            return await ctx.stick(False, 'Could not find command.')

        if command == 'help':
            src = type(self.bot.help_command)
            filename = inspect.getsourcefile(src)
        else:
            item = inspect.unwrap(obj.callback)
            src = item.__code__
            filename = src.co_filename

        lines, firstlineno = inspect.getsourcelines(src)
        if filename is None:
            return await ctx.stick(False, 'Could not find source for command.')

        location_parts = filename.split(os.path.sep)
        cogs_index = location_parts.index('cogs')
        location = os.path.sep.join(location_parts[cogs_index:])  # Join parts from 'cogs' onwards

        final_url = f'<{source_url}/blob/master/{location}#L{firstlineno}-L{firstlineno + len(lines) - 1}>'

        embed = discord.Embed(title=f'Command: {command}', description=obj.description)
        embed.add_field(name='Source Code', value=f'[Jump to GitHub]({final_url})')
        embed.set_footer(text=f'{location}:{firstlineno}')
        await ctx.send(embed=embed)

    @commands.command(
        app_commands.command,
        name='help',
        description='Get help for a command or module.',
        guild_only=True
    )
    @app_commands.describe(module='Get help for a module.', command='Get help for a command')
    async def _help(self, interaction: discord.Interaction, module: Optional[str] = None,
                    command: Optional[str] = None):
        """Shows help for a command or module."""
        ctx: Context = await self.bot.get_context(interaction)
        await ctx.send_help(module or command)

    @_help.autocomplete('command')
    async def help_command_autocomplete(
            self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        if not hasattr(self, '_help_autocomplete_cache'):
            await interaction.response.autocomplete([])
            self.bot.loop.create_task(self._fill_autocomplete())  # noqa

        module = interaction.namespace.module
        if module is not None:
            module_commands = self._help_autocomplete_cache.get(module, [])
            cmds = [c for c in module_commands if c.qualified_name == module]
        else:
            cmds = list(itertools.chain.from_iterable(self._help_autocomplete_cache.values()))

        results = fuzzy.finder(current, [c.qualified_name for c in cmds])
        return [app_commands.Choice(name=res, value=res) for res in results[:25]]

    @_help.autocomplete('module')
    async def help_cog_autocomplete(
            self, interaction: discord.Interaction, current: str,  # noqa
    ) -> list[app_commands.Choice[str]]:
        if not hasattr(self, '_help_autocomplete_cache'):
            self.bot.loop.create_task(self._fill_autocomplete())  # noqa

        cogs = self._help_autocomplete_cache.keys()
        results = fuzzy.finder(current, [c.qualified_name for c in cogs])
        return [app_commands.Choice(name=res, value=res) for res in results][:25]

    @commands.hybrid_group(
        name='info',
        description='Shows info about a user or server.',
        invoke_without_command=True
    )
    async def info(self, ctx: Context):
        """Shows info about a user or server."""
        await ctx.send_help(ctx.command)

    @commands.command(
        info.command,
        name='features',
        description='Shows the features of a guild.',
        guild_only=True
    )
    async def info_features(self, ctx: Context, guild_id: str = None):
        """Shows the features of a guild."""

        if guild_id and not guild_id.isdigit():
            return await ctx.stick(False, 'Guild ID must be an int.')

        guild_id = guild_id or ctx.guild.id
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return await ctx.stick(False, f'Guild with ID `{guild_id}` not found.')

        features = list(
            map(lambda e: f'**{e[0]}** - {e[1]}', list(self.bot.get_guild_features(guild.features, only_current=True))))
        embed = discord.Embed(title='Guild Features',
                              timestamp=discord.utils.utcnow(),
                              color=self.bot.colour.coral())
        embed.set_footer(text=f'{plural(len(features)):feature|features}')
        await LinePaginator.start(ctx, entries=features, per_page=12, embed=embed, location='description')

    @commands.command(
        info.command,
        name='user',
        description='Shows info about a user.',
        guild_only=True
    )
    @app_commands.describe(user_id='The user ID to show info about. (Default: You)')
    async def info_user(self, ctx: Context, user_id: str = None):
        """Shows info about a user."""
        if ctx.interaction:
            await ctx.defer()

        if user_id and not user_id.isdigit():
            return await ctx.stick(False, 'User ID must be a number.')

        user_id = user_id or ctx.author.id
        user = await self.bot.get_or_fetch_member(ctx.guild, user_id)

        if not user:
            return await ctx.stick(False, 'User not found.')

        embed = discord.Embed()
        roles = [role.name.replace('@', '@\u200b') for role in getattr(user, 'roles', [])]
        embed.set_author(name=str(user))

        embed.add_field(name='ID', value=user.id, inline=False)
        embed.add_field(name='Created', value=format_date(user.created_at), inline=False)

        badges_to_emoji = {
            'partner': '<:partner:1110272293780848710>',  # Emoji Server
            'verified_bot_developer': '<:earlydev:1072925287123259423>',  # Parzival's Hideout
            'hypesquad_balance': '<:balance:1110272531811803216>',  # Emoji Server
            'hypesquad_bravery': '<:bravery:1110272621444083814>',  # Emoji Server
            'hypesquad_brilliance': '<:brilliance:1110272713299345438>',  # Emoji Server
            'bug_hunter': '<:lvl1:1072925290520653884>',  # Parzival's Hideout
            'hypesquad': '<:hypesquad_events:1110273043403644948>',  # Emoji Server
            'early_supporter': '<:earlysupporter:1072925288243146877>',  # Parzival's Hideout
            'bug_hunter_level_2': '<:lvl2:1072925293351800934>',  # Parzival's Hideout
            'staff': '<:staff_badge:1088921280947945562>',  # Emoji Server
            'discord_certified_moderator': '<:certified_mod_badge:1088921123967737926>',  # Emoji Server
            'active_developer': '<:activedev:1070318990406189057>',  # Playground
        }

        misc_flags_descriptions = {
            'team_user': 'Application Team User',
            'system': 'System User',
            'spammer': 'Spammer',
            'verified_bot': 'Verified Bot',
            'bot_http_interactions': 'HTTP Interactions Bot',
        }

        set_flags = {flag for flag, value in user.public_flags if value}
        subset_flags = set_flags & badges_to_emoji.keys()
        badges = [badges_to_emoji[flag] for flag in subset_flags]

        if ctx.guild is not None and ctx.guild.owner_id == user.id:
            badges.append('<:owner:1110273602324005025>')  # Emoji Server

        if isinstance(user, discord.Member) and user.premium_since is not None:
            embed.add_field(name='Boosted', value=format_date(user.premium_since), inline=False)
            badges.append('<:booster:1088921589145415751>')  # Emoji Server

        if badges:
            embed.description = ''.join(badges)

        activities = getattr(user, 'activities', None)
        if activities is None:
            activities = []

        spotify = next((act for act in activities if isinstance(act, discord.Spotify)), None)

        embed.add_field(
            name=f'Spotify',
            value=(
                f'**[{spotify.title}]({spotify.track_url})**'
                f'\n__By:__ {spotify.artist}'
                f'\n__On Album:__ {spotify.album}'
                f'\n`{datetime.timedelta(seconds=round((ctx.message.created_at - spotify.start).total_seconds()))}`/'
                f'`{datetime.timedelta(seconds=round(spotify.duration.total_seconds()))}`\n'
                if spotify
                else '*Not listening to anything...*'
            )
        )

        custom_activity = next((act for act in activities if isinstance(act, discord.CustomActivity)), None)
        activity_string = (
            f'`{discord.utils.remove_markdown(custom_activity.name)}`'
            if custom_activity and custom_activity.name
            else '*User has no custom status.*'
        )
        embed.add_field(
            name=f'Custom status',
            value=f'\n{activity_string}',
            inline=False
        )

        voice = getattr(user, 'voice', None)
        if voice is not None:
            vc = voice.channel
            other_people = len(vc.members) - 1
            voice = f'`{vc.name}` with {other_people} others' if other_people else f'`{vc.name}` by themselves'
            embed.add_field(name='Voice', value=voice, inline=False)

        if roles:
            embed.add_field(name='Roles', value=', '.join(roles) if len(roles) < 10 else f'{len(roles)} roles',
                            inline=False)

        remaining_flags = (set_flags - subset_flags) & misc_flags_descriptions.keys()
        if remaining_flags:
            embed.add_field(
                name='Public Flags',
                value='\n'.join(misc_flags_descriptions[flag] for flag in remaining_flags),
                inline=False,
            )

        perms = user.guild_permissions.value
        embed.add_field(name='Permissions', value=f'[{perms}](https://discordapi.com/permissions.html#{perms})',
                        inline=False)

        colour = user.colour
        if colour.value:
            embed.colour = colour

        embed.set_thumbnail(url=user.display_avatar.url)

        member = user

        user = await self.bot.fetch_user(user.id)
        if user.banner:
            embed.set_image(url=user.banner.url)

        embed.set_footer(text=f'Requested by: {ctx.author}')

        await ctx.send(embed=embed, view=UserJoinView(member, ctx.author))

    @commands.command(
        info.command,
        name='server',
        description='Shows info about a server.',
        guild_only=True
    )
    @app_commands.describe(guild_id='The ID of the server to show info about. (Default: Current server)')
    async def info_server(self, ctx: Context, guild_id: str = None):
        """Shows info about the current or a specified server."""

        if not guild_id or (guild_id and not await self.bot.is_owner(ctx.author)):
            if not ctx.guild:
                return await ctx.stick(False, 'You must specify a guild ID.')
            guild = ctx.guild
        else:
            if not guild_id.isdigit():
                return await ctx.stick(False, 'Guild ID must be a number.')
            guild = self.bot.get_guild(int(guild_id))

        if not guild:
            return await ctx.stick(False, 'Guild not found.')

        roles = [role.name.replace('@', '@\u200b') for role in guild.roles]

        if not guild.chunked:
            async with ctx.channel.typing():
                await guild.chunk(cache=True)

        everyone = guild.default_role
        everyone_perms = everyone.permissions.value
        secret = Counter()
        totals = Counter()

        for channel in guild.channels:
            allow, deny = channel.overwrites_for(everyone).pair()
            perms = discord.Permissions((everyone_perms & ~deny.value) | allow.value)
            channel_type = type(channel)
            totals[channel_type] += 1
            if not perms.read_messages:
                secret[channel_type] += 1
            elif isinstance(channel, discord.VoiceChannel) and (not perms.connect or not perms.speak):
                secret[channel_type] += 1

        embed = discord.Embed(title=guild.name, description=f'**ID**: {guild.id}\n**Owner**: {guild.owner}')
        embed.set_thumbnail(url=get_asset_url(guild))

        channel_info = []
        key_to_emoji = {
            discord.TextChannel: '<:text_channel:1079445372562329610>',
            discord.VoiceChannel: '<:voice_channel:1079445548853112873>',
        }
        for key, total in totals.items():
            secrets = secret[key]
            try:
                emoji = key_to_emoji[key]  # noqa
            except KeyError:
                continue

            if secrets:
                channel_info.append(f'{emoji} {total} (<:channel_locked:1079445956556238888> {secrets} locked)')
            else:
                channel_info.append(f'{emoji} {total}')

        embed.add_field(name='Features', value=f'Use `{ctx.prefix}info features` to see the features of this server.')

        embed.add_field(name='Channels', value='\n'.join(channel_info))

        if guild.premium_tier != 0:
            boosts = f'Level {guild.premium_tier}\n{guild.premium_subscription_count} boosts'
            last_boost = max(guild.members, key=lambda m: m.premium_since or guild.created_at)
            if last_boost.premium_since is not None:
                boosts = f'{boosts}\nLast Boost: {last_boost} ({discord.utils.format_dt(last_boost.premium_since, style='R')})'
            embed.add_field(name='Boosts', value=boosts, inline=False)

        bots = sum(m.bot for m in guild.members)
        fmt = f'Total: {guild.member_count} ({plural(bots):bot} `{bots / guild.member_count:.2%}`)'

        embed.add_field(name='Members', value=fmt, inline=False)
        embed.add_field(name='Roles', value=', '.join(roles) if len(roles) < 10 else f'{len(roles)} roles')

        emoji_stats = Counter()
        for emoji in guild.emojis:
            if emoji.animated:
                emoji_stats['animated'] += 1
                emoji_stats['animated_disabled'] += not emoji.available
            else:
                emoji_stats['regular'] += 1
                emoji_stats['disabled'] += not emoji.available

        fmt = (
            f'Regular: {emoji_stats['regular']}/{guild.emoji_limit}\n'
            f'Animated: {emoji_stats['animated']}/{guild.emoji_limit}\n'
        )
        if emoji_stats['disabled'] or emoji_stats['animated_disabled']:
            fmt = f'{fmt}Disabled: {emoji_stats['disabled']} regular, {emoji_stats['animated_disabled']} animated\n'

        fmt = f'{fmt}Total Emoji: {len(guild.emojis)}/{guild.emoji_limit * 2}'
        embed.add_field(name='Emoji', value=fmt, inline=False)

        if guild.banner:
            embed.set_image(url=guild.banner.url)

        embed.set_footer(text='Created').timestamp = guild.created_at
        await ctx.send(embed=embed, view=GuildUserJoinView(ctx.author))

    @commands.command(
        description='Shows the avatar of a user.',
        aliases=['av'],
    )
    @app_commands.describe(user='The user to show the avatar of. (Default: You)')
    async def avatar(self, ctx: Context, *, user: Union[discord.Member, discord.User] = None):
        """Shows a user's enlarged avatar (if possible)."""
        user = user or ctx.author
        avatar = user.display_avatar.with_static_format('png')
        embed = discord.Embed(colour=discord.Colour.from_rgb(
            *self.bot.get_cog('Emoji').render.get_dominant_color(io.BytesIO(await avatar.read()))))  # type: ignore
        embed.set_author(name=str(user), url=avatar)
        embed.set_image(url=avatar)
        await ctx.send(embed=embed)

    @commands.command(
        name='charinfo',
        description='Shows you information about a number of characters.',
    )
    @app_commands.describe(characters='A String of characters that should be introspected.')
    async def charinfo(self, ctx: Context, *, characters: str):
        """Shows you information on up to 50 unicode characters."""
        match = re.match(r'<(a?):(\w+):(\d+)>', characters)
        if match:
            raise commands.BadArgument('Cannot get information on custom emoji.')

        if len(characters) > 50:
            raise commands.BadArgument(f'Too many characters ({len(characters)}/50)')

        def char_info(char: str) -> tuple[str, str]:
            digit = f'{ord(char):x}'
            if len(digit) <= 4:
                u_code = f'\\u{digit:>04}'
            else:
                u_code = f'\\U{digit:>08}'
            url = f'https://www.compart.com/en/unicode/U+{digit:>04}'
            name = f'[{unicodedata.name(char, '')}]({url})'
            info = f'`{u_code.ljust(10)}`: {name} - {discord.utils.escape_markdown(char)}'
            return info, u_code

        char_list, raw_list = zip(*(char_info(c) for c in characters), strict=True)
        embed = discord.Embed(title='Char Info', colour=self.bot.colour.coral())

        if len(characters) > 1:
            embed.add_field(name='Full Text', value=f'`{''.join(raw_list)}`', inline=False)

        await LinePaginator.start(ctx, entries=char_list, per_page=10, embed=embed, location='description')

    @commands.command(
        commands.group,
        name='prefix',
        description='Manages or show the server\'s custom prefixes.',
        invoke_without_command=True,
        guild_only=True
    )
    async def prefix(self, ctx: Context):
        """Manages the server's custom prefixes."""
        prefixes = self.bot.get_guild_prefixes(ctx.guild)
        del prefixes[1]

        embed = discord.Embed(title='Prefix List', colour=self.bot.colour.coral())
        embed.set_author(name=ctx.guild.name, icon_url=get_asset_url(ctx.guild))
        embed.set_thumbnail(url=get_asset_url(ctx.guild))
        embed.set_footer(text=f'{len(prefixes)} prefixes')
        embed.description = '\n'.join(f'`{index}.` {elem}' for index, elem in enumerate(prefixes, 1))
        await ctx.send(embed=embed)

    @commands.command(
        prefix.command,
        name='add',
        description='Appends a prefix to the list of custom prefixes.',
        ignore_extra=False,
        guild_only=True
    )
    @commands.permissions(user=['manage_guild'])
    async def prefix_add(self, ctx: GuildContext, prefix: Annotated[str, Prefix]):
        """Adds a prefix to the list of custom prefixes.
        Multi-word prefixes must be quoted.
        """

        current_prefixes = self.bot.get_raw_guild_prefixes(ctx.guild.id)
        current_prefixes.append(prefix)
        try:
            await self.bot.set_guild_prefixes(ctx.guild, current_prefixes)
        except Exception as e:
            raise commands.CommandError(f'Unkown error: {e}')
        else:
            await ctx.stick(True, 'Prefix added.')

    @prefix_add.error
    async def prefix_add_error(self, ctx: GuildContext, error: commands.CommandError):
        if isinstance(error, commands.TooManyArguments):
            await ctx.stick(False, 'Too many arguments. Did you forget to quote a multi-word prefix?')

    @commands.command(
        prefix.command,
        name='remove',
        aliases=['delete'],
        ignore_extra=False,
        guild_only=True
    )
    @commands.permissions(user=['manage_guild'])
    async def prefix_remove(self, ctx: GuildContext, prefix: Annotated[str, Prefix]):
        """Removes a prefix from the list of custom prefixes.
        This is the inverse of the 'prefix add' command. You can
        use this to remove prefixes from the default set as well.
        You must have Manage Server permission to use this command.
        """
        current_prefixes = self.bot.get_raw_guild_prefixes(ctx.guild.id)

        try:
            current_prefixes.remove(prefix)
        except ValueError:
            raise commands.CommandError(f'{prefix!r} is not in the list of prefixes.')

        try:
            await self.bot.set_guild_prefixes(ctx.guild, current_prefixes)
        except Exception as e:
            raise commands.CommandError(f'Unkown error: {e}')
        else:
            await ctx.stick(True, 'Prefix removed.')

    @commands.command(
        prefix.command,
        name='reset',
        description='Removes all custom prefixes.',
        ignore_extra=False,
        guild_only=True
    )
    @commands.permissions(user=['manage_guild'])
    async def prefix_reset(self, ctx: GuildContext):
        """Removes all custom prefixes.
        After this, the bot will listen to only mention prefixes.
        You must have Manage Server permission to use this command.
        """
        await self.bot.set_guild_prefixes(ctx.guild, [])
        await ctx.stick(True, 'Cleared all prefixes.')

    @commands.command(
        name='ping',
        description='Shows some client and API latency information.',
    )
    async def ping(self, ctx: Context):
        """Shows some Client and API latency information."""
        message = None

        def build_embed(content: str) -> discord.Embed:
            return discord.Embed(
                title='Pong!',
                colour=helpers.Colour.coral(),
                description=content
            )

        api_readings: List[float] = []
        websocket_readings: List[float] = []

        for _ in range(6):
            text = '*Calculating round-trip time...*\n\n'
            text += '\n'.join(
                f'Reading `{index + 1}`: `{reading * 1000:.2f}ms`' for index, reading in enumerate(api_readings))

            if api_readings:
                average, stddev = mean_stddev(api_readings)

                text += f'\n\n**Average:** `{average * 1000:.2f}ms` \N{PLUS-MINUS SIGN} `{stddev * 1000:.2f}ms`'
            else:
                text += '\n\n*No readings yet.*'

            if websocket_readings:
                average = sum(websocket_readings) / len(websocket_readings)

                text += f'\n**Websocket latency:** `{average * 1000:.2f}ms`'
            else:
                text += f'\n**Websocket latency:** `{self.bot.latency * 1000:.2f}ms`'

            if _ == 5:
                gateway_url = await self.bot.http.get_gateway()
                start = time.monotonic()
                async with self.bot.session.get(f'{gateway_url}/ping'):
                    end = time.monotonic()
                    gateway_ping = (end - start) * 1000

                text += f'\n**Gateway latency:** `{gateway_ping:.2f}ms`'

            if message:
                before = time.perf_counter()
                await message.edit(embed=build_embed(text))
                after = time.perf_counter()

                api_readings.append(after - before)
            else:
                before = time.perf_counter()
                message = await ctx.send(embed=build_embed(text))
                after = time.perf_counter()

                api_readings.append(after - before)

            if self.bot.latency > 0.0:
                websocket_readings.append(self.bot.latency)

    @staticmethod
    async def say_permissions(
            ctx: Context, member: discord.Member, channel: Union[discord.abc.GuildChannel, discord.Thread]
    ):
        permissions = channel.permissions_for(member)
        embed = discord.Embed(colour=member.colour)
        avatar = member.display_avatar.with_static_format('png')
        embed.set_author(name=str(member), url=avatar)
        allowed, denied = [], []
        for name, value in permissions:
            name = name.replace('_', ' ').replace('guild', 'server').title()
            if value:
                allowed.append(name)
            else:
                denied.append(name)

        embed.add_field(name='Allowed', value='\n'.join(allowed))
        embed.add_field(name='Denied', value='\n'.join(denied))
        await ctx.send(embed=embed)

    @commands.command(
        commands.group,
        name='permissions',
        description='Shows permissions for a member or the bot in a specific channel.',
        guild_only=True
    )
    async def permissions(self, ctx: Context):
        """Shows permissions for a member or the bot in a specific channel."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @commands.command(
        permissions.command,
        name='user',
        description='Shows a member\'s permissions in a specific channel.',
        guild_only=True
    )
    async def permissions_user(
            self,
            ctx: GuildContext,
            member: discord.Member = None,
            channel: Union[discord.abc.GuildChannel, discord.Thread] = None,
    ):
        """Shows a member's permissions in a specific channel.
        If no channel is given then it uses the current one.
        You cannot use this in private messages. If no member is given then
        the info returned will be yours.
        """
        channel = channel or ctx.channel
        if member is None:
            member = ctx.author

        await self.say_permissions(ctx, member, channel)

    @commands.command(
        permissions.command,
        name='bot',
        description='Shows the bot\'s permissions in a specific channel.',
        guild_only=True
    )
    async def permissions_bot(self, ctx: GuildContext, *,
                              channel: Union[discord.abc.GuildChannel, discord.Thread] = None):
        """Shows the bots permissions in a specific channel.
        If no channel is given then it uses the current one.
        This is a good way of checking if the bot has the permissions needed
        to execute the commands it wants to execute.
        """
        channel = channel or ctx.channel
        member = ctx.guild.me
        await self.say_permissions(ctx, member, channel)

    @commands.command(
        commands.hybrid_command,
        name='snipe',
        description='Snipes a deleted message.',
        guild_only=True
    )
    async def snipe(self, ctx: GuildContext, channel: discord.TextChannel = None):
        """Snipes a deleted message.
        If no channel is given, then it uses the current one.
        """
        channel = channel or ctx.channel
        try:
            obj = self.snipe_del_chache[ctx.guild.id][channel.id][0]
        except (IndexError, AttributeError, KeyError):
            raise commands.CommandError('I have not sniped any messages in this channel.')

        embed = discord.Embed(
            description=f'### Content\n{truncate(str(obj.before.clean_content), 4000)}',
            color=self.bot.colour.coral(),
            timestamp=obj.timestamp)
        embed.set_author(name=obj.before.author, icon_url=get_asset_url(obj.before.author))
        embed.set_footer(text='Deleted at')
        await ctx.send(embed=embed)

    @commands.command(
        commands.hybrid_command,
        name='esnipe',
        description='Snipes a deleted edited.',
        guild_only=True
    )
    async def esnipe(self, ctx: GuildContext, channel: discord.TextChannel = None):
        """Snipes a deleted edited.
        If no channel is given, then it uses the current one.
        """
        channel = channel or ctx.channel
        try:
            obj = self.snipe_edit_chache[ctx.guild.id][channel.id][0]
        except (IndexError, AttributeError, KeyError):
            raise commands.CommandError('I have not sniped any messages in this channel.')

        embed = discord.Embed(
            description=f'### Before\n'
                        f'{truncate(str(obj.before.clean_content), 2000)}\n'
                        f'### After\n'
                        f'{truncate(str(obj.after.clean_content), 2000)}',
            color=self.bot.colour.coral(),
            timestamp=obj.timestamp
        )
        embed.set_author(name=obj.before.author, icon_url=get_asset_url(obj.before.author))
        embed.add_field(name='Message', value=obj.before.jump_url)
        embed.set_footer(text='Edited at')
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.guild is None:
            return

        cache = self.snipe_del_chache.setdefault(message.guild.id, {})
        cache = cache.setdefault(message.channel.id, [])
        cache.append(SnipedMessage(before=message, timestamp=discord.utils.utcnow()))

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.guild is None:
            return

        cache = self.snipe_edit_chache.setdefault(before.guild.id, {})
        cache = cache.setdefault(before.channel.id, [])
        cache.append(SnipedMessage(after=after, before=before, timestamp=discord.utils.utcnow()))


async def setup(bot: Percy):
    await bot.add_cog(Meta(bot))
