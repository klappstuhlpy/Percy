from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, TypeVar

import discord
from discord import app_commands
from discord.ext import commands

# Phase 5: the command, context, embed and permission classes were split out of
# this module into dedicated files. They are re-exported here so that existing
# ``from app.core.models import X`` imports keep working; new code should prefer
# importing from ``app.core`` or the dedicated modules.
from app.core.command import (
    Command,
    CommandInstance,
    GroupCommand,
    HybridCommand,
    HybridGroupCommand,
    ParamInfo,
    command,
    cooldown,
    describe,
    group,
    guild_max_concurrency,
    guilds,
    user_max_concurrency,
)
from app.core.context import Context, HybridContext, HybridContextProtocol
from app.core.embeds import EmbedBuilder
from app.core.permissions import PermissionSpec, PermissionTemplate

if TYPE_CHECKING:
    from app.core import Bot

CogT = TypeVar("CogT", bound="Cog")

__all__ = (
    "AppBadArgument",
    "BadArgument",
    "Cog",
    "CogT",
    "Command",
    "CommandInstance",
    "Context",
    "EmbedBuilder",
    "GroupCommand",
    "HybridCommand",
    "HybridContext",
    "HybridContextProtocol",
    "HybridGroupCommand",
    "ParamInfo",
    "PermissionSpec",
    "PermissionTemplate",
    "command",
    "cooldown",
    "describe",
    "group",
    "guild_max_concurrency",
    "guilds",
    "user_max_concurrency",
)


class AppBadArgument(app_commands.AppCommandError):
    """The base exception for all application command argument errors."""

    def __init__(self, message: str, namespace: str | None = None, /) -> None:
        self.namespace: str | None = namespace
        super().__init__(message)


class BadArgument(commands.BadArgument):
    """The base exception for all command argument errors.

    Using the `namespace` parameter, the name of a parameter will be passed down to the final error handler
    to specify the parameter of the command that should be highlighted responsible for the error.

    If the parameter is found in the command, this overrides the `Context.current_parameter` value.

    Note: The parsing is handled in the final error handler.
    """

    def __init__(self, message: str, namespace: str | None = None, /) -> None:
        self.namespace: str | None = namespace
        super().__init__(message)


@discord.utils.copy_doc(commands.Cog)
class Cog(commands.Cog):
    """The base class for all cogs.

    This inherits from :class:`discord.ext.commands.Cog` and adds a few more features to it.

    Attributes
    ----------
    bot: Bot
        The bot instance that the cog is attached to.
    __hidden__: bool
        Whether the cog is hidden from the help command.
    emoji: str | discord.PartialEmoji | None
        The emoji that represents the cog.
    """

    __hidden__: ClassVar[bool] = False
    emoji: ClassVar[str | discord.PartialEmoji | None] = None

    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot
