from __future__ import annotations

import datetime
import inspect
import io
import itertools
import os
import re
import time
from collections import Counter
from typing import (
    Optional, Union, TYPE_CHECKING, Mapping, List, Annotated, Dict,
    NamedTuple, Sequence, Type, Iterable, Callable, Literal, Any
)

import discord
import psutil
import unicodedata
from dateutil.relativedelta import relativedelta
from discord import app_commands, Interaction
from discord.ext import commands
from lru import LRU

from . import command, command_permissions
from .utils import fuzzy, helpers
from .utils.converters import Prefix
from .utils.formats import plural, format_date
from .utils.paginator import BasePaginator, TextSource, LinePaginator
from .utils.constants import PH_HELP_FORUM, PH_SOLVED_TAG, PartialCommand, PartialCommandGroup, Hybrid, Core, App
from .utils.timetools import mean_stddev, RelativeDelta

if TYPE_CHECKING:
    from bot import Percy
    from .utils.context import GuildContext, Context

COMMAND_ICON_URL = 'https://cdn.discordapp.com/emojis/782701715479724063.webp?size=32'
INFO_ICON_URL = 'https://cdn3.emoji.gg/emojis/4765-discord-info-white-theme.png'


def cleanup_docstring(s1: Optional[str], s2: Optional[str]) -> str:
    if not s1 and not s2:
        return '*No help found.*'

    if s1 == s2:
        return inspect.cleandoc(s1)

    if s1 or s2:
        return inspect.cleandoc(s1 or s2)

    if s1 and s2:
        return inspect.cleandoc(f"{s1}\n\n{s2}")


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
        description='The maximum number of messages the thread needs to be considered active. Defaults to 5.',
    )
    threshold: relativedelta = commands.flag(
        default=relativedelta(minutes=5),
        description='How old the thread needs to be (e.g. "10m" or "22m"). Defaults to 5 minutes.',
        converter=RelativeDelta(),
    )


class GroupHelpPaginator(BasePaginator[PartialCommand]):
    _ctx: Context  # possible Context from Interaction
    group: commands.Group | commands.Cog  # The current Group displayed
    groups: Optional[Dict[commands.Cog, list[PartialCommand]]]  # The list of all groups from this help menu

    async def format_page(self, entries: List[commands.Command]):
        emoji = getattr(self.group, 'display_emoji', None) or ''
        embed = discord.Embed(title=f'{emoji} {self.group.qualified_name} Commands',
                              description=self.group.description,
                              colour=helpers.Colour.darker_red())

        is_app_command_cog = False
        if isinstance(self.group, commands.Cog):
            if not list(filter(lambda c: not c.hidden, self.group.get_commands())):
                is_app_command_cog = True

        helper = PaginatedHelpCommand.temporary(self._ctx)
        for cmd in entries:
            signature = helper.get_command_signature(cmd, with_prefix=False)  # type: ignore
            embed.add_field(name=signature, value=cmd.description or 'No help given...', inline=False)

        embed.set_author(name=f'{plural(len(self.entries)):command}', icon_url=COMMAND_ICON_URL)

        if is_app_command_cog:
            embed.set_footer(text=f'Those Commands are only available as Slash Commands.')
        else:
            embed.set_footer(text=f'Use "{self._ctx.clean_prefix}help command" for more info on a command.')

        return embed

    @classmethod
    async def start(
            cls: Type[GroupHelpPaginator],
            context: Context | discord.Interaction,
            /,
            *,
            entries: List[PartialCommand],
            per_page: int = 6,
            clamp_pages: bool = True,
            timeout: int = 180,
            search_for: bool = False,
            ephemeral: bool = False,
            **kwargs: Any,
    ) -> GroupHelpPaginator[PartialCommand]:
        """Overwritten to add the view to the message and edit message, not send new."""
        self = cls(entries=entries, per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)
        self.ctx = context

        self._ctx = context
        if isinstance(context, discord.Interaction):
            self._ctx = await self.ctx.client.get_context(context.message)

        self.groups = kwargs.pop('groups') if 'groups' in kwargs else None
        self.group = kwargs.pop('group')

        page: discord.Embed = await self.format_page(self.pages[0])  # type: ignore

        if self.groups is not None:
            self.add_item(CategorySelect(self.groups, getattr(context, 'bot', context.client)))  # type: ignore
        self.update_buttons()

        if kwargs.pop('edit', False):
            await self._edit(context, embed=page, view=self)
        else:
            if self.total_pages <= 1:
                await self._send(context, embed=page, ephemeral=ephemeral)
            else:
                await self._send(context, embed=page, view=self, ephemeral=ephemeral)
        return self

    @classmethod
    async def _edit(cls, context, **kwargs) -> discord.Message:
        if isinstance(context, discord.Interaction):
            if context.response.is_done():
                msg = await context.edit_original_response(**kwargs)
            else:
                msg = await context.response.edit_message(**kwargs)
        else:
            msg = await context.message.edit(**kwargs)
        return msg


class CategorySelect(discord.ui.Select):
    def __init__(self, entries: dict[commands.Cog, list[commands.Command]], bot: Percy):
        super().__init__(
            placeholder='Select a category to view...',
            row=1,
        )
        self.commands: dict[commands.Cog, list[commands.Command], list[app_commands.AppCommand]] = entries
        self.bot: Percy = bot
        self.__fill_options()

    def __fill_options(self) -> None:
        self.add_option(
            label='Start Page',
            emoji=discord.PartialEmoji(name="vegaleftarrow", id=1066024601332748389),
            value='__index',
            description='The front page of the Help Menu.',
        )

        for cog, cmds in self.commands.items():
            if not cmds:
                continue

            description = cog.description.split('\n', 1)[0] or None
            emoji = getattr(cog, 'display_emoji', None)
            self.add_option(label=cog.qualified_name, value=cog.qualified_name, description=description, emoji=emoji)

    async def callback(self, ctx: discord.Interaction):
        assert self.view is not None
        value = self.values[0]
        if value == '__index':
            await FrontHelpPaginator.start(ctx, entries=self.commands, edit=True)
        else:
            cog = self.bot.get_cog(value)
            if cog is None:
                await ctx.response.send_message('Somehow this category does not exist?', ephemeral=True)
                return

            cmds = self.commands[cog]
            if not cmds:
                await ctx.response.send_message('This category has no commands for you', ephemeral=True)
                return

            await GroupHelpPaginator.start(ctx, entries=cmds, edit=True, group=cog, groups=self.view.groups)


class FrontHelpPaginator(BasePaginator[str]):
    _ctx: Context  # possible Context from Interaction
    groups: dict[commands.Cog, list[commands.Command], list[app_commands.AppCommand]]

    async def format_page(self, entries: List, /):
        embed = discord.Embed(title=f"{self.ctx.client.user.name}'s Help Page", colour=helpers.Colour.darker_red())
        embed.set_thumbnail(url=self.ctx.client.user.avatar.url)

        pag_help: PaginatedHelpCommand = self.ctx.client.help_command.temporary(self.ctx)  # type: ignore
        if self._current_page == 0:
            embed.description = inspect.cleandoc(
                f"""
                ## Introduction
                Here you can find all *Message-/Slash-Commands* for {self.ctx.client.user.name}.
                Try using the dropdown to navigate through the categories to get a list of all Commands.

                I'm open source! You can find my code on [GitHub](https://github.com/klappstuhlpy/Percy).
                ## More Help
                Alternatively you can use the following Commands to get Information about a specific Command or Category:
                - `{self._ctx.clean_prefix}help` *`command`*
                - `{self._ctx.clean_prefix}help` *`category`*
                ## Support
                For more help, consider joining the official server over at
                https://discord.com/invite/eKwMtGydqh.
                ## Stats
                Total of **{await pag_help.total_commands_invoked()}** command runs.
                Currently are **{len(pag_help.all_commands)}** commands loaded.
                """
            )
        elif self._current_page == 1:
            entries = (
                ('<argument>', 'This argument is **required**.'),
                ('[argument]', 'This argument is **optional**.'),
                ('<A|B>', 'This means **multiple choice**, you can choose by using one. Although it must be A or B.'),
                ('<argument...>', 'There are multiple Arguments.'),
                ("<'argument'>", 'This argument is case-sensitive and should be typed exaclty as shown.'),
                ('<argument=A>', 'The default value if you dont provide one of this argument is A.'),
                ("Flags",
                 "Flags are mostly available for commands with many arguments.\n"
                 "They can provide a better overview and are not required to be typed in.\n"
                 "\n"
                 "Flags are prefixed with `--` and can be used like this:\n"
                 f"- `{self._ctx.clean_prefix}command --flag1 argument1 --flag2 argument2`\n"
                 f"- `{self._ctx.clean_prefix}command --flag1 argument1 --flag2 argument2 --flag3 argument3`\n"
                 f"\n"
                 f"Flag values can also be more than one word long, they end with the next flag you type (`--`):\n"
                 f"- `{self._ctx.clean_prefix}command --flag1 my first argument --flag2 'argument 2`'"
                 ),
                ('\u200b',
                 '<:discord_info:1113421814132117545> **Important:**\n'
                 'Do not type the arguments in brackets.\n'
                 'Most of the Commands are **Hybrid Commands**, which means that you can use them as Slash Commands or Message Commands.'
                 ),
            )
            for name, value in entries:
                embed.add_field(name=name, value=value, inline=False)

        elif self._current_page == 2:
            embed.description = inspect.cleandoc(
                f"""
                ## License
                Percy is licensed and underlying the [MPL-2.0 License](https://www.tldrlegal.com/license/mozilla-public-license-2-0-mpl-2) and Guidelines.
                ## Credits
                I was made by <@991398932397703238>.
                
                Any questions regarding licensing and credits can be directed to <@991398932397703238>.
                """
            )

        embed.set_footer(text=f'I was created at')
        embed.timestamp = self.ctx.client.user.created_at

        return embed

    @classmethod
    async def start(
            cls: Type[FrontHelpPaginator],
            context: Context | discord.Interaction,
            /,
            *,
            entries: Dict,
            per_page: int = 1,
            clamp_pages: bool = True,
            timeout: int = 180,
            search_for: bool = False,
            ephemeral: bool = False,
            **kwargs: Any,
    ) -> FrontHelpPaginator[str]:
        """Overwritten to add the SelectMenu"""
        self = cls(entries=['', '', ''], per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)

        self.ctx = context
        self.groups = entries

        self._ctx = context
        if isinstance(context, discord.Interaction):
            self._ctx = await self.ctx.client.get_context(context.message)

        page = await self.format_page(self.pages[0])
        kwargs = {'view': self, 'embed' if isinstance(page, discord.Embed) else 'content': page}
        if self.total_pages <= 1:
            kwargs.pop('view')

        self.add_item(CategorySelect(entries, self.ctx.client))  # type: ignore
        self.update_buttons()

        if kwargs.pop('edit', False):
            if isinstance(context, discord.Interaction):
                self.msg = await context.response.edit_message(**kwargs)
            else:
                self.msg = await context.message.edit(embed=page, view=self)
        else:
            self.msg = await cls._send(context, ephemeral, **kwargs)
        return self


class PaginatedHelpCommand(commands.HelpCommand):
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

    # noinspection PyProtectedMember
    @property
    def all_commands(self) -> set[PartialCommand]:
        return set(self.context.client.commands) | set(self.context.client.tree._get_all_commands())

    @staticmethod
    def get_cog_commands(cog: commands.Cog) -> set[PartialCommand]:
        return set(cog.get_commands()) | set(cog.get_app_commands())

    async def total_commands_invoked(self) -> int:
        query = "SELECT COUNT(*) as total FROM commands;"
        return await self.context.client.pool.fetchval(query)  # type: ignore

    async def command_callback(self, ctx: Context, /, *, command: Optional[str] = None):  # noqa
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

        cog = ctx.bot.get_cog(command)
        if cog is not None:
            return await self.send_cog_help(cog)

        cmd = self.context.bot.resolve_command(command)
        if cmd is None:
            # Maybe it's a SubCommand? Let the parent class deal with it
            return await super().command_callback(ctx, command=command)

        if isinstance(cmd, PartialCommandGroup):
            return await self.send_group_help(cmd)
        else:
            return await self.send_command_help(cmd)

    async def maybe_hidden(self, command: PartialCommand, user: Optional[discord.Member | discord.User] = None):  # noqa
        """|coro|

        Checks if a command is hidden for a user.

        Parameters
        ----------
        command: :class:`.PartialCommand`
            The command to check.
        user: Union[:class:`.discord.Member`, :class:`.discord.User`]
            The user to check for.

        Returns
        -------
        bool
            Whether the command is hidden for the user.
        """
        if hasattr(command, 'hidden'):
            if user is None:
                return command.hidden
            is_owner: bool = await self.context.bot.is_owner(user)
            return command.hidden and not is_owner
        return False

    async def filter_commands(
            self,
            cmd_iter: Iterable[PartialCommand],
            /,
            *,
            sort: bool = False,
            key: Optional[Callable] = lambda c: c.name,
    ) -> List[PartialCommand]:
        """|coro|

        This is a Helper Function to filter the bots Application Commands, Hybrid Commands and Core Commands.

        Parameters
        ----------
        cmd_iter: Iterable[PartialCommand]
            The Iterable of Commands to filter.
        sort: bool
            Whether to sort the Commands by their name.
        key: Optional[Callable]
            The Key to sort the Commands by.

        Returns
        -------
        List[PartialCommand]
            The filtered Commands.
        """
        resolved = []
        resolved_names = set()

        if not hasattr(self.context, 'author'):
            class FakeContext:
                author = None
            
            # We are doing this because on startup, the `filter_commands` method
            # will be triggered without a context,
            # which will result in an AttributeError because for the 
            # `maybe_hidden` method, we need the author
            # attribute of the context.
            
            self.context = FakeContext()  # noqa

        for cmd in cmd_iter:
            if isinstance(cmd, PartialCommandGroup):
                if isinstance(cmd, (Hybrid, Core)):
                    if await self.maybe_hidden(cmd, self.context.author):
                        continue

                for subcmd in cmd.commands:
                    if (
                            isinstance(subcmd, commands.hybrid.HybridAppCommand)
                            or subcmd.qualified_name in resolved_names
                            or subcmd.name in resolved_names
                            or isinstance(subcmd, (Hybrid, Core)) and await self.maybe_hidden(subcmd, self.context.author)
                    ):
                        continue

                    if isinstance(subcmd, PartialCommandGroup):
                        for subsubcmd in subcmd.commands:
                            if (
                                    isinstance(subsubcmd, commands.hybrid.HybridAppCommand)
                                    or subsubcmd.qualified_name in resolved_names
                                    or subsubcmd.name in resolved_names
                                    or isinstance(subsubcmd, (Hybrid, Core)) and await self.maybe_hidden(subsubcmd, self.context.author)
                            ):
                                continue

                            resolved.append(subsubcmd)
                            resolved_names.add(subsubcmd.qualified_name)
                    else:
                        resolved.append(subcmd)
                        resolved_names.add(subcmd.qualified_name)
                else:
                    resolved.append(cmd)
                    resolved_names.add(cmd.qualified_name)
            else:
                if (
                        isinstance(cmd, (Hybrid, Core)) and await self.maybe_hidden(cmd, self.context.author)
                        or isinstance(cmd, commands.hybrid.HybridAppCommand)
                        or cmd.qualified_name in resolved_names
                        or cmd.name in resolved_names
                ):
                    continue

                resolved.append(cmd)
                resolved_names.add(cmd.name)

        if sort:
            return sorted(resolved, key=key)
        return resolved

    def get_command_signature(self, command: PartialCommand, cut: bool = False,
                              with_prefix: bool = False) -> str:  # noqa
        """Takes an :class:`.PartialCommand` and returns a POSIX-like signature useful for help command output.

        This is a modified version of the original get_command_signature.
        """
        is_app = isinstance(command, (app_commands.commands.Command, app_commands.commands.Group))

        prefix = ('/' if is_app else self.context.clean_prefix) if with_prefix else ''

        if is_app:
            if cut:
                return f'{prefix}{command.qualified_name}'

            if isinstance(command, app_commands.commands.Group):
                return f'{prefix}{command.qualified_name} <subcommand>'

            signature = ' '.join(
                f'<{option.name}>' if option.required else f'[{option.name}]' for option in command.parameters)
            return f'{prefix}{command.qualified_name} {signature}'

        signature = command.signature

        flags = self.get_command_flag_formatting(command)
        if flags:
            signature = re.sub(r'\s+', ' ', f'{signature} {flags}')

        parent = command.full_parent_name if command.parent else None
        alias = f'{parent} {command.name}' if parent else command.name

        if cut:
            return f'{prefix}{alias}'
        return f'{prefix}{alias} {signature}' + (" [!]" if getattr(command, 'hidden', None) else "")

    async def send_bot_help(self, mapping: Mapping[commands.Cog | None, list[PartialCommand]]):
        """|coro|

        Sends the help command for the whole bot.

        This is a modified version of the original send_bot_help.
        """

        def key(cmd: PartialCommand) -> str:
            try:
                if isinstance(cmd, app_commands.commands.Group):
                    return cmd.parent.qualified_name
                elif isinstance(cmd, app_commands.commands.Command):
                    return cmd.binding.qualified_name
                else:
                    return cmd.cog.qualified_name
            except AttributeError:
                # Escape if None but still not group to None
                return '\U0010ffff'

        entries: list[PartialCommand] = await self.filter_commands(
            self.all_commands, sort=True, key=lambda cmd: key(cmd)
        )

        grouped: dict[commands.Cog, list[PartialCommand]] = {}
        for name, children in itertools.groupby(entries, key=lambda cmd: key(cmd)):
            if name == '\U0010ffff':
                continue

            cog = self.context.bot.get_cog(name)
            if cog is None:
                continue

            grouped[cog] = list(children)

        await FrontHelpPaginator.start(self.context, entries=grouped, per_page=1)

    async def send_cog_help(self, cog: commands.Cog):
        """|coro|

        Sends the help command for a cog.

        This is a modified version of the original send_cog_help.
        """
        entries = await self.filter_commands(
            self.get_cog_commands(cog),
            sort=True
        )
        await GroupHelpPaginator.start(self.context, entries=entries, group=cog)

    @staticmethod
    def get_command_flag_formatting(command: PartialCommand, descripted: bool = False) -> str | list[dict]:  # noqa
        """Returns a string with the command flag formatting.

        Parameters
        ----------
        command: PartialCommand
            The command to get the flag formatting from.
        descripted: bool
            Whether to include the flag description or not.
        chunk: bool
            Whether to chunk the flags or not. Works only with descripted=True.

        Returns
        -------
        str
            The command flag formatting.
        """
        if isinstance(command, (app_commands.commands.Command, app_commands.commands.Group)):
            return [] if descripted else ""

        flags = command.clean_params.get('flags')
        resolved: list[str] = []

        if not flags:
            return [] if descripted else ""

        if descripted:
            for flag in flags.converter.get_flags().values():
                fmt = f'`--{flag.name}` - {flag.description}'
                resolved.append(fmt)

            chunked = ['\n'.join(resolved[i:i + 15]) for i in range(0, len(resolved), 15)]
            to_fields = []
            for i, chunk in enumerate(chunked):
                to_fields.append({'name': 'Flags' if i == 0 else '\u200b', 'value': chunk, 'inline': False})
            return to_fields
        else:
            for flag in flags.converter.get_flags().values():
                default = ""
                if flag.default is not None:
                    default = " " + (
                        f"{flag.default!r}" if (flag.annotation is str or Literal or Optional[str])
                        else str(flag.default)
                    )

                fmt = f'<--{flag.name}{default}>' if flag.required else f'[--{flag.name}{default}]'
                resolved.append(fmt)

            return ' '.join(resolved)

    @staticmethod
    def get_command_permission_formatting(command: PartialCommand, stringified: bool = False) -> str | dict:  # noqa
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
            return "" if stringified else {}

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
                string += f"{group.title()}: {', '.join(fmt(p) for p in perms)}\n"
            return string
        else:
            return resolved

    async def command_formatting(self, command: PartialCommand) -> discord.Embed:  # noqa
        """Returns an Embed with the command formatting.

        This is a modified version of the original command_formatting.
        """
        embed = discord.Embed(colour=helpers.Colour.darker_red())
        embed.set_author(name="Command Help", icon_url=COMMAND_ICON_URL)

        embed.description = (
            f"**```py\n{self.get_command_signature(command)}```**\n"
            f"{cleanup_docstring(command.description, getattr(command, 'help', None))}"
        )

        if getattr(command, 'aliases', None):
            embed.add_field(name='**Aliases**', value=f"`{' '.join(command.aliases)}`", inline=False)

        if isinstance(command, commands.hybrid.HybridGroup):
            embed.add_field(name='**Slash Command Fallback**', value='Commands can be used as a slash commands.',
                            inline=False)

        if isinstance(command, App):
            embed.add_field(name="**Slash Command**", value="Can only be used as a slash command.", inline=False)

        if getattr(command, 'commands', None):
            resolved_sub_commands = [
                f"* `{self.get_command_signature(cmd)}`" for cmd in command.commands
                if not await self.maybe_hidden(cmd, self.context.author)
            ]
            if resolved_sub_commands:
                subcommands = '\n'.join(resolved_sub_commands)
                embed.add_field(name='**Subcommands**', value=subcommands, inline=False)

        if permissions := self.get_command_permission_formatting(command, stringified=True):
            embed.add_field(name='**Required Permissions**', value=permissions, inline=False)

        if examples := command.extras.get('examples', None):
            text = '\n'.join(f'* `{self.get_command_signature(command, cut=True)} {example}`' for example in examples)
            embed.add_field(name='**Examples**', value=text, inline=False)

        for field in self.get_command_flag_formatting(command, descripted=True):
            embed.add_field(**field)

        return embed

    async def send_command_help(self, command: PartialCommand):  # noqa
        """|coro|

        Sends the help command for a command.

        This is a modified version of the original send_command_help.
        """
        # Checking for Application Commands or Groups because they can't be hidden
        if not isinstance(command, (app_commands.commands.Command, app_commands.commands.Group)):
            if command.hidden and not await self.context.bot.is_owner(self.context.author):
                return await self.context.send(self.command_not_found(command.name), silent=True)

        embed = await self.command_formatting(command)
        await self.context.send(embed=embed, silent=True)

    async def send_group_help(self, group: PartialCommandGroup):
        """|coro|

        Sends the help command for a group.

        This is a modified version of the original send_group_help.
        """
        # Only need to do this because send_command_help handles subcommands on its own
        await self.send_command_help(group)

    @classmethod
    def temporary(cls, context: Context | discord.Interaction) -> 'PaginatedHelpCommand':
        """Returns a temporary instance of the help command.

        Useful for helper functions that require a help command instance.
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
            "<:redTick:1079249771975413910> You are not the author of this interaction.", ephemeral=True)
        return False

    @discord.ui.button(label="Join Position", style=discord.ButtonStyle.blurple,
                       emoji=discord.PartialEmoji(name="join", id=1096930367522472037))
    async def join_position(self, interaction: discord.Interaction, button: discord.ui.Button):
        chunked_users = sorted(await interaction.guild.chunk(), key=lambda m: m.joined_at)

        def fmt(p, u, j):
            if u == interaction.user:
                return f"<a:arrow_right:1113018784651956334> `{p}.` **{u}** <a:arrow_left:1113018813244518451>"
            return f"`{p}.` **{u}** ({discord.utils.format_dt(j, style='f')})"

        source = TextSource(prefix=None, suffix=None, max_size=4000)
        author_index = chunked_users.index(self.user)

        for p, u in enumerate(chunked_users[author_index - 6 if author_index - 6 > 0 else 0:author_index],
                              start=author_index - 5 if author_index - 5 > 0 else 1):
            source.add_line(fmt(p, u, u.joined_at))

        for p, u in enumerate(chunked_users[author_index:author_index + 6], start=author_index + 1):
            source.add_line(fmt(p, u, u.joined_at))

        embed = discord.Embed(title=f"Join Position in {interaction.guild}", color=0x2b2d31)
        embed.add_field(name='Joined', value=f"{format_date(self.user.joined_at)}\n"
                                             f"╰ **Join Position:** {author_index + 1}",
                        inline=False)
        embed.add_field(name='Position', value=source.pages[0], inline=False)
        embed.set_author(name=self.user, icon_url=self.user.avatar.url)

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
            "<:redTick:1079249771975413910> You are not the author of this interaction.", ephemeral=True)
        return False

    @discord.ui.button(label="Join List", style=discord.ButtonStyle.blurple,
                       emoji=discord.PartialEmoji(name="join", id=1096930367522472037))
    async def join_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        chunked_users = sorted(await interaction.guild.chunk(), key=lambda m: m.joined_at)
        chunked = [[position, user, user.joined_at]
                   for position, user in enumerate(chunked_users, start=1)]

        def fmt(p, u, j):
            return f"`{p}.` **{u}** ({discord.utils.format_dt(j, style='f')})"

        source = TextSource(prefix=None, suffix=None, max_size=4000)
        for line in chunked:
            source.add_line(fmt(*line))

        class EmbedPaginator(BasePaginator[str]):

            async def format_page(self, entries: List[str], /) -> discord.Embed:
                embed = discord.Embed(title=f"Join List in {interaction.guild}", color=helpers.Colour.darker_red())
                embed.set_author(name=interaction.guild, icon_url=interaction.guild.icon.url)
                embed.set_footer(text=f"{plural(len(chunked_users)):entry|entries}")

                embed.description = '\n'.join(entries)

                return embed

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(view=self)
        await EmbedPaginator.start(interaction, entries=source.pages, per_page=1)
        self.stop()


class CustomMessage(NamedTuple):
    message: discord.Message
    before: Optional[discord.Message]
    after: Optional[discord.Message]
    timestamp: datetime


class Meta(commands.Cog):
    """Commands for utilities related to Discord or the Percy itself."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot
        self.process = psutil.Process()
        self.old_help_command: Optional[commands.HelpCommand] = bot.help_command
        bot.help_command = PaginatedHelpCommand()
        bot.help_command.cog = self

        self.snipe_del_chache: Dict[int, Dict[int, List[CustomMessage]]] = LRU(128)  # noqa
        self.snipe_edit_chache: Dict[int, Dict[int, List[CustomMessage]]] = LRU(128)  # noqa

        if not hasattr(self, '_help_autocomplete_cache'):
            self.bot.loop.create_task(self._fill_autocomplete())

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name="staff_animated", id=1076911514193231974, animated=True)

    def cog_unload(self):
        self.bot.help_command = self.old_help_command

    async def cog_command_error(self, ctx: Context, error: commands.CommandError):
        if isinstance(error, commands.BadArgument):
            await ctx.send(str(error))

    async def _fill_autocomplete(self) -> None:
        def key(command: PartialCommand) -> str:  # noqa
            cog = command.cog
            return cog.qualified_name if cog else '\U0010ffff'

        entries: list[PartialCommand] = await self.bot.help_command.filter_commands(
            self.bot.commands, sort=True, key=key)
        all_commands: dict[commands.Cog, list[PartialCommand]] = {}
        for name, children in itertools.groupby(entries, key=key):
            if name == '\U0010ffff':
                continue

            cog = self.bot.get_cog(name)
            assert cog is not None
            all_commands[cog] = sorted(children, key=lambda c: c.qualified_name)

        self._help_autocomplete_cache: Dict[commands.Cog, List[PartialCommand]] = all_commands

    @staticmethod
    async def mark_as_solved(thread: discord.Thread, user: discord.abc.User) -> None:
        tags: Sequence[discord.ForumTag] = thread.applied_tags

        if not any(tag.id == PH_SOLVED_TAG for tag in tags):
            tags.append(discord.Object(id=PH_SOLVED_TAG))  # type: ignore

        await thread.edit(
            locked=True,
            archived=True,
            applied_tags=tags[:5],
            reason=f'Marked as solved by {user} (ID: {user.id})',
        )

    @command(
        commands.hybrid_command,
        name='solved',
        description='Marks a thread as solved.',
    )
    @commands.guild_only()
    @commands.cooldown(1, 20, commands.BucketType.channel)
    @is_help_thread()
    async def solved(self, ctx: GuildContext):
        """Marks a thread as solved."""

        assert isinstance(ctx.channel, discord.Thread)

        if can_close_threads(ctx) and ctx.invoked_with == 'solved':
            await ctx.message.add_reaction(ctx.tick(True))
            await self.mark_as_solved(ctx.channel, ctx.user)
        else:
            msg = f"<@!{ctx.channel.owner_id}>, would you like to mark this thread as solved? This has been requested by {ctx.author.mention}."
            confirm = await ctx.prompt(msg, author_id=ctx.channel.owner_id, timeout=300.0)

            if ctx.channel.locked:
                return

            if confirm:
                await ctx.send(
                    f'{ctx.tick(True)} Marking as solved. Note that next time, you can mark the thread as solved yourself with `?solved`.'
                )
                await self.mark_as_solved(ctx.channel, ctx.channel.owner._user or ctx.user)
            elif confirm is None:
                await ctx.send(f'{ctx.tick(False)} Timed out waiting for a response. Not marking as solved.')
            else:
                await ctx.send(f'{ctx.tick(False)} Not marking as solved.')

    @command(commands.command, description='Shows parts of the Bots Source Command.')
    async def source(self, ctx: Context, *, command: str = None):
        """Displays my full source code or for a specific command.

        To display the source code of a subcommand you can separate it by
        periods, e.g. tag.create for the create subcommand of the tag command
        or by spaces.
        """
        source_url = 'https://github.com/klappstuhlpy/Percy'
        if command is None:
            return await ctx.send(source_url)

        if command == 'help':
            src = type(self.bot.help_command)
            filename = inspect.getsourcefile(src)
        else:
            obj = self.bot.remove_command(command)
            if obj is None:
                return await ctx.send(f'{ctx.tick(False)} Could not find command.')

            src = obj.callback.__code__
            filename = src.co_filename

        lines, firstlineno = inspect.getsourcelines(src)
        if filename is None:
            return await ctx.send('Could not find source for command.')

        location_parts = filename.split(os.path.sep)
        cogs_index = location_parts.index("cogs")
        location = os.path.sep.join(location_parts[cogs_index:])  # Join parts from "cogs" onwards

        final_url = f'<{source_url}/blob/master/{location}#L{firstlineno}-L{firstlineno + len(lines) - 1}>'

        resolved = self.bot.resolve_command(command)
        embed = discord.Embed(description=resolved.description, colour=helpers.Colour.darker_red())
        embed.set_author(name=f'Command: {command}', icon_url=INFO_ICON_URL)
        embed.add_field(name="Source Code", value=f"[Jump to GitHub]({final_url})")
        embed.set_footer(text=f"{location}:{firstlineno}")
        await ctx.send(embed=embed)

    @app_commands.command(name="help", description="Get help for a command or module.")
    @app_commands.guild_only()
    @app_commands.describe(module="Get help for a module.", command="Get help for a command")
    async def _help(
            self, interaction: discord.Interaction, module: Optional[str] = None, command: Optional[str] = None
    ):
        """Shows help for a command or module."""
        ctx: Context = await self.bot.get_context(interaction)
        await ctx.send_help(module or command)

    @_help.autocomplete('command')
    async def help_command_autocomplete(
            self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        if not hasattr(self, '_help_autocomplete_cache'):
            await interaction.response.autocomplete([])
            self.bot.loop.create_task(self._fill_autocomplete())

        module = interaction.namespace.module
        if module is not None:
            module_commands = self._help_autocomplete_cache.get(module, [])
            commands = [c for c in module_commands if c.qualified_name == module]
        else:
            commands = list(itertools.chain.from_iterable(self._help_autocomplete_cache.values()))

        results = fuzzy.finder(current, [c.qualified_name for c in commands])
        choices = [app_commands.Choice(name=res, value=res) for res in results[:25]]
        return choices

    @_help.autocomplete('module')
    async def help_cog_autocomplete(
            self, interaction: discord.Interaction, current: str,  # noqa
    ) -> list[app_commands.Choice[str]]:
        if not hasattr(self, '_help_autocomplete_cache'):
            self.bot.loop.create_task(self._fill_autocomplete())

        cogs = self._help_autocomplete_cache.keys()
        results = fuzzy.finder(current, [c.qualified_name for c in cogs])
        return [app_commands.Choice(name=res, value=res) for res in results][:25]

    @commands.hybrid_group(name="info", description="Shows info about a user or server.",
                           invoke_without_command=True)
    async def info(self, ctx: Context):
        """Shows info about a user or server."""
        await ctx.send_help(ctx.command)

    @info.command(name='features', description='Shows the features of a guild.')
    @commands.guild_only()
    async def info_features(self, ctx: Context, guild_id: str = None):
        """Shows the features of a guild."""

        if guild_id and not guild_id.isdigit():
            raise commands.BadArgument("<:redTick:1079249771975413910> Guild ID must be a number.")

        guild_id = guild_id or ctx.guild.id
        guild = self.bot.get_guild(guild_id)
        if not guild:
            raise commands.BadArgument(f'Guild with ID `{guild_id}` not found.')

        features = list(
            map(lambda e: f"**{e[0]}** - {e[1]}", list(self.bot.get_guild_features(guild.features, only_current=True))))
        embed = discord.Embed(title="Guild Features",
                              timestamp=discord.utils.utcnow(),
                              color=self.bot.colour.darker_red())
        embed.set_footer(text=f"{plural(len(features)):feature|features}")
        await LinePaginator.start(ctx, entries=features, per_page=12, embed=embed, location='description')

    @info.command(name="user", description="Shows info about a user.")
    @commands.guild_only()
    @app_commands.describe(user_id="The user ID to show info about. (Default: You)")
    async def info_user(self, ctx: Context, user_id: str = None):
        """Shows info about a user."""

        if ctx.interaction:
            await ctx.defer()

        if user_id and not user_id.isdigit():
            raise commands.BadArgument("<:redTick:1079249771975413910> User ID must be a number.")

        user_id = user_id or ctx.author.id
        user = await self.bot.get_or_fetch_member(ctx.guild, user_id)

        if not user:
            raise commands.BadArgument("<:redTick:1079249771975413910> User not found.")

        e = discord.Embed()
        roles = [role.name.replace('@', '@\u200b') for role in getattr(user, 'roles', [])]
        e.set_author(name=str(user))

        e.add_field(name='ID', value=user.id, inline=False)
        e.add_field(name='Created', value=format_date(user.created_at), inline=False)

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
            e.add_field(name='Boosted', value=format_date(user.premium_since), inline=False)
            badges.append('<:booster:1088921589145415751>')  # Emoji Server

        if badges:
            e.description = ''.join(badges)

        activities = getattr(user, 'activities', None)
        if activities is None:
            activities = []

        spotify = next((act for act in activities if isinstance(act, discord.Spotify)), None)

        e.add_field(
            name=f"Spotify",
            value=(
                f"**[{spotify.title}]({spotify.track_url})**"
                f"\n__By:__ {spotify.artist}"
                f"\n__On Album:__ {spotify.album}"
                f"\n`{datetime.timedelta(seconds=round((ctx.message.created_at - spotify.start).total_seconds()))}`/"
                f"`{datetime.timedelta(seconds=round(spotify.duration.total_seconds()))}`\n"
                if spotify
                else '*Not listening to anything...*'
            )
        )

        custom_activity = next((act for act in activities if isinstance(act, discord.CustomActivity)), None)
        activity_string = (
            f"`{discord.utils.remove_markdown(custom_activity.name)}`"
            if custom_activity and custom_activity.name
            else '*User has no custom status.*'
        )
        e.add_field(
            name=f'Custom status',
            value=f"\n{activity_string}",
            inline=False
        )

        voice = getattr(user, 'voice', None)
        if voice is not None:
            vc = voice.channel
            other_people = len(vc.members) - 1
            voice = f'`{vc.name}` with {other_people} others' if other_people else f'`{vc.name}` by themselves'
            e.add_field(name='Voice', value=voice, inline=False)

        if roles:
            e.add_field(name='Roles', value=', '.join(roles) if len(roles) < 10 else f'{len(roles)} roles',
                        inline=False)

        remaining_flags = (set_flags - subset_flags) & misc_flags_descriptions.keys()
        if remaining_flags:
            e.add_field(
                name='Public Flags',
                value='\n'.join(misc_flags_descriptions[flag] for flag in remaining_flags),
                inline=False,
            )

        colour = user.colour
        if colour.value:
            e.colour = colour

        e.set_thumbnail(url=user.display_avatar.url)

        member = user

        user = await self.bot.fetch_user(user.id)
        if user.banner:
            e.set_image(url=user.banner.url)

        e.set_footer(text=f'Requested by: {ctx.author}')

        await ctx.send(embed=e, view=UserJoinView(member, ctx.author))

    @info.command(name="server", description="Shows info about a server.")
    @commands.guild_only()
    @app_commands.describe(guild_id="The ID of the server to show info about. (Default: Current server)")
    async def info_server(self, ctx: Context, guild_id: str = None):
        """Shows info about the current or a specified server."""

        if not guild_id or (guild_id and not await self.bot.is_owner(ctx.author)):
            if not ctx.guild:
                raise commands.BadArgument("<:redTick:1079249771975413910> You must specify a guild ID.")
            guild = ctx.guild
        else:
            if not guild_id.isdigit():
                raise commands.BadArgument("<:redTick:1079249771975413910> Guild ID must be a number.")
            guild = self.bot.get_guild(int(guild_id))

        if not guild:
            raise commands.BadArgument("<:redTick:1079249771975413910> Guild not found.")

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

        e = discord.Embed(title=guild.name, description=f'**ID**: {guild.id}\n**Owner**: {guild.owner}')
        if guild.icon:
            e.set_thumbnail(url=guild.icon.url)

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

        info = []
        features = set(guild.features)
        all_features = {
            'PARTNERED': 'Partnered',
            'VERIFIED': 'Verified',
            'DISCOVERABLE': 'Server Discovery',
            'COMMUNITY': 'Community Server',
            'FEATURABLE': 'Featured',
            'WELCOME_SCREEN_ENABLED': 'Welcome Screen',
            'INVITE_SPLASH': 'Invite Splash',
            'VIP_REGIONS': 'VIP Voice Servers',
            'VANITY_URL': 'Vanity Invite',
            'COMMERCE': 'Commerce',
            'LURKABLE': 'Lurkable',
            'NEWS': 'News Channels',
            'ANIMATED_ICON': 'Animated Icon',
            'BANNER': 'Banner',
        }

        for feature, label in all_features.items():
            if feature in features:
                info.append(f'{ctx.tick(True)}: {label}')

        if info:
            e.add_field(name='Features', value='\n'.join(info))

        e.add_field(name='Channels', value='\n'.join(channel_info))

        if guild.premium_tier != 0:
            boosts = f'Level {guild.premium_tier}\n{guild.premium_subscription_count} boosts'
            last_boost = max(guild.members, key=lambda m: m.premium_since or guild.created_at)
            if last_boost.premium_since is not None:
                boosts = f'{boosts}\nLast Boost: {last_boost} ({discord.utils.format_dt(last_boost.premium_since, style="R")})'
            e.add_field(name='Boosts', value=boosts, inline=False)

        bots = sum(m.bot for m in guild.members)
        fmt = f'Total: {guild.member_count} ({plural(bots):bot} `{bots / guild.member_count:.2%}`)'

        e.add_field(name='Members', value=fmt, inline=False)
        e.add_field(name='Roles', value=', '.join(roles) if len(roles) < 10 else f'{len(roles)} roles')

        emoji_stats = Counter()
        for emoji in guild.emojis:
            if emoji.animated:
                emoji_stats['animated'] += 1
                emoji_stats['animated_disabled'] += not emoji.available
            else:
                emoji_stats['regular'] += 1
                emoji_stats['disabled'] += not emoji.available

        fmt = (
            f'Regular: {emoji_stats["regular"]}/{guild.emoji_limit}\n'
            f'Animated: {emoji_stats["animated"]}/{guild.emoji_limit}\n'
        )
        if emoji_stats['disabled'] or emoji_stats['animated_disabled']:
            fmt = f'{fmt}Disabled: {emoji_stats["disabled"]} regular, {emoji_stats["animated_disabled"]} animated\n'

        fmt = f'{fmt}Total Emoji: {len(guild.emojis)}/{guild.emoji_limit * 2}'
        e.add_field(name='Emoji', value=fmt, inline=False)

        if guild.banner:
            e.set_image(url=guild.banner.url)

        e.set_footer(text='Created').timestamp = guild.created_at
        await ctx.send(embed=e, view=GuildUserJoinView(ctx.author))

    @command()
    async def avatar(self, ctx: Context, *, user: Union[discord.Member, discord.User] = None):
        """Shows a user's enlarged avatar (if possible)."""
        user = user or ctx.author
        avatar = user.display_avatar.with_static_format('png')
        embed = discord.Embed(colour=discord.Colour.from_rgb(
            *self.bot.get_cog('Emoji').render.get_dominant_color(io.BytesIO(await avatar.read()))))  # type: ignore
        embed.set_author(name=str(user), url=avatar)
        embed.set_image(url=avatar)
        await ctx.send(embed=embed)

    @command(
        commands.hybrid_command,
        name='charinfo',
        description='Shows you information about a number of characters.',
    )
    @app_commands.describe(characters="A String of characters that should be introspected.")
    async def charinfo(self, ctx: Context, *, characters: str):
        """Shows you information on up to 50 unicode characters."""
        match = re.match(r"<(a?):(\w+):(\d+)>", characters)
        if match:
            await ctx.send(f"{ctx.tick(False)} Cannot introspect custom emojis.")
            return

        if len(characters) > 50:
            await ctx.send(f"{ctx.tick(False)} Character limit of `50` exceeded.")
            return

        def char_info(char: str) -> tuple[str, str]:
            digit = f"{ord(char):x}"
            if len(digit) <= 4:
                u_code = f"\\u{digit:>04}"
            else:
                u_code = f"\\U{digit:>08}"
            url = f"https://www.compart.com/en/unicode/U+{digit:>04}"
            name = f"[{unicodedata.name(char, '')}]({url})"
            info = f"`{u_code.ljust(10)}`: {name} - {discord.utils.escape_markdown(char)}"
            return info, u_code

        char_list, raw_list = zip(*(char_info(c) for c in characters), strict=True)
        embed = discord.Embed(title="Char Info", colour=self.bot.colour.darker_red())

        if len(characters) > 1:
            embed.add_field(name="Full Text", value=f"`{''.join(raw_list)}`", inline=False)

        await LinePaginator.start(ctx, entries=char_list, per_page=10, embed=embed, location="description")

    @command(
        commands.group,
        name='prefix',
        description='Manages the server\'s custom prefixes.',
        invoke_without_command=True,
    )
    async def prefix(self, ctx: Context):
        """Manages the server's custom prefixes.
        If called without a subcommand, this will list the currently set
        prefixes.
        """

        prefixes = self.bot.get_guild_prefixes(ctx.guild)
        del prefixes[1]

        e = discord.Embed(title='Prefix List', colour=self.bot.colour.darker_red())
        e.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url)
        e.set_thumbnail(url=self.bot.user.avatar.url)
        e.set_footer(text=f'{len(prefixes)} prefixes')
        e.description = '\n'.join(f'`{index}.` {elem}' for index, elem in enumerate(prefixes, 1))
        await ctx.send(embed=e)

    @command(
        prefix.command,
        name='add',
        description='Appends a prefix to the list of custom prefixes.',
        ignore_extra=False,
    )
    @command_permissions(user=["manage_guild"])
    async def prefix_add(self, ctx: GuildContext, prefix: Annotated[str, Prefix]):
        """Appends a prefix to the list of custom prefixes.
        Previously set prefixes are not overridden.
        To have a word prefix, you should quote it and end it with
        a space, e.g. "hello " to set the prefix to "hello ". This
        is because Discord removes spaces when sending messages so
        the spaces are not preserved.
        Multi-word prefixes must be quoted also.
        You must have Manage Server permission to use this command.
        """

        current_prefixes = self.bot.get_raw_guild_prefixes(ctx.guild.id)
        current_prefixes.append(prefix)
        try:
            await self.bot.set_guild_prefixes(ctx.guild, current_prefixes)
        except Exception as e:
            await ctx.send(f'{ctx.tick(False)} {e}')
        else:
            await ctx.send(ctx.tick(True) + ' Prefix added.')

    @prefix_add.error
    async def prefix_add_error(self, ctx: GuildContext, error: commands.CommandError):
        if isinstance(error, commands.TooManyArguments):
            await ctx.send("You've given too many prefixes. Either quote it or only do it one by one.")

    @command(
        prefix.command,
        name='remove',
        aliases=['delete'],
        ignore_extra=False
    )
    @command_permissions(user=["manage_guild"])
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
            return await ctx.send('I do not have this prefix registered.')

        try:
            await self.bot.set_guild_prefixes(ctx.guild, current_prefixes)
        except Exception as e:
            await ctx.send(f'{ctx.tick(False)} {e}')
        else:
            await ctx.send(ctx.tick(True) + ' Prefix removed.')

    @command(
        prefix.command,
        name='reset',
        description='Removes all custom prefixes.',
        ignore_extra=False
    )
    @command_permissions(user=["manage_guild"])
    async def prefix_reset(self, ctx: GuildContext):
        """Removes all custom prefixes.
        After this, the bot will listen to only mention prefixes.
        You must have Manage Server permission to use this command.
        """

        await self.bot.set_guild_prefixes(ctx.guild, [])
        await ctx.send(ctx.tick(True) + ' Cleared all prefixes.')

    @command(
        commands.hybrid_command,
        name='ping',
        description='Shows some Client and API latency information.',
    )
    async def ping(self, ctx: Context):
        """Shows some Client and API latency information."""

        message = None

        def build_embed(content: str) -> discord.Embed:
            return discord.Embed(
                title="Pong!",
                colour=helpers.Colour.darker_red(),
                description=content
            )

        api_readings: List[float] = []
        websocket_readings: List[float] = []

        for _ in range(6):
            text = "*Calculating round-trip time...*\n\n"
            text += "\n".join(
                f"Reading `{index + 1}`: `{reading * 1000:.2f}ms`" for index, reading in enumerate(api_readings))

            if api_readings:
                average, stddev = mean_stddev(api_readings)

                text += f"\n\n**Average:** `{average * 1000:.2f}ms` \N{PLUS-MINUS SIGN} `{stddev * 1000:.2f}ms`"
            else:
                text += "\n\n*No readings yet.*"

            if websocket_readings:
                average = sum(websocket_readings) / len(websocket_readings)

                text += f"\n**Websocket latency:** `{average * 1000:.2f}ms`"
            else:
                text += f"\n**Websocket latency:** `{self.bot.latency * 1000:.2f}ms`"

            if _ == 5:
                gateway_url = await self.bot.http.get_gateway()
                start = time.monotonic()
                async with self.bot.session.get(f'{gateway_url}/ping'):
                    end = time.monotonic()
                    gateway_ping = (end - start) * 1000

                text += f"\n**Gateway latency:** `{gateway_ping:.2f}ms`"

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

    async def say_permissions(
            self, ctx: Context, member: discord.Member, channel: Union[discord.abc.GuildChannel, discord.Thread]
    ):
        permissions = channel.permissions_for(member)
        e = discord.Embed(colour=member.colour)
        avatar = member.display_avatar.with_static_format('png')
        e.set_author(name=str(member), url=avatar)
        allowed, denied = [], []
        for name, value in permissions:
            name = name.replace('_', ' ').replace('guild', 'server').title()
            if value:
                allowed.append(name)
            else:
                denied.append(name)

        e.add_field(name='Allowed', value='\n'.join(allowed))
        e.add_field(name='Denied', value='\n'.join(denied))
        await ctx.send(embed=e)

    @command(
        commands.hybrid_group,
        name='permissions',
        description='Shows permissions for a member or the bot in a specific channel.',
    )
    @commands.guild_only()
    async def permissions(self, ctx: Context):
        """Shows permissions for a member or the bot in a specific channel."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @command(
        permissions.command,
        name='user',
        description='Shows a member\'s permissions in a specific channel.',
    )
    @commands.guild_only()
    async def user(
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

    @command(
        permissions.command,
        name='bot',
        description='Shows the bot\'s permissions in a specific channel.',
    )
    @commands.guild_only()
    async def bot(self, ctx: GuildContext, *, channel: Union[discord.abc.GuildChannel, discord.Thread] = None):
        """Shows the bots permissions in a specific channel.
        If no channel is given then it uses the current one.
        This is a good way of checking if the bot has the permissions needed
        to execute the commands it wants to execute.
        """

        channel = channel or ctx.channel
        member = ctx.guild.me
        await self.say_permissions(ctx, member, channel)

    @command(
        commands.command,
        name='debug',
        description='Shows permission resolution for a channel and an optional author.',
    )
    @commands.is_owner()
    async def debug(self, ctx: Context, guild_id: int, channel_id: int, author_id: int = None):
        """Shows permission resolution for a channel and an optional author."""

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return await ctx.send('Guild not found?')

        channel = guild.get_channel(channel_id)
        if channel is None:
            return await ctx.send('Channel not found?')

        if author_id is None:
            member = guild.me
        else:
            member = await self.bot.get_or_fetch_member(guild, author_id)

        if member is None:
            return await ctx.send('Member not found?')

        await self.say_permissions(ctx, member, channel)

    @command(
        commands.hybrid_command,
        name='snipe',
        description='Snipes a deleted message.',
    )
    @commands.guild_only()
    async def snipe(self, ctx: GuildContext, channel: discord.TextChannel = None):
        """Snipes a deleted message.
        If no channel is given, then it uses the current one.
        """

        channel = channel or ctx.channel
        try:
            obj = sorted(self.snipe_del_chache[ctx.guild.id][channel.id],
                         key=lambda x: x.timestamp, reverse=True)[0]
        except KeyError:
            return await ctx.send('I have not sniped any messages in this channel.')

        embed = discord.Embed(description=obj.message.clean_content, color=self.bot.colour.darker_red(),
                              timestamp=obj.timestamp)
        embed.set_author(name=obj.message.author, icon_url=obj.message.author.display_avatar.url)
        embed.add_field(name="Message", value=obj.message.jump_url)
        embed.set_footer(text="Deleted at")
        await ctx.send(embed=embed)

    @command(
        commands.hybrid_command,
        name='esnipe',
        description='Snipes a deleted edited.',
    )
    @commands.guild_only()
    async def esnipe(self, ctx: GuildContext, channel: discord.TextChannel = None):
        """Snipes a deleted edited.
        If no channel is given, then it uses the current one.
        """

        channel = channel or ctx.channel
        try:
            obj = sorted(self.snipe_edit_chache[ctx.guild.id][channel.id],
                         key=lambda x: x.timestamp, reverse=True)[0]
        except KeyError:
            return await ctx.send('I have not sniped any messages in this channel.')

        embed = discord.Embed(color=self.bot.colour.darker_red(), timestamp=obj.timestamp)
        embed.set_author(name=obj.message.author, icon_url=obj.message.author.display_avatar.url)
        embed.add_field(name="Message", value=obj.message.jump_url)
        embed.add_field(name="Before", value=obj.before.clean_content, inline=False)
        embed.add_field(name="After", value=obj.after.clean_content, inline=False)
        embed.set_footer(text="Edited at")
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.guild is None:
            return

        if message.guild.id not in self.snipe_del_chache:
            self.snipe_del_chache[message.guild.id] = {}

        if message.channel.id not in self.snipe_del_chache[message.guild.id]:
            self.snipe_del_chache[message.guild.id][message.channel.id] = []

        obj = CustomMessage(message=message, timestamp=discord.utils.utcnow(), after=None, before=None)

        self.snipe_del_chache[message.guild.id][message.channel.id].append(obj)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.guild is None:
            return

        if before.guild.id not in self.snipe_edit_chache:
            self.snipe_edit_chache[before.guild.id] = {}

        if before.channel.id not in self.snipe_edit_chache[before.guild.id]:
            self.snipe_edit_chache[before.guild.id][before.channel.id] = []

        obj = CustomMessage(message=after, timestamp=discord.utils.utcnow(), after=after, before=before)

        self.snipe_edit_chache[before.guild.id][before.channel.id].append(obj)


async def setup(bot: Percy):
    await bot.add_cog(Meta(bot))
