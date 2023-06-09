from pkgutil import iter_modules

from discord import app_commands
from discord.ext import commands
from dataclasses import dataclass, field
from typing import List, Optional, Union

EXTENSIONS = [module.name for module in iter_modules(__path__, f'{__package__}.')]


# Custom Command Decorator for Adding Extra **kwargs


class PermissionTemplate:
    def __init__(
            self, bot: List[str] = [], user: List[str] = [], template=None
    ) -> None:
        self.bot = bot
        self.user = user

        if template:
            self.bot += template.bot
            self.user += template.user


class PermissionTemplates:
    text_command: PermissionTemplate = PermissionTemplate(
        bot=["send_messages", "read_message_history", "send_messages_in_threads"],
        user=["send_messages", "read_message_history", "send_messages_in_threads"],
    )
    hybrid_command = PermissionTemplate = PermissionTemplate(
        template=text_command, user=["use_application_commands"]
    )


@dataclass
class CommandPermissions:
    template: Optional[PermissionTemplate] = PermissionTemplates.hybrid_command
    bot: List[str] = field(default_factory=list)
    user: List[str] = field(default_factory=list)


def command(
        func: Union[
            app_commands.command,
            commands.command,
            commands.group,
            commands.hybrid_command,
            commands.hybrid_group,
        ] = commands.hybrid_command,
        *,
        name=None,
        examples: List[str] = [],
        permissions: CommandPermissions = CommandPermissions(None, [], []),
        description="Command undocumented.",
        **kwargs
):
    r"""Custom command decorator for adding extra kwargs to the command.

    Attributes
    ----------
    func: Union[app_commands.command, commands.command, commands.group, commands.hybrid_command, commands.hybrid_group]
        The command to be decorated.
    name: :class:`str`
        The name of the command.
    callback: :ref:`coroutine <coroutine>`
        The coroutine that is executed when the command is called.
    help: Optional[:class:`str`]
        The long help text for the command.
    brief: Optional[:class:`str`]
        The short help text for the command.
    usage: Optional[:class:`str`]
        A replacement for arguments in the default help text.
    aliases: Union[List[:class:`str`], Tuple[:class:`str`]]
        The list of aliases the command can be invoked under.
    enabled: :class:`bool`
        A boolean that indicates if the command is currently enabled.
        If the command is invoked while it is disabled, then
        :exc:`.DisabledCommand` is raised to the :func:`.on_command_error`
        event. Defaults to ``True``.
    parent: Optional[:class:`Group`]
        The parent group that this command belongs to. ``None`` if there
        isn't one.
    cog: Optional[:class:`Cog`]
        The cog that this command belongs to. ``None`` if there isn't one.
    checks: List[Callable[[:class:`.Context`], :class:`bool`]]
        A list of predicates that verifies if the command could be executed
        with the given :class:`.Context` as the sole parameter. If an exception
        is necessary to be thrown to signal failure, then one inherited from
        :exc:`.CommandError` should be used. Note that if the checks fail then
        :exc:`.CheckFailure` exception is raised to the :func:`.on_command_error`
        event.
    description: :class:`str`
        The message prefixed into the default help command.
    hidden: :class:`bool`
        If ``True``\, the default help command does not show this in the
        help output.
    rest_is_raw: :class:`bool`
        If ``False`` and a keyword-only argument is provided then the keyword
        only argument is stripped and handled as if it was a regular argument
        that handles :exc:`.MissingRequiredArgument` and default values in a
        regular matter rather than passing the rest completely raw. If ``True``
        then the keyword-only argument will pass in the rest of the arguments
        in a completely raw matter. Defaults to ``False``.
    invoked_subcommand: Optional[:class:`Command`]
        The subcommand that was invoked, if any.
    require_var_positional: :class:`bool`
        If ``True`` and a variadic positional argument is specified, requires
        the user to specify at least one argument. Defaults to ``False``.

        .. versionadded:: 1.5

    ignore_extra: :class:`bool`
        If ``True``\, ignores extraneous strings passed to a command if all its
        requirements are met (e.g. ``?foo a b c`` when only expecting ``a``
        and ``b``). Otherwise :func:`.on_command_error` and local error handlers
        are called with :exc:`.TooManyArguments`. Defaults to ``True``.
    cooldown_after_parsing: :class:`bool`
        If ``True``\, cooldown processing is done after argument parsing,
        which calls converters. If ``False`` then cooldown processing is done
        first and then the converters are called second. Defaults to ``False``.
    extras: :class:`dict`
        A dict of user provided extras to attach to the Command.

        .. note::
            This object may be copied by the library.


        .. versionadded:: 2.0

    examples: List[:class:`str`]
        A list of examples for the command.
    permissions: :class:`CommandPermissions`
        A dataclass containing the permissions for the command.

        .. versionadded:: CUSTOM COMMAND DECORATOR by Klappstuhl
    """

    perms = {"bot": permissions.bot, "user": permissions.user}
    if permissions.template:
        perms["bot"] += permissions.template.bot
        perms["user"] += permissions.template.user

    return func(
        name=name,
        extras={
            "permissions": {"bot": permissions.bot, "user": permissions.user},
            "examples": examples,
        },
        description=description,
        **kwargs
    )
