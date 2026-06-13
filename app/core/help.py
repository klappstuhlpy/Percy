from __future__ import annotations

import functools
import inspect
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands
from discord.ext.commands import Cog

from app.core.command import Command, HybridCommand
from app.core.components_v2 import NoticeView
from app.core.flags import FlagMeta
from app.core.views import LayoutView
from app.utils import AnsiColor, AnsiStringBuilder, get_asset_url, helpers, humanize_duration, pluralize, truncate
from config import Emojis

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Mapping

    from app.core import Bot, Cog, Context

AnyGroup = commands.Group | commands.HybridGroup
AnyCommand = commands.Command | commands.HybridCommand | AnyGroup

COMMAND_ICON_URL = "https://klappstuhl.me/gallery/raw/jQkGI.png"

#: How many commands a single category page lists.
COMMANDS_PER_PAGE = 6


# -- shared rendering helpers ---------------------------------------------


def create_prefixes(cmd: AnyCommand) -> list[str]:
    """The small status emojis (locked / has-more-help) shown before a command."""
    prefixes = []
    if getattr(cmd, "is_locked", False):
        prefixes.append(Emojis.Command.locked)
    if getattr(cmd, "has_more_help", False):
        prefixes.append(Emojis.Command.more_info)
    return prefixes


def iter_help_notes(is_any_locked: bool, any_has_more_help: bool, prefix: str) -> Generator[str, Any, None]:
    """The explanatory footnotes shown under a category's command list."""
    if is_any_locked:
        yield f"{Emojis.Command.locked} » This command expects certain permissions from the user to be run."
    if any_has_more_help:
        yield f"{Emojis.Command.more_info} » This command has more detailed help available with `{prefix}help <command>`."


def add_field_blocks(container: discord.ui.Container, fields: list[dict[str, str | bool]]) -> None:
    """Render embed-style ``{name, value}`` field dicts as CV2 text displays."""
    for field in fields:
        name = str(field.get("name", ""))
        value = str(field.get("value", ""))
        if name and name != "​":
            container.add_item(discord.ui.TextDisplay(f"**{name}**\n{value}"))
        else:
            container.add_item(discord.ui.TextDisplay(value))


# -- Components V2 help view -----------------------------------------------


class HelpView(LayoutView):
    """The Components V2 help menu — front page, paginated category pages and selects.

    Replaces the old embed-based ``HelpPaginator``. A single :class:`~app.core.LayoutView`
    holds the current card, the prev/next navigation row (on category pages) and the
    category :class:`CategorySelect`s, all swapped in place as the user navigates.
    """

    def __init__(
        self,
        helper: PaginatedHelpCommand,
        *,
        mapping: dict[Cog, list[AnyCommand]],
        group: Cog | None = None,
        entries: list[AnyCommand] | None = None,
        with_index: bool = True,
    ) -> None:
        super().__init__(members=helper.context.author, timeout=180)
        self.helper: PaginatedHelpCommand = helper
        self.mapping: dict[Cog, list[AnyCommand]] = mapping
        self.group: Cog | None = group
        self.with_index: bool = with_index
        self._set_entries(entries or [])

    def _set_entries(self, entries: list[AnyCommand]) -> None:
        cmds = list(entries)
        self.entries: list[AnyCommand] = cmds
        self.pages: list[list[AnyCommand]] = [
            cmds[i : i + COMMANDS_PER_PAGE] for i in range(0, len(cmds), COMMANDS_PER_PAGE)
        ] or [[]]
        self.index: int = 0

    @property
    def total_pages(self) -> int:
        return len(self.pages)

    async def _current_container(self) -> discord.ui.Container:
        if self.group is None:
            return await self.helper.build_front_page_container()
        return self.helper.build_group_container(
            self.group, self.pages[self.index], index=self.index, total_pages=self.total_pages,
            total_commands=len(self.entries),
        )

    def _make_nav_row(self) -> discord.ui.ActionRow:
        row: discord.ui.ActionRow = discord.ui.ActionRow()
        back = discord.ui.Button(style=discord.ButtonStyle.green, label="<==")
        back.callback = functools.partial(self._navigate, delta=-1)  # type: ignore[assignment]
        indicator = discord.ui.Button(
            style=discord.ButtonStyle.grey, label=f"{self.index + 1}/{self.total_pages}", disabled=True
        )
        forward = discord.ui.Button(style=discord.ButtonStyle.green, label="==>")
        forward.callback = functools.partial(self._navigate, delta=1)  # type: ignore[assignment]
        row.add_item(back)
        row.add_item(indicator)
        row.add_item(forward)
        return row

    async def _navigate(self, interaction: discord.Interaction, *, delta: int) -> None:
        self.index = (self.index + delta) % self.total_pages
        await self.compose()
        await interaction.response.edit_message(view=self)

    async def compose(self) -> None:
        """Rebuild every component for the current state (card + nav + selects)."""
        self.clear_items()
        self.add_item(await self._current_container())

        if self.group is not None and self.total_pages > 1:
            self.add_item(self._make_nav_row())

        default = self.group if self.group in self.mapping else None
        for select in build_category_selects(
            self.helper.context.bot, self.mapping, with_index=self.with_index, default=default
        ):
            select.bind(self)
            self.add_item(discord.ui.ActionRow(select))

    def show_group(self, cog: Cog, entries: list[AnyCommand]) -> None:
        """Switch the view to a category's command list (page 0)."""
        self.group = cog
        self._set_entries(entries)

    def show_front_page(self) -> None:
        """Switch the view back to the index/front page."""
        self.group = None
        self._set_entries([])

    @classmethod
    async def start(
        cls,
        helper: PaginatedHelpCommand,
        *,
        mapping: dict[Cog, list[AnyCommand]],
        group: Cog | None = None,
        entries: list[AnyCommand] | None = None,
        with_index: bool = True,
    ) -> HelpView:
        """Build and send the help menu for the helper's invoking context."""
        self = cls(helper, mapping=mapping, group=group, entries=entries, with_index=with_index)
        await self.compose()
        self.message = await helper.context.send(view=self)
        return self


class CategorySelect(discord.ui.Select["HelpView"]):
    """A select menu that switches the :class:`HelpView` between categories."""

    def __init__(
        self,
        bot: Bot,
        mapping: dict[Cog, list[AnyCommand]],
        *,
        cogs: list[Cog] | None = None,
        with_index: bool = True,
        default: Cog | None = None,
    ) -> None:
        super().__init__(placeholder="Select a category to view...")
        self.bot: Bot = bot

        self.with_index: bool = with_index
        self.default: Cog | None = default

        # ``mapping`` is the full category map (used by the callback / index page);
        # ``cogs`` is the subset of categories this particular select renders as options,
        # so the categories can be spread across several selects when there are too many
        # to fit Discord's 25-option limit on a single select.
        self.mapping: dict[Cog, list[AnyCommand]] = mapping
        self.cog_mapping: dict[str, Cog] = {cog.qualified_name: cog for cog in mapping}
        self.option_cogs: list[Cog] = cogs if cogs is not None else list(mapping)
        self._helpview: HelpView | None = None

        self.__fill_options()

    def bind(self, view: HelpView) -> None:
        """Attach the owning view so the callback can drive navigation."""
        self._helpview = view

    def __fill_options(self) -> None:
        if self.with_index:
            self.add_option(
                label="Start Page",
                emoji=Emojis.Arrows.left,
                value="__index",
                description="The front page of the Help Menu.",
            )

        for cog in self.option_cogs:
            emoji = getattr(cog, "emoji", None)
            self.add_option(
                label=cog.qualified_name,
                value=cog.qualified_name,
                description=truncate(cog.description, 50),
                emoji=emoji,
                default=cog is self.default,
            )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self._helpview is not None
        view = self._helpview

        value = self.values[0]
        if value == "__index":
            view.show_front_page()
        else:
            cog = self.cog_mapping.get(value)
            if cog is None:
                await interaction.response.send_message(
                    f"{Emojis.error} Somehow this category does not exist?", ephemeral=True
                )
                return

            _commands = view.mapping.get(cog, [])
            if not _commands:
                await interaction.response.send_message(
                    f"{Emojis.error} This category has no commands for you.", ephemeral=True
                )
                return

            view.show_group(cog, _commands)

        await view.compose()
        await interaction.response.edit_message(view=view)


#: Discord allows at most 25 options per select.
MAX_SELECT_OPTIONS = 25
#: Cap on category selects, leaving room in the view's rows for navigation buttons.
MAX_HELP_SELECTS = 3


def partition_categories(
    total: int, *, with_index: bool, max_options: int = MAX_SELECT_OPTIONS, max_selects: int = MAX_HELP_SELECTS
) -> list[tuple[int, int]]:
    """Split ``total`` categories into ``(start, end)`` slices that each fit one select.

    The first slice reserves one option for the "Start Page" entry when ``with_index`` is
    set. At most ``max_selects`` slices are produced; any categories beyond that are
    dropped (well above any realistic category count).
    """
    ranges: list[tuple[int, int]] = []
    start = 0
    for index in range(max_selects):
        capacity = max_options - (1 if with_index and index == 0 else 0)
        end = min(start + capacity, total)
        if start >= end:
            break
        ranges.append((start, end))
        start = end
        if start >= total:
            break
    return ranges


def build_category_selects(
    bot: Bot, mapping: dict[Cog, list[AnyCommand]], *, with_index: bool = True, default: Cog | None = None
) -> list[CategorySelect]:
    """Build one or more :class:`CategorySelect`s, chunked to fit Discord's option limit."""
    cogs = list(mapping)
    selects: list[CategorySelect] = []
    for offset, (start, end) in enumerate(partition_categories(len(cogs), with_index=with_index)):
        chunk = cogs[start:end]
        select = CategorySelect(
            bot, mapping, cogs=chunk, with_index=with_index and offset == 0, default=default
        )
        if len(cogs) > end or start > 0:
            # More than one select is in play; label each with its category range.
            select.placeholder = f"Categories: {chunk[0].qualified_name} – {chunk[-1].qualified_name}"
        selects.append(select)
    return selects


class PaginatedHelpCommand(commands.HelpCommand):
    """A subclass of the default help command that implements support for Application/Hybrid Commands."""

    context: Context | discord.Interaction

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            show_hidden=False,
            verify_checks=False,
            command_attrs={
                "cooldown": commands.CooldownMapping.from_cooldown(1, 3.0, commands.BucketType.member),
                "hidden": True,
                "aliases": ["h"],
                "description": "Get help for a module or a command.",
            },
            **kwargs,  # type: ignore[arg-type]
        )
        #: Whether to render commands as clickable ``</name:id>`` mentions. Enabled only
        #: when help is invoked as a slash command (kept off for prefix invocations).
        self._render_mentions: bool = False

    def get_bot_mapping(self) -> dict[Cog | None, list[Command]]:
        mapping = super().get_bot_mapping()
        del mapping[None]
        return mapping

    async def total_commands_invoked(self) -> int:
        """Returns the total amount of commands invoked."""
        return await self.context.db.stats.count_all_commands()  # type: ignore

    @staticmethod
    def command_requires_permissions(command: AnyCommand) -> bool:
        """Returns whether a command is locked or not."""
        if not isinstance(command, (Command, HybridCommand)):
            return False

        spec = command.permissions
        return bool(spec.user)

    def _get_all_subcommands(self, command: AnyCommand | AnyGroup, names: set[str]) -> set[AnyCommand]:
        """Returns all subcommands of a command."""
        subcommands: set[AnyCommand] = set()

        def add_subcommand(cmd: AnyCommand) -> None:
            nonlocal subcommands, names
            if not cmd.hidden and self.is_available(cmd) and cmd.qualified_name not in names:
                setattr(cmd, "is_locked", self.command_requires_permissions(cmd))

                subcommands.add(cmd)
                names.add(cmd.qualified_name)

        if isinstance(command, AnyGroup):
            # If the group is not just a placeholder, add it to the list
            if getattr(command, "fallback", None) is not None:
                add_subcommand(command)

            for subcommand in command.walk_commands():
                add_subcommand(subcommand)
        else:
            add_subcommand(command)

        return subcommands

    def is_available(self, command: AnyCommand) -> bool:
        """Returns whether a command is available or not by looking for guild_id restrictions."""
        guild_ids = getattr(command.callback, "__guild_ids__", None)
        if not guild_ids:
            return True
        return bool(self.context.guild and self.context.guild.id in guild_ids)

    async def filter_commands(
        self, commands: Iterable[AnyCommand], /, *, sort: bool = False, key: Callable[[AnyCommand], Any] | None = None
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
            key = lambda c: c.name

        iterator = commands if self.show_hidden else filter(lambda c: not c.hidden, commands)

        if getattr(self.context, "guild", None) is None:
            iterator = filter(lambda c: not getattr(c, "guild_only", False), iterator)

        ret: list[AnyCommand] = []
        names: set[str] = set()
        for command in iterator:
            ret.extend(self._get_all_subcommands(command, names))

        if sort:
            ret.sort(key=key)  # type: ignore[arg-type]
        return ret

    async def prepare_help_command(self, ctx: Context, command: str | None = None) -> None:
        """Runs before every help dispatch — both the text ``command_callback`` and
        ``Context.send_help`` (used by the ``/help`` slash command) call this, so it's the
        right place to resolve clickable command mentions for the slash path.
        """
        await super().prepare_help_command(ctx, command)
        if getattr(ctx, "interaction", None) is not None:
            self._render_mentions = True
            await ctx.bot.resolve_app_command_ids(guild=ctx.guild)

    async def command_callback(self, ctx: Context, /, *, command: str | None = None) -> None:
        if command is not None and command.lower() == "flags":
            await ctx.send(view=NoticeView(self.build_flag_help_container()), silent=True)
        else:
            await super().command_callback(ctx, command=command)

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
        flags: FlagMeta | None = getattr(command, "custom_flags", None)

        if not flags:
            return [] if descripted else ""

        resolved: list[str] = []

        if descripted:
            for flag in flags.walk_flags():
                fmt = f"- `--{flag.name}`: {flag.description}"
                resolved.append(fmt)

            chunked = list(discord.utils.as_chunks(resolved, 15))
            to_fields = []
            for i, chunk in enumerate(chunked):
                to_fields.append({"name": "Flags" if i == 0 else "​", "value": "\n".join(chunk), "inline": False})
            return to_fields
        else:
            for flag in flags.walk_flags():
                if flag.required:
                    start, end = "<>"
                else:
                    start, end = "[]"

                resolved.append(start + f"--{flag.name}" + end)

            return " ".join(resolved)

    def get_command_signature(
        self,
        command: AnyCommand,
        *,
        descripted: bool = False,
        no_signature: bool = False,
        args_only: bool = False,
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
        args_only: :class:`bool`
            Whether to return only the argument signature (without prefix or command name),
            e.g. ``<user> [reason]``. Used alongside a clickable command mention.

        Returns
        -------
        :class:`str`
            The command signature.
        """
        if descripted:
            params = command.clean_params
            resolved: list[str] = []

            for param in params.values():
                if isinstance(param.annotation, FlagMeta) and getattr(command, "custom_flags", None):
                    continue

                # resolve arg description through app_commands.describe
                # decorators or fallback to the default description if present
                description = getattr(command.callback, "__discord_app_commands_param_description__", param.description)
                if isinstance(description, dict):
                    description = description.get(param.name, "Argument undocumented.")

                fmt = f"- `{param.name}`: {description}"
                resolved.append(fmt)

            chunked = list(discord.utils.as_chunks(resolved, 15))
            to_fields = []
            for i, chunk in enumerate(chunked):
                to_fields.append({"name": "Arguments" if i == 0 else "​", "value": "\n".join(chunk), "inline": False})
            return to_fields

        prefix = self.context.clean_prefix

        if no_signature:
            return f"{prefix}{command.qualified_name}"

        signature = getattr(command, "ansi_signature", None)
        signature = signature.raw if signature is not None else command.signature
        hidden_tag = "[!]" if command.hidden else ""

        if args_only:
            return f"{truncate(signature, 150)} {hidden_tag}".strip()

        return f"{prefix}{command.qualified_name} {truncate(signature, 150)} {hidden_tag}".strip()

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
            return cmd.cog.qualified_name if cmd.cog else "No Category"

        entries = await self.filter_commands(self.context.bot.commands, sort=True, key=key)

        grouped: dict[Cog, list[AnyCommand]] = {}
        for command in entries:
            cog: Cog | None = self.context.bot.get_cog(key(command))
            if getattr(cog, "__hidden__", False):
                continue

            if cog and not command.hidden:
                grouped.setdefault(cog, []).append(command)

        grouped = dict(sorted(grouped.items(), key=lambda x: x[0].qualified_name))
        await HelpView.start(self, mapping=grouped)

    async def send_cog_help(self, cog: Cog) -> discord.Message | None:
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

        await HelpView.start(self, mapping={cog: entries}, group=cog, entries=entries, with_index=False)
        return None

    async def send_command_help(self, command: AnyCommand) -> discord.Message | None:
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

        container = await self.build_command_container(command)
        await self.context.send(view=NoticeView(container), silent=True)
        return None

    async def send_group_help(self, group: AnyGroup) -> None:
        """|coro|

        Sends the help command for a group.
        This is a modified version of the original send_group_help.

        Parameters
        ----------
        group: :class:`.commands.PartialCommandGroup`
            The group to send the help for.
        """
        await self.send_command_help(group)

    async def build_front_page_container(self) -> discord.ui.Container:
        """|coro| Build the Components V2 front page of the help menu."""
        ctx = self.context
        prefix = ctx.clean_prefix if isinstance(ctx, commands.Context) else ""

        bot_user = ctx.bot.user if isinstance(ctx, commands.Context) else ctx.client.user
        thumb = get_asset_url(ctx.guild) if ctx.guild else get_asset_url(bot_user)

        container = discord.ui.Container(accent_colour=helpers.Colour.white())
        container.add_item(
            discord.ui.Section(
                f"## {bot_user.name if bot_user else 'Percy'} Help\n"
                f"Check out Percy's dashboard by clicking [here](https://r.klappstuhl.me/db)!\n\n"
                f"**Privacy Policy**: [Click here](https://r.klappstuhl.me/pp)\n"
                f"**Terms of Service**: [Click here](https://r.klappstuhl.me/tos)",
                accessory=discord.ui.Thumbnail(thumb),
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            "### More Help\n"
            "Alternatively you can use the following commands to get information about a specific command or category:\n"
            f"- `{prefix}help <command>`\n"
            f"- `{prefix}help <category>`\n\n"
            f"You can also use `{prefix}help flags` to get an overview of how to use flags."
        ))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            "### Your Data & Privacy\n"
            "Percy stores some data about you to power features like presence graphs and "
            "name/avatar history. This tracking is **on by default**, and you're always in control:\n"
            f"- `{prefix}settings tracking false` — turn off **all** tracking at once\n"
            f"- `{prefix}settings presence false` / `{prefix}settings history false` — turn off one kind\n"
            f"- `{prefix}settings request-data` — export a copy of your stored data\n"
            f"- `{prefix}settings remove-personal-data` — permanently delete it"
        ))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            f"### Stats\n"
            f"**Total Commands:** `{len(ctx.bot.commands)}`\n"
            f"**Total Commands Invoked:** `{await self.total_commands_invoked()}`"
        ))
        container.add_item(discord.ui.Separator())
        created = discord.utils.format_dt(bot_user.created_at, "R") if bot_user else ""
        container.add_item(discord.ui.TextDisplay(f"-# {bot_user} • created {created}"))
        return container

    def build_group_container(
        self,
        group: Cog,
        entries: list[AnyCommand],
        *,
        index: int,
        total_pages: int,
        total_commands: int,
    ) -> discord.ui.Container:
        """Build the Components V2 card listing one page of a category's commands."""
        emoji = getattr(group, "emoji", "")
        container = discord.ui.Container(accent_colour=helpers.Colour.white())
        container.add_item(discord.ui.TextDisplay(f"## {emoji} {group.qualified_name}\n{group.description or ''}"))
        container.add_item(discord.ui.Separator())

        is_any_locked = any(getattr(cmd, "is_locked", False) for cmd in entries)
        any_has_more_help = any(getattr(cmd, "has_more_help", False) for cmd in entries)

        for cmd in entries:
            prefixes = create_prefixes(cmd)
            prefix = (" ".join(prefixes) + " | ") if prefixes else ""
            # When help was run as a slash command, show the clickable mention followed by
            # just the argument signature; otherwise (and for commands without an app-command
            # counterpart) fall back to the full prefixed signature.
            mention = getattr(cmd, "mention", None) if self._render_mentions else None
            if mention:
                args = self.get_command_signature(cmd, args_only=True)
                label = f"{mention} **`{args}`**" if args else mention
            else:
                label = f"**`{self.get_command_signature(cmd)}`**"
            description = cmd.description or "…"
            examples = cmd.extras.get("examples")
            if examples:
                cmd_sig = self.get_command_signature(cmd, no_signature=True)
                description += f"\n-# e.g. `{cmd_sig} {examples[0]}`"
            container.add_item(discord.ui.TextDisplay(f"{prefix}{label}\n{description}"))

        notes = list(iter_help_notes(is_any_locked, any_has_more_help, self.context.clean_prefix))
        if notes:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay("\n".join(notes)))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            f"-# {pluralize(total_commands):command} • Page {index + 1}/{total_pages} • "
            f"Showing only commands available to you in this category."
        ))
        return container

    def build_flag_help_container(self) -> discord.ui.Container:
        """Build the Components V2 card explaining command argument and flag syntax."""
        ctx = self.context
        prefix = ctx.clean_prefix

        container = discord.ui.Container(accent_colour=helpers.Colour.white())
        thumb = get_asset_url(ctx.guild) if ctx.guild else None
        header = "## Command Argument Overview\n**```\nType command arguments without the brackets shown here!```**"
        if thumb is not None:
            container.add_item(discord.ui.Section(header, accessory=discord.ui.Thumbnail(thumb)))
        else:
            container.add_item(discord.ui.TextDisplay(header))
        container.add_item(discord.ui.Separator())

        rows = [
            ("`<argument>`", "This argument is **required**."),
            ("`[argument]`", "This argument is **optional**."),
            ("`<A|B>`", "This means **multiple choice**, you can choose by using one. Although it must be A or B."),
            ("`<argument...>`", "There are multiple arguments."),
            ('`<"argument">`', "This argument is case-sensitive and should be typed exactly as shown."),
            ('`<argument="A">`', "The default value if you dont provide one of this argument is **A**."),
            (
                '`[--name] or [--name <argument>] or [--name <argument="A">]`',
                "This argument is a **flag**. See below for more information on flags.",
            ),
        ]
        for name, value in rows:
            container.add_item(discord.ui.TextDisplay(f"**{name}**\n{value}"))

        escaped_asterisk = discord.utils.escape_markdown("*")
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            "## Command Flags\n"
            "Flags are POSIX-like arguments that can be passed to a command.\n"
            "They are prefixed with `--` and can be used in any order.\n\n"
            "Flags that take no value (shown as `[--flag1]`) and represent a boolean value are called **store-true** flags. "
            "If the flag is not present, the value is always `False`, if it is present, the value is `True`:\n"
            f"E.g. `{prefix}command --flag1 --flag2`\n\n"
            "A flag that can take a value (shown as `[--flag1 <argument>]`) is a normal flag. Therefore, if you provide the flag,\n"
            "you always need to pass a value to it:\n"
            f"E.g. `{prefix}command --flag1 this is flag1 --flag2 value for flag2`\n\n"
            "**Short-hand** flags are prefixed with a single `-` and can be combined with other "
            f"flags (__that take no arguments{escaped_asterisk}__) into one short flag:\n"
            f"E.g. `{prefix}command -ab` is equal to `{prefix}command --a --b`.\n\n"
            f"{escaped_asterisk} The last flag in the short-hand combination can take an argument."
        ))
        return container

    async def build_command_container(self, command: AnyCommand) -> discord.ui.Container:
        """Build the Components V2 detail card for a single command."""
        from app.core import Command, HybridCommand

        ctx = self.context
        container = discord.ui.Container(accent_colour=helpers.Colour.white())

        signature = AnsiStringBuilder()
        signature.append(ctx.clean_prefix, color=AnsiColor.white, bold=True)
        signature.append(command.qualified_name + " ", color=AnsiColor.green, bold=True)
        signature.extend(Command.ansi_signature_of(command))  # type: ignore

        description = inspect.cleandoc(command.help or command.description or "No description provided.")
        rendered = signature.ensure_codeblock(fallback="md").dynamic(ctx)  # type: ignore
        container.add_item(
            discord.ui.Section(
                f"## Command Help\n"
                f"{rendered}\n"
                f"{description}",
                accessory=discord.ui.Thumbnail(COMMAND_ICON_URL)
            )
        )

        sig_fields = self.get_command_signature(command, descripted=True)
        if isinstance(sig_fields, list) and sig_fields:
            container.add_item(discord.ui.Separator())
            add_field_blocks(container, sig_fields)

        flag_fields = self.get_command_flag_signature(command, descripted=True)
        if isinstance(flag_fields, list) and flag_fields:
            add_field_blocks(container, flag_fields)

        extra: list[str] = []
        if getattr(command, "aliases", None):
            extra.append(
                f"**{Emojis.Command.alias} | Aliases**\n" + " ".join(f"`{alias}`" for alias in command.aliases)
            )

        if cooldown := command._buckets._cooldown:
            extra.append(
                f"**\N{HOURGLASS} Cooldown**\n{cooldown.rate} time(s) per {humanize_duration(cooldown.per)}"
            )

        if getattr(command, "commands", None):
            resolved_sub_commands = [
                f"- {(self._render_mentions and getattr(cmd, 'mention', None)) or f'`{self.get_command_signature(cmd)}`'}"
                for cmd in command.walk_commands()  # type: ignore[attr-defined]
                if not cmd.hidden
            ]
            if resolved_sub_commands:
                extra.append(f"**{Emojis.info} | Subcommands**\n" + "\n".join(resolved_sub_commands))

        if isinstance(command, (Command, HybridCommand)):
            spec = command.permissions
            parts = []
            if user := spec.user:
                parts.append("**User:** " + ", ".join(map(spec.permission_as_str, user)))
            if bot := spec.bot:
                parts.append("**Bot:** " + ", ".join(map(spec.permission_as_str, bot)))
            if parts:
                extra.append(f"**{Emojis.Command.locked} | Required Permissions**\n" + "\n".join(parts))

        if examples := command.extras.get("examples"):
            command_signature = self.get_command_signature(command, no_signature=True)
            extra.append(
                f"**{Emojis.Command.example} | Examples**\n"
                + "\n".join(f"* `{command_signature} {example}`" for example in examples)
            )

        related = self._find_related_commands(command)
        if related:
            related_lines = [
                (self._render_mentions and getattr(cmd, "mention", None)) or f"`{self.get_command_signature(cmd, no_signature=True)}`"
                for cmd in related[:5]
            ]
            extra.append(f"**{Emojis.info} | Related Commands**\n" + " • ".join(related_lines))

        if extra:
            container.add_item(discord.ui.Separator())
            for block in extra:
                container.add_item(discord.ui.TextDisplay(block))
                container.add_item(discord.ui.Separator())

        invoked: int = await ctx.bot.db.stats.get_command_invokation_count(command.qualified_name)
        footer = f"-# {command.qualified_name}"
        if invoked > 1:
            footer += f" • {invoked}x invoked"

        container.add_item(discord.ui.TextDisplay(footer))

        return container

    def _find_related_commands(self, command: AnyCommand) -> list[AnyCommand]:
        """Find sibling commands in the same cog, excluding the command itself."""
        cog = command.cog
        if cog is None:
            return []
        siblings = [
            cmd for cmd in cog.walk_commands()
            if cmd.qualified_name != command.qualified_name
            and not cmd.hidden
            and self.is_available(cmd)
        ]
        return siblings[:5]

    @classmethod
    def temporary(cls, context: Context | discord.Interaction) -> PaginatedHelpCommand:
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
