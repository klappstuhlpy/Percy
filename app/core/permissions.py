from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal, NamedTuple

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.core import Bot
    from app.core.context import Context

__all__ = (
    "PermissionSpec",
    "PermissionTemplate",
)


class PermissionTemplate:
    """Permission Templates for the bot and user.

    This implements basic permission sets for easy access to permissions.
    """

    bot: ClassVar[set[str]] = {"read_message_history", "view_channel", "send_messages", "embed_links", "use_external_emojis"}
    mod: ClassVar[set[str]] = {"ban_members", "manage_messages"}
    sup: ClassVar[set[str]] = {"kick_members", "manage_roles", "manage_messages"}
    setup: ClassVar[set[str]] = {"manage_roles", "manage_channels", "manage_messages"}
    admin: ClassVar[set[str]] = {"administrator"}
    manager: ClassVar[set[str]] = {"manage_guild"}


VALID_FLAGS: dict[str, int] = discord.Permissions.VALID_FLAGS


class PermissionSpec(NamedTuple):
    """Represents permissions specifications that includes the bot's and user's permissions for a command.

    Notes
    -----
    A PermissionSpec object must be initialized with the `new` method.

    Attributes
    ----------
    user: set[str]
        The permissions required by the user.
    bot: set[str]
        The permissions required by the bot.
    """

    user: set[str]
    bot: set[str]

    @classmethod
    def new(cls) -> PermissionSpec:
        """Creates a new permission spec.

        Users default to requiring no permissions.
        Bots default to requiring Read Message History, View Channel, Send Messages, Embed Links, and External Emojis permissions.

        Both sets are fresh copies: ``PermissionTemplate.bot`` is class-level and mutable,
        so aliasing it here would let one command's ``bot_permissions`` leak into every
        other command (and into the template itself) via the in-place ``update``.
        """
        return cls(user=set(), bot=set(PermissionTemplate.bot))

    def update(
        self,
        permissions: Iterable[str | discord.Permissions],
        destination: Literal["user", "bot"],
    ) -> None:
        """Updates the permissions of the given destination."""
        false = [permission for permission in permissions if permission not in VALID_FLAGS]
        if false:
            raise ValueError(f"Invalid permission(s): {', '.join(false)}")

        if destination == "user":
            self.user.update(permissions)
            return

        self.bot.update(permissions)

    @staticmethod
    def permission_as_str(permission: str) -> str:
        """Takes the attribute name of a permission and turns it into a capitalized, readable one."""
        return permission.title().replace("_", " ").replace("Tts", "TTS").replace("Guild", "Server")

    @staticmethod
    def _is_owner(bot: Bot, user: discord.User | discord.Member) -> bool:
        """Checks if the given user is the owner of the bot."""
        if bot.owner_id:
            return user.id == bot.owner_id

        elif bot.owner_ids:
            return user.id in bot.owner_ids

        return False

    def check(self, ctx: Context) -> bool:
        """Checks if the given context meets the required permissions."""
        if ctx.bot.bypass_checks or self._is_owner(ctx.bot, ctx.author):
            return True

        user = ctx.permissions
        missing = [perm for perm, value in user if perm in self.user and not value]

        if missing and not user.administrator:
            raise commands.MissingPermissions(missing)

        bot = ctx.bot_permissions
        missing = [perm for perm, value in bot if perm in self.bot and not value]

        if missing and not bot.administrator:
            raise commands.BotMissingPermissions(missing)

        return True
