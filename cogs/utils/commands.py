from __future__ import annotations
import enum
import inspect
from typing import List, Optional, Union, Dict, Any, Callable, TypeVar, ClassVar

import discord
from discord import app_commands
from discord.abc import Snowflake
from discord.app_commands import locale_str
from discord.ext.commands import *
from discord.ext import commands
from cogs.utils import checks, helpers


# Aliases
core_command = commands.command
FlagConverter = helpers.FlagConverter


class CommandCategory(enum.Enum):
    """The category of the command.

    Note
    ----
    App = 1; Hybrid = 2; Core = 3;
    """
    App = 1
    Hybrid = 2
    Core = 3


AnyCommand = Union[
    app_commands.command,
    command,
    group,
    hybrid_command,
    hybrid_group,
]

AnyCommandSignature = {
    'hybrid.py': CommandCategory.Hybrid,
    'core.py': CommandCategory.Core,
    'commands.py': CommandCategory.App,
}


T = TypeVar('T')


class CooldownMap:
    """A class that represents a mapping of cooldowns."""

    rate: ClassVar[int]
    per: ClassVar[int]
    type: Optional[ClassVar[BucketType]]
    key: Optional[ClassVar[Callable]]


class PermissionTemplate:
    r"""Permission Templates for the bot and user."""

    bot: ClassVar[str] = ['send_messages', 'embed_links', 'attach_files', 'use_external_emojis',
                          'view_channel', 'read_message_history']
    user: ClassVar[str] = []  # Placeholder
    mod: ClassVar[str] = ['ban_members', 'manage_messages']
    admin: ClassVar[str] = ['administrator']
    manager: ClassVar[str] = ['manage_guild']


def guilds(*guild_ids: Union[Snowflake, int]) -> Callable[[T], T]:
    r"""Decorator to set the guilds which this command will be added to by default.

    Works for App, Hybrid and Core commands/groups.

    Parameters
    -----------
    \*guild_ids: Union[:class:`int`, :class:`~discord.abc.Snowflake`]
        The guilds to associate this command with. The command tree will
        use this as the default when added rather than adding it as a global
        command.
    """

    defaults: List[int] = [g if isinstance(g, int) else g.id for g in guild_ids]

    def decorator(inner: T) -> T:
        if isinstance(inner, (app_commands.commands.Group, app_commands.commands.ContextMenu)):
            inner._guild_ids = defaults
        elif isinstance(inner, app_commands.commands.Command):
            if inner.parent is not None:
                raise ValueError('Child commands of a group cannot have default guilds set.')

            inner._guild_ids = defaults
        else:
            # Runtime attribute assignment
            inner.__discord_app_commands_default_guilds__ = defaults

            # Apply custom guild check decorator that checks if the guild is in the default guilds
            # Used for Message/Text Commands
            checks.guilds_check(*guild_ids)(inner)

        return inner

    return decorator


def permissions(
        category: CommandCategory | int = CommandCategory.Hybrid,
        *,
        user: Optional[List[str] | str] = PermissionTemplate.user,
        bot: Optional[List[str] | str] = PermissionTemplate.bot  # noqa
) -> Callable[[T], T]:
    r"""A custom decorator that allows you to assign permission for the bot and user.
    Assign a :class:`CommandCategory` to the ``category`` parameter to determine which decorator to use.

    Note
    ----
    To set permissions accordingly, the function that you are decorating must be wrapped with the :func:`command` decorator.
    It needs to have the ``__type_info__`` attribute to handle the permission setting.

    Parameters
    ----------
    category: CommandCategory | int
        The type of command you are creating. This is used to determine which decorator to use.
        If you are using a hybrid command, you must use the `CommandCategory.Hybrid` enum.
    user: Optional[List[str]]
        A list of permissions that the user must have to run the command.
    bot: Optional[List[str]]
        A list of permissions that the bot must have to run the command.
    """

    if isinstance(category, int):
        category = CommandCategory(category)

    if bot is not PermissionTemplate.bot:
        bot += PermissionTemplate.bot

    invalid = (set(user) | set(bot)) - set(discord.Permissions.VALID_FLAGS)
    if invalid:
        raise TypeError(f'Invalid permission(s): {', '.join(invalid)}')

    # After this point, we can assume that the permissions are valid.
    # We are now creating a mapping of permissions to a boolean value set to True.
    # This is used to create a discord.Permissions object.

    _user_permissions = {permission: True for permission in user}
    _bot_permissions = {permission: True for permission in bot}

    def decorator(func: T) -> T:
        # Mainly App/Hybrid Commands
        func.default_permissions = discord.Permissions(**_user_permissions)

        if _bot_permissions:
            if category == CommandCategory.App:
                func = app_commands.checks.bot_has_permissions(**_bot_permissions)(func)
            elif category in (CommandCategory.Core, CommandCategory.Hybrid):
                func = bot_has_permissions(**_bot_permissions)(func)

        if _user_permissions:
            if category == CommandCategory.App:
                func = app_commands.checks.has_permissions(**_user_permissions)(func)
            elif category == CommandCategory.Core:
                func = has_permissions(**_user_permissions)(func)
            elif category == CommandCategory.Hybrid:
                func = checks.hybrid_permissions_check(**_user_permissions)(func)
            else:
                func.__discord_app_commands_default_permissions__ = discord.Permissions(**_user_permissions)

        # This is used to determine the bot and user
        # permissions wherever you like,
        # for example, in the help command.
        func.__bot_permissions__ = _bot_permissions
        func.__user_permissions__ = _user_permissions

        return func

    return decorator


def command(
        func: AnyCommand = hybrid_command,
        *,
        name: Optional[str] = None,
        description: Union[str, locale_str] = 'Command undocumented.',
        examples: List[str] = None,
        nsfw: bool = False,
        extras: Dict[str, Any] = None,
        raw: bool = False,
        guild_only: bool = False,  # noqa
        cooldown: CooldownMap = None,  # noqa
        **kwargs
):
    r"""A custom decorator that assigns a function as a command.

    This decorator merges the functionality of ``commands.command``,
    ``commands.group``, ``commands.hybrid_command``, ``commands.hybrid_command`` and
    ``app_commands.command`` for easier accessibility.

    Note
    ----
    This decorator also adds a ``__type_info__`` attribute to the command that contains the
    module and function name of the command. This is used for handling the correct permission checks for every command type.

    It also adds a :class:`PermissionTemplate` that can be modified with the :func:`permissions` decorator.
    Default permissions are set to ``PermissionTemplate.user`` for the user and ``PermissionTemplate.bot`` for the bot.

    Parameters
    ----------
    func: AnyCommand
        The command type to use. Defaults to ``discord.ext.commands.hybrid_command``.
    name: Optional[str]
        The name of the command. Defaults to the name of the function ``func.__name__``.
    description: Union[str, locale_str]
        The description of the command. Defaults to ``"Command undocumented."``.
    examples: List[str]
        A list of examples for the command. Defaults to ``None``.
    nsfw: bool
        Whether the command is NSFW. Defaults to ``False``.
    extras: Dict[Any, Any]
        A dictionary of extra information to be stored in the command. Defaults to ``None``.
    raw: bool
        Whether to or not to return the command with an applied :class:`PermissionTemplate`.
    guild_only: bool
        Whether the command can only be used in a guild.
    cooldown: CooldownMapping
        A mapping of cooldowns for the command. Defaults to ``None``.
        This can be used for app- and core commands.
    **kwargs
        Any keyword arguments to be passed to the command type.

    Returns
    -------
    AnyCommand
        The wrapped command with or without the applied :class:`PermissionTemplate`.
    """

    signature = AnyCommandSignature.get(inspect.getfile(func).split('\\')[-1])
    # Searching for filename of the position from the function
    if not extras:
        extras = {}

    if not extras.get('examples'):
        extras['examples'] = examples

    self = func(
        name=name,
        extras=extras,
        description=description,
        nsfw=nsfw,
        **kwargs
    )
    setattr(self, '__type_info__', f'{func.__module__}.{func.__name__}')

    if guild_only:
        if func == app_commands.command:
            self = app_commands.guild_only()(self)
        else:
            self = commands.guild_only()(self)

    if cooldown:
        if func == app_commands.command:
            self = app_commands.checks.cooldown(rate=cooldown.rate, per=cooldown.per, key=cooldown.key)(self)
        else:
            self = commands.cooldown(rate=cooldown.rate, per=cooldown.per, type=cooldown.type)(self)

    if raw:
        return self
    return permissions(signature)(self)
