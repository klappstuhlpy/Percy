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


def perm_fmt(perm: str) -> str: return perm.replace('_', ' ').title().replace('Guild', 'Server')


T = TypeVar('T')


class PermissionTemplate:
    r"""Permission Templates for the bot and user."""

    bot: ClassVar[str] = ["send_messages", "embed_links", "attach_files", "use_external_emojis",
                          "view_channel", "read_message_history"]
    mod: ClassVar[str] = ["ban_members", "manage_messages"]
    admin: ClassVar[str] = ["administrator"]
    manager: ClassVar[str] = ["manage_guild"]


def command_permissions(
        setter: CMD | int = CMD.Core,
        *,
        user: Optional[List[str]] = [],
        bot: Optional[List[str]] = PermissionTemplate.bot
) -> Callable[[T], T]:
    r"""A custom decorator that allows you to assign permission for the bot and user.

    Note
    ----
    To set permissions accordingly, the function that you are decorating must be wrapped with the :func:`command` decorator.
    It needs to have the ``__type_info__`` attribute to handle the permission setting.

    Parameters
    ----------
    setter: CMD | int
        The type of command you are creating. This is used to determine which decorator to use.
        If you are using a hybrid command, you must use the `CMD.Hybrid` enum.
    user: Optional[List[str]]
        A list of permissions that the user must have to run the command.
    bot: Optional[List[str]]
        A list of permissions that the bot must have to run the command.
    """

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
        extras: Dict[Any, Any] = None,
        raw: bool = False,
        **kwargs
):
    r"""A custom decorator that assigns a function as a command.

    This decorator merges the functionality of :func:`commands.command`,
    :func:`commands.group`, and :func:`commands.hybrid_command` :func:`commands.hybrid_command and
    :func:`app_commands.command` for easier accessibility.

    Note
    ----
    This decorator also adds a ``__type_info__`` attribute to the command that contains the
    module and function name of the command. This is used for handling the correct permission checks for every command type.

    It also adds a :class:`PermissionTemplate` that can be modified with the :func:`command_permissions` decorator.

    Parameters
    ----------
    func: AnyCommand
        The command type to use. Defaults to :func:`discord.ext.commands.hybrid_command`.
    name: Optional[str]
        The name of the command. Defaults to ``func.__name__``.
    description: Union[str, locale_str]
        The description of the command. Defaults to ``"Command undocumented."``.
    examples: List[str]
        A list of examples for the command. Defaults to ``None``.
    nsfw: bool
        Whether or not the command is NSFW. Defaults to ``False``.
    extras: Dict[Any, Any]
        A dictionary of extra information to be stored in the command. Defaults to ``MISSING``.
    raw: bool
        Whether or not to return the command without an applied :class:`PermissionTemplate`.
    **kwargs
        Any keyword arguments to be passed to the command type.

    Returns
    -------
    AnyCommand
        The wrapped command with the optional applied :class:`PermissionTemplate`.
    """

    signature = AnyCommandSignature.get(inspect.getfile(func).split('\\')[-1])
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
    setattr(self, "__type_info__", f"{func.__module__}.{func.__name__}")

    if raw:
        return self
    return command_permissions(signature)(self)
