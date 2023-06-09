from __future__ import annotations
import enum
import inspect
from pkgutil import iter_modules
from typing import List, Optional, Union, Dict, Any, Callable, TypeVar, ClassVar

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands
from discord.utils import MISSING

from cogs.utils import checks
from cogs.utils.context import Context

EXTENSIONS = [module.name for module in iter_modules(__path__, f'{__package__}.')]


def perm_fmt(perm: str) -> str: return perm.replace('_', ' ').title().replace('Guild', 'Server')


T = TypeVar('T')


class PermissionTemplate:
    bot: ClassVar[str] = ["send_messages", "embed_links", "attach_files", "use_external_emojis",
                          "view_channel", "read_message_history"]


class CMD(enum.Enum):
    App = 1
    Hybrid = 2
    Core = 3


AnyCommand = Union[
    app_commands.command,
    commands.command,
    commands.group,
    commands.hybrid_command,
    commands.hybrid_group,
]

AnyCommandSignature = {
    "hybrid.py": CMD.Hybrid,
    "core.py": CMD.Core,
    "commands.py": CMD.App,
}


def command_permissions(
        setter: CMD | int = CMD.Core,
        *,
        user: Optional[List[str]] = [],
        bot: Optional[List[str]] = PermissionTemplate.bot
) -> Callable[[T], T]:
    r"""A decorator that sets permission Info for a command."""

    if isinstance(setter, int):
        setter = CMD(setter)

    if bot is not PermissionTemplate.bot:
        bot += PermissionTemplate.bot

    invalid = (set(user) | set(bot)) - set(discord.Permissions.VALID_FLAGS)
    if invalid:
        raise TypeError(f"Invalid permission(s): {', '.join(invalid)}")

    user_perms = {perm: True for perm in user}
    bot_perms = {perm: True for perm in bot}

    readable_bot_perms = [perm_fmt(perm) for perm in bot]
    readable_user_perms = [perm_fmt(perm) for perm in user]

    def decorator(func: T) -> T:
        func.default_permissions = discord.Permissions(**user_perms)

        if bot_perms:
            if setter == CMD.App:
                func = app_commands.checks.bot_has_permissions(**bot_perms)(func)
            elif setter in (CMD.Core, CMD.Hybrid):
                func = commands.bot_has_permissions(**bot_perms)(func)

        if user_perms:
            if setter == CMD.App:
                func = app_commands.checks.has_permissions(**user_perms)(func)
            elif setter == CMD.Core:
                func = commands.has_permissions(**user_perms)(func)
            elif setter == CMD.Hybrid:
                func = checks.hybrid_permissions_check(**user_perms)(func)
            else:
                func.__discord_app_commands_default_permissions__ = discord.Permissions(**user_perms)

        func.__readable_bot_perms__ = readable_bot_perms
        func.__readable_user_perms__ = readable_user_perms

        return func

    return decorator


def command(
        func: AnyCommand = commands.hybrid_command,
        *,
        name: Optional[str] = None,
        description: Union[str, locale_str] = "Command undocumented.",
        examples: List[str] = None,
        nsfw: bool = False,
        extras: Dict[Any, Any] = MISSING,
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
    """

    perm_category = AnyCommandSignature.get(inspect.getfile(func).split('\\')[-1])
    if not extras:
        extras = {}

    if not extras.get("examples"):
        extras["examples"] = examples

    self = func(
        name=name,
        extras=extras,
        description=description,
        nsfw=nsfw,
        **kwargs
    )
    setattr(self, "__class_info__", f"{func.__module__}.{func.__name__}")
    return command_permissions(perm_category)(self)
