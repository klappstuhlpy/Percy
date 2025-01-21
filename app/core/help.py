from __future__ import annotations

import inspect
from typing import Type, Any, Callable, Mapping, TYPE_CHECKING

import discord
from discord.ext import commands

from app.utils import helpers, pluralize, truncate, get_asset_url, AnsiStringBuilder, AnsiColor, humanize_duration
from app.core.flags import FlagMeta
from app.utils.pagination import BasePaginator
from app.core.models import Command, HybridCommand, EmbedBuilder
from config import Emojis

if TYPE_CHECKING:
    from app.core import Context, Bot, Cog

AnyGroup = commands.Group | commands.HybridGroup
AnyCommand = commands.Command | commands.HybridCommand | AnyGroup

COMMAND_ICON_URL = 'https://klappstuhl.me/gallery/xGPqHsSgWE.png'
INFO_ICON_URL = 'https://klappstuhl.me/gallery/YDdbsAuttR.png'


class HelpPaginator(BasePaginator[AnyCommand]):

    @staticmethod
    def create_prefixes(cmd):
        prefixes = []
        if getattr(cmd, 'is_locked', False):
            prefixes.append(Emojis.Command.locked)
        if getattr(cmd, 'has_more_help', False):
            prefixes.append(Emojis.Command.more_info)
        return prefixes

    def create_text(self, is_any_locked: bool, any_has_more_help: bool) -> str:
        if is_any_locked:
            yield f'{Emojis.Command.locked} » This command expects certain permissions from the user to be run.'
        if any_has_more_help:
            prefix = self.extras.get('origin').clean_prefix
            yield f'{Emojis.Command.more_info} » This command has more detailed help available with `{prefix}help <command>`.'

    async def format_page(self, entries: list[AnyCommand]) -> discord.Embed:
        helper = PaginatedHelpCommand.temporary(self.extras.get('origin'))

        if self.current_page == 1 and isinstance(self.entries, dict):
            return await helper.get_front_page_embed()

        if not (group := self.extras.get('group')):
            raise commands.BadArgument('The group attribute is missing.')

        emoji = getattr(group, 'emoji', '')
        embed = discord.Embed(
            title=f'{emoji} {group.qualified_name}',
            description=group.description,
            colour=helpers.Colour.white()
        )

        is_any_locked = any(getattr(cmd, 'is_locked', False) for cmd in entries)
        any_has_more_help = any(getattr(cmd, 'has_more_help', False) for cmd in entries)

        for cmd in entries:
            prefixes = self.create_prefixes(cmd)
            prefix = (' '.join(prefixes) + ' | ') if prefixes else ''
            signature = helper.get_command_signature(cmd)
            embed.add_field(name=f'{prefix}**`{signature}`**', value=cmd.description or '…', inline=False)

        text = list(self.create_text(is_any_locked, any_has_more_help))
        if text:
            embed.add_field(
                name='\u200b',
                value='\n'.join(text),
                inline=False
            )

        embed.set_author(name=f'{pluralize(len(self.entries)):command}', icon_url=COMMAND_ICON_URL)
        embed.set_footer(
            text=f'{self.ctx.user} | Use the components below for navigation. '
                 f'This menu shows only the available commands for this Group.'
        )
        return embed

    @classmethod
    async def start(
            cls: Type[HelpPaginator],
            context: Context | discord.Interaction,
            /,
            *,
            entries: list[AnyCommand] | dict[Cog, list[AnyCommand]],
            per_page: int = 6,
            clamp_pages: bool = True,
            timeout: int = 180,
            search_for: bool = False,
            ephemeral: bool = False,
            **kwargs: Any,
    ) -> HelpPaginator[AnyCommand]:
        """Overwritten to add the view to the message and edit message, not send new."""
        self = cls(entries=entries, per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)
        self.ctx = context

        edit = kwargs.pop('edit', False)
        self.extras.update(kwargs)

        if len(self.pages) == 1:
            self.clear_items()

        def prepare_select(items: dict[Cog, list[AnyCommand]] | list[AnyCommand]):
            return CategorySelect(context.client, mapping=items, with_index=self.extras.get('with_index', True))

        if isinstance(entries, dict):
            self.extras['groups'] = entries
            self.add_item(prepare_select(entries))
        elif isinstance(entries, list):
            if (groups := self.extras.get('groups')) is not None:
                self.add_item(prepare_select(groups))
        else:
            raise commands.BadArgument('The entries attribute is missing.')

        page: discord.Embed = await self.format_page(self.pages[0])
        self.update_buttons()

        view = self
        if self.total_pages <= 1 and not self.current_page == 1 and not edit:
            view = None

        await (self._edit if edit else self._send)(ctx=context, embed=page, view=view, ephemeral=ephemeral)
        return self


class CategorySelect(discord.ui.Select[HelpPaginator]):
    """A select menu for the HelpPaginator to navigate through categories."""

    def __init__(
            self,
            bot: Bot,
            mapping: dict[Cog, list[AnyCommand]],
            *,
            with_index: bool = True,
            default: Cog | None = None
    ):
        super().__init__(placeholder='Select a category to view...')
        self.bot: Bot = bot

        self.with_index: bool = with_index
        self.default: Cog | None = default

        self.mapping: dict[Cog, list[AnyCommand]] = mapping
        self.cog_mapping: dict[str, Cog] = {cog.qualified_name: cog for cog in mapping}

        self.__fill_options()

    def __fill_options(self) -> None:
        if self.with_index:
            self.add_option(
                label='Start Page',
                emoji=Emojis.Arrows.left,
                value='__index',
                description='The front page of the Help Menu.',
            )

        for cog in self.mapping:
            emoji = getattr(cog, 'emoji', None)
            self.add_option(
                label=cog.qualified_name,
                value=cog.qualified_name,
                description=truncate(cog.description, 50),
                emoji=emoji,
                default=cog is self.default
            )

    async def callback(self, interaction: discord.Interaction):
        assert self.view is not None
        await interaction.response.defer()

        value = self.values[0]
        if value == '__index':
            await HelpPaginator.start(interaction, entries=self.mapping, edit=True, **self.view.extras)
        else:
            try:
                cog = self.cog_mapping[value]
            except KeyError:
                return await interaction.response.send_message(
                    f'{Emojis.error} Somehow this category does not exist?', ephemeral=True)

            _commands = self.mapping[cog]
            if not _commands:
                return await interaction.response.send_message(
                    f'{Emojis.error} This category has no commands for you.', ephemeral=True)

            extras = self.view.extras
            extras.pop('group', None)
            await HelpPaginator.start(interaction, entries=_commands, edit=True, group=cog, **self.view.extras)


class PaginatedHelpCommand(commands.HelpCommand):
    """A subclass of the default help command that implements support for Application/Hybrid Commands."""

    context: Context

    def __init__(self, **kwargs: Any):
        super().__init__(
            show_hidden=False,
            verify_checks=False,
            command_attrs={
                'cooldown': commands.CooldownMapping.from_cooldown(1, 3.0, commands.BucketType.member),
                'hidden': True,
                'aliases': ['h'],
                'description': 'Get help for a module or a command.'
            },
            **kwargs
        )

    def get_bot_mapping(self) -> dict[Cog, list[Command]]:
        mapping = super().get_bot_mapping()
        del mapping[None]
        return mapping

    async def total_commands_invoked(self) -> int:
        """Returns the total amount of commands invoked."""
        query = "SELECT COUNT(*) as total FROM commands;"
        return await self.context.db.fetchval(query)  # type: ignore

    @staticmethod
    def command_requires_permissions(command: AnyCommand):
        """Returns whether a command is locked or not."""
        if not isinstance(command, (Command, HybridCommand)):
            return False

        spec = command.permissions
        return bool(spec.user)

    def _get_all_subcommands(self, command: AnyCommand | AnyGroup, names: set[str]) -> set[AnyCommand]:
        """Returns all subcommands of a command."""
        subcommands: set[AnyCommand] = set()

        def add_subcommand(cmd: AnyCommand):
            nonlocal subcommands, names
            if not cmd.hidden and self.is_available(cmd) and cmd.qualified_name not in names:
                setattr(cmd, 'is_locked', self.command_requires_permissions(cmd))

                subcommands.add(cmd)
                names.add(cmd.qualified_name)

        if isinstance(command, AnyGroup):
            # If the group is not just a placeholder, add it to the list
            if getattr(command, 'fallback', None) is not None:
                add_subcommand(command)

            for subcommand in command.walk_commands():
                add_subcommand(subcommand)
        else:
            add_subcommand(command)

        return subcommands

    def is_available(self, command: AnyCommand) -> bool:
        """Returns whether a command is available or not by looking for guild_id restrictions."""
        guild_ids = getattr(command.callback, '__guild_ids__', None)
        if not guild_ids:
            return True
        if self.context.guild and self.context.guild.id in guild_ids:
            return True
        return False

    async def filter_commands(
            self,
            commands: Iterable[AnyCommand],  # noqa
            /,
            *,
            sort: bool = False,
            key: Callable[[AnyCommand], Any] | None = None
    ) -> list[AnyCommand]:
        """|coro|

        This is a Helper Function to filter the bots Application Commands, Hybrid Commands and Core Commands.

        Parameters
        ------------
        commands: Iterable[AnyCommand]
            An iterable of commands that are getting filtered.
        sort: :class:`bool`
            Whether to sort the result.
        key: Callable[[`AnyCommand`], Any]
            An optional key function to pass to :func:`py:sorted` that
            takes a :class:`Command` as its sole parameter. If ``sort`` is
            passed as ``True`` then this will default as the command name.

        Returns
        -------
        list[`AnyCommand`]
            The filtered Commands.
        """
        if sort and key is None:
            key = lambda c: c.name  # noqa

        iterator = commands if self.show_hidden else filter(lambda c: not c.hidden, commands)

        if getattr(self.context, 'guild', None) is None:
            iterator = filter(lambda c: not getattr(c, 'guild_only', False), iterator)

        ret: list[AnyCommand] = []
        names: set[str] = set()
        for command in iterator:
            ret.extend(self._get_all_subcommands(command, names))

        if sort:
            ret.sort(key=key)
        return ret

    async def command_callback(self, ctx: Context, /, *, command: str | None = None) -> None:
        if command is not None and command.lower() == 'flags':
            await ctx.send(embed=self.get_flag_help_embed(), silent=True)
        else:
            await super().command_callback(ctx, command=command)

    # noinspection PyProtectedMember
    @staticmethod
    def get_command_flag_signature(
            command: AnyCommand,
            *,
            descripted: bool = False,
    ) -> list[dict[str, str | bool]] | str:
        """Takes an :class:`Command` and returns a POSIX-like signature useful for help command output.

        This is a modified version of the original get_command_signature.

        Parameters
        ----------
        command: :class:`Command`
            The command to get the signature for.
        descripted: :class:`bool`
            Whether to return the commands as formatted embed fields with description.

        Returns
        -------
        :class:`str`
            The command signature.
        """
        flags: FlagMeta = getattr(command, 'custom_flags', None)

        if not flags:
            return [] if descripted else ''

        resolved: list[str] = []

        if descripted:
            for flag in flags.walk_flags():
                fmt = f'- `--{flag.name}`: {flag.description}'
                resolved.append(fmt)

            chunked = list(discord.utils.as_chunks(resolved, 15))
            to_fields = []
            for i, chunk in enumerate(chunked):
                to_fields.append({'name': 'Flags' if i == 0 else '\u200b', 'value': '\n'.join(chunk), 'inline': False})
            return to_fields
        else:
            for flag in flags.walk_flags():
                if flag.required:
                    start, end = '<>'
                else:
                    start, end = '[]'

                resolved.append(start + f'--{flag.name}' + end)

            return ' '.join(resolved)

    def get_command_signature(
            self,
            command: AnyCommand,
            *,
            descripted: bool = False,
            no_signature: bool = False,
    ) -> list[dict[str, str | bool]] | str:
        """Takes an :class:`Command` and returns a POSIX-like signature useful for help command output.

        This is a modified version of the original get_command_signature.

        Parameters
        ----------
        command: :class:`Command`
            The command to get the signature for.
        descripted: :class:`bool`
            Whether to return the commands as formatted embed fields with description.
        no_signature: :class:`bool`
            Whether to return only the command name without signature.

        Returns
        -------
        :class:`str`
            The command signature.
        """
        if descripted:
            params = command.clean_params
            resolved: list[str] = []

            for param in params.values():
                if isinstance(param.annotation, FlagMeta) and getattr(command, 'custom_flags', None):
                    continue

                # resolve arg description through app_commands.describe
                # decorators or fallback to the default description if present
                description = getattr(command.callback, '__discord_app_commands_param_description__', param.description)
                if isinstance(description, dict):
                    description = description.get(param.name, 'Argument undocumented.')

                fmt = f'- `{param.name}`: {description}'
                resolved.append(fmt)

            chunked = list(discord.utils.as_chunks(resolved, 15))
            to_fields = []
            for i, chunk in enumerate(chunked):
                to_fields.append({'name': 'Arguments' if i == 0 else '\u200b', 'value': '\n'.join(chunk), 'inline': False})
            return to_fields

        prefix = self.context.clean_prefix

        if no_signature:
            return f'{prefix}{command.qualified_name}'

        signature = command.ansi_signature.raw if isinstance(command, AnyCommand) else command.signature  # type: ignore

        hidden_tag = '[!]' if command.hidden else ''
        return f'{prefix}{command.qualified_name} {truncate(signature, 150)} {hidden_tag}'.strip()

    async def send_bot_help(self, mapping: Mapping[Cog | None, list[AnyCommand]]) -> None:
        """|coro|

        Sends the help command for the whole bot.
        This is a modified version of the original send_bot_help.

        Parameters
        ----------
        mapping: Mapping[:class:`.Cog`, list[:class:`.commands.Command`]]
            The mapping of the commands.
        """
        def key(cmd: AnyCommand) -> str:
            return cmd.cog.qualified_name if cmd.cog else 'No Category'

        entries = await self.filter_commands(self.context.bot.commands, sort=True, key=key)

        grouped: dict[Cog, list[AnyCommand]] = {}
        for command in entries:
            cog: Cog = self.context.bot.get_cog(key(command))
            if getattr(cog, '__hidden__', False):
                continue

            if cog and not command.hidden:
                grouped.setdefault(cog, []).append(command)

        grouped = {cog: cmds for cog, cmds in sorted(grouped.items(), key=lambda x: x[0].qualified_name)}
        await HelpPaginator.start(self.context, entries=grouped, per_page=1, origin=self.context)

    async def send_cog_help(self, cog: Cog):
        """|coro|

        Sends the help command for a cog.
        This is a modified version of the original send_cog_help.

        Parameters
        ----------
        cog: :class:`.Cog`
            The cog to send the help for.
        """
        entries = await self.filter_commands(cog.walk_commands(), sort=True)
        if not entries:
            return await self.context.send(self.command_not_found(cog.qualified_name), silent=True)

        await HelpPaginator.start(self.context, entries=entries, group=cog, with_index=False, origin=self.context)

    async def send_command_help(self, command: AnyCommand):
        """|coro|

        Sends the help command for a command.
        This is a modified version of the original send_command_help.

        Parameters
        ----------
        command: :class:`.commands.Command`
            The command to send the help for.
        """
        if command.hidden or not self.is_available(command):
            return await self.context.send(self.command_not_found(command.name), silent=True)

        embed = await self.command_formatting(command)
        await self.context.send(embed=embed, silent=True)

    async def send_group_help(self, group: AnyGroup):
        """|coro|

        Sends the help command for a group.
        This is a modified version of the original send_group_help.

        Parameters
        ----------
        group: :class:`.commands.PartialCommandGroup`
            The group to send the help for.
        """
        await self.send_command_help(group)

    async def get_front_page_embed(self) -> discord.Embed:
        """|coro|

        Returns the front page of the help command.

        Returns
        -------
        :class:`discord.Embed`
            The front page of the help command.
        """
        ctx = self.context
        prefix = ctx.clean_prefix

        embed = discord.Embed(
            title=f'{ctx.bot.user.name} Help',
            description='**```\nPlease use the Select Menu below to explore the corresponding category.```**'
                        '\n'  # TODO: Percy-v2 Release Note
                        f'## {Emojis.very_cool} Percy-v2 has been released and is online.\n'
                        f'{Emojis.info} *`If you encounter any issues or have any suggestions, '
                        f'please let me know by using the "{prefix}feedback" command!`*\n\n'
                        f'Use `{prefix}v2` to get more information about the Percy-v2 Release.\n\n'
                        f'**Privacy Policy**: [Click here](https://t.ly/vAhUk)\n'
                        f'**Terms of Service**: [Click here](https://t.ly/8V2D4)',
            colour=helpers.Colour.white()
        )
        embed.set_thumbnail(url=get_asset_url(ctx.guild))
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
                f'**Total Commands:** `{len(ctx.bot.commands)}`\n'
                f'**Total Commands Invoked:** `{await self.total_commands_invoked()}`'
            ),
        )
        embed.set_author(name=ctx.client.user, icon_url=get_asset_url(ctx.client.user))
        embed.set_footer(text='I was created at')
        embed.timestamp = ctx.client.user.created_at
        return embed

    def get_flag_help_embed(self) -> discord.Embed:
        """|coro|

        Returns the flag help page of the help command.

        Returns
        -------
        :class:`discord.Embed`
            The front page of the help command.
        """
        ctx = self.context
        prefix = ctx.clean_prefix

        embed = discord.Embed(
            title='Command Argument Overview',
            description='**```\nType command arguments without the brackets shown here!```**',
            colour=helpers.Colour.white()
        )
        embed.set_thumbnail(url=get_asset_url(ctx.guild))
        embed.add_field(name='`<argument>`', value='This argument is **required**.', inline=False)
        embed.add_field(name='`[argument]`', value='This argument is **optional**.', inline=False)
        embed.add_field(name='`<A|B>`',
                        value='This means **multiple choice**, you can choose by using one. Although it must be A or B.',
                        inline=False)
        embed.add_field(name='`<argument...>`', value='There are multiple arguments.', inline=False)
        embed.add_field(name='`<"argument">`',
                        value='This argument is case-sensitive and should be typed exactly as shown.', inline=False)
        embed.add_field(name='`<argument="A">`',
                        value='The default value if you dont provide one of this argument is **A**.', inline=False)
        embed.add_field(name='`[--name] or [--name <argument>] or [--name <argument="A">]`',
                        value='This argument is a **flag**. See below for more information on flags.', inline=False)

        escaped_asterisk = discord.utils.escape_markdown('*')
        embed.add_field(
            name='**Command Flags**',
            value='Flags are POSIX-like arguments that can be passed to a command.\n'
                  'They are prefixed with `--` and can be used in any order.\n\n'
                  'Flags that take no value (shown as `[--flag1]`) and represent a boolean value are called **store-true** flags. '
                  'If the flag is not present, the value is always `False`, if it is present, the value is `True`:\n'
                  f'E.g. `{prefix}command --flag1 --flag2`\n\n'
                  'A flag that can take a value (shown as `[--flag1 <argument>]`) is a normal flag. Therefore, if you provide the flag,\n'
                  'you always need to pass a value to it:\n'
                  f'E.g. `{prefix}command --flag1 this is flag1 --flag2 value for flag2`\n\n'
                  '**Short-hand** flags are prefixed with a single `-` and can be combined with other '
                  f'flags (__that take no arguments{escaped_asterisk}__) into one short flag:\n'
                  f'E.g. `{prefix}command -ab` is equal to `{prefix}command --a --b`.\n\n'
                  f'{escaped_asterisk} The last flag in the short-hand combination can take an argument.', inline=False)

        embed.set_author(name=ctx.bot.user, icon_url=get_asset_url(ctx.bot.user))
        return embed

    async def command_formatting(self, command: AnyCommand) -> discord.Embed:
        """|coro|

        Returns an Embed with the command formatting.
        This is a modified version of the original command_formatting.

        Parameters
        ----------
        command: :class:`.commands.Command`
            The command to format.

        Returns
        -------
        :class:`discord.Embed`
            The formatted command.
        """
        from app.core import Command, HybridCommand

        ctx = self.context
        embed = EmbedBuilder()
        embed.set_author(name='Command Help', icon_url=COMMAND_ICON_URL)

        signature = AnsiStringBuilder()
        signature.append(ctx.clean_prefix, color=AnsiColor.white, bold=True)
        signature.append(command.qualified_name + ' ', color=AnsiColor.green, bold=True)
        signature.extend(Command.ansi_signature_of(command))

        description = inspect.cleandoc(command.help or command.description or 'No description provided.')

        signature = signature.ensure_codeblock(fallback='md').dynamic(ctx)
        embed.description = f'{signature}\n{description}'

        embed.add_fields(self.get_command_signature(command, descripted=True))
        embed.add_fields(self.get_command_flag_signature(command, descripted=True))

        if getattr(command, 'aliases', None):
            embed.add_field(name=f'{Emojis.Command.alias} | **Aliases**',
                            value=' '.join(f'`{alias}`' for alias in command.aliases), inline=False)

        if cooldown := command._buckets._cooldown:  # noqa
            humanized = f'{cooldown.rate} time(s) per {humanize_duration(cooldown.per)}'
            embed.add_field(name='\N{HOURGLASS} Cooldown', value=humanized)

        if getattr(command, 'commands', None):
            resolved_sub_commands = [
                f'- `{self.get_command_signature(cmd)}`' for cmd in command.walk_commands() if not cmd.hidden  # type: ignore
            ]
            if resolved_sub_commands:
                embed.add_field(
                    name=f'{Emojis.info} | **Subcommands**',
                    value='\n'.join(resolved_sub_commands),
                    inline=False
                )

        if isinstance(command, AnyCommand):
            assert isinstance(command, (Command, HybridCommand))

            spec = command.permissions
            parts = []

            if user := spec.user:
                parts.append('User: ' + ', '.join(map(spec.permission_as_str, user)))
            if bot := spec.bot:
                parts.append('Bot: ' + ', '.join(map(spec.permission_as_str, bot)))

            embed.add_field(name=f'{Emojis.Command.locked} | **Required Permissions**',
                            value='\n'.join(parts), inline=False)

        if examples := command.extras.get('examples'):
            command_signature = self.get_command_signature(command, no_signature=True)
            embed.add_field(
                name=f'{Emojis.Command.example} | **Examples**',
                value='\n'.join(f'* `{command_signature} {example}`' for example in examples),
                inline=False
            )

        return embed

    @classmethod
    def temporary(cls, context: Context | discord.Interaction) -> 'PaginatedHelpCommand':
        """Returns a temporary instance of the help command.

        Useful for helper functions that require a help command instance.

        Parameters
        ----------
        context: class:`Context` | :class:`discord.Interaction`
            The context to use for the temporary help command.

        Returns
        -------
        :class:`PaginatedHelpCommand`
            The temporary help command instance.
        """
        self = cls()
        self.context = context
        return self
