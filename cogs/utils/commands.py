from __future__ import annotations

import types
from dataclasses import dataclass
from typing import List, Optional, Union, Dict, Any, Callable, TypeVar, ClassVar, Coroutine

import discord
from discord import app_commands
from discord.abc import Snowflake
from discord.app_commands import locale_str
from discord.ext.commands import *
from discord.ext import commands
from cogs.utils import checks, helpers
from cogs.utils import errors as error_utils
from cogs.utils.constants import App

# Aliases
core_command = commands.command
FlagConverter = helpers.FlagConverter

BadArgument = error_utils.BadArgument
CommandError = error_utils.CommandError


AnyCommand = Union[app_commands.command, command, group, hybrid_command, hybrid_group]


T = TypeVar('T')


@dataclass
class CooldownMap:
    """A class that represents a mapping of cooldowns."""

    rate: int
    per: int | float
    type: Optional[BucketType] = BucketType.default
    key: Optional[Callable] = None


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
        *,
        user: Optional[List[str] | str] = PermissionTemplate.user,
        bot: Optional[List[str] | str] = PermissionTemplate.bot  # noqa
) -> Callable[[T], T]:
    r"""A custom decorator that allows you to assign permission for the bot and user.

    Parameters
    ----------
    user: Optional[List[str]]
        A list of permissions that the user must have to run the command.
    bot: Optional[List[str]]
        A list of permissions that the bot must have to run the command.
    """

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
            func = checks.hybrid_bot_permissions_check(**_bot_permissions)(func)

        if _user_permissions:
            func = checks.hybrid_user_permissions_check(**_user_permissions)(func)

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
        perm_template: bool = True,
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
    perm_template: bool
        Whether to return the command with an applied :class:`PermissionTemplate`.
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

    if not extras:
        extras = {}

    if not extras.get('examples'):
        extras['examples'] = examples

    def decorator(f: types.FunctionType) -> Callable[[tuple[Any, ...], dict[str, Any]], Coroutine[Any, Any, Any]]:
        f = func(
            name=name,
            extras=extras,
            description=description,
            nsfw=nsfw,
            **kwargs
        )(f)

        # Wrap the command with the permission template and other check decorators
        if perm_template:
            f = permissions()(f)

        if isinstance(f, App):
            if cooldown:
                f = app_commands.checks.cooldown(rate=cooldown.rate, per=cooldown.per, key=cooldown.key)(f)
            if guild_only:
                f = app_commands.guild_only()(f)
        else:
            if cooldown:
                f = commands.cooldown(rate=cooldown.rate, per=cooldown.per, type=cooldown.type)(f)
            if guild_only:
                f = commands.guild_only()(f)

        return f
    return decorator
