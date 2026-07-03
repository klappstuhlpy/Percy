from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, ClassVar, Literal, NamedTuple, Union

import discord
from discord.ext import commands
from discord.flags import flag_value

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.core import Bot
    from app.core.context import Context

__all__ = (
    "PermissionInput",
    "PermissionSpec",
    "PermissionTemplate",
)

VALID_FLAGS: dict[str, int] = discord.Permissions.VALID_FLAGS

#: Anything that can describe one or more permissions in a decorator.
#:
#: * a raw permission name (``"manage_roles"``) -- discouraged, prefer the forms below,
#: * a :class:`discord.flags.flag_value` descriptor accessed on the class (``discord.Permissions.manage_roles``),
#: * a fully-built :class:`discord.Permissions` (every enabled flag is required),
#: * a :class:`PermissionTemplate` (a reusable, named permission set),
#: * or any iterable mixing the above (e.g. ``[discord.Permissions.connect, discord.Permissions.speak]``).
PermissionInput = Union[
    str,
    flag_value,
    discord.Permissions,
    "PermissionTemplate",
    "Iterable[PermissionInput]",
]


def _canonicalize(name: str) -> set[str]:
    """Resolve a permission name (including aliases) to its canonical flag name(s).

    Discord.py exposes aliases such as ``manage_emojis`` (canonical:
    ``manage_emojis_and_stickers``) and ``manage_permissions`` (canonical:
    ``manage_roles``). :meth:`PermissionSpec.check` compares against the *canonical*
    names yielded when iterating a :class:`discord.Permissions`, so storing an alias
    would make the gate silently never match. Round-tripping through a
    :class:`discord.Permissions` normalises the name.

    Raises
    ------
    ValueError
        If ``name`` is not a known permission flag.
    """
    try:
        perms = discord.Permissions(**{name: True})
    except TypeError:
        raise ValueError(f"Invalid permission: {name!r}") from None
    return {flag for flag, enabled in perms if enabled}


def _iter_permission_names(value: PermissionInput) -> Iterator[str]:
    """Yield the canonical permission name(s) described by ``value``.

    Accepts every form documented on :data:`PermissionInput` and normalises each to
    its canonical flag name. Strings are validated (and de-aliased); ``flag_value``
    descriptors and :class:`discord.Permissions` are expanded into their enabled flags.
    """
    if isinstance(value, str):
        yield from _canonicalize(value)
    elif isinstance(value, flag_value):
        # ``discord.Permissions.manage_roles`` and friends -- a single-bit descriptor.
        yield from (flag for flag, enabled in discord.Permissions(value.flag) if enabled)
    elif isinstance(value, PermissionTemplate):
        yield from value.permissions
    elif isinstance(value, discord.Permissions):
        yield from (flag for flag, enabled in value if enabled)
    elif isinstance(value, Iterable):
        for item in value:
            yield from _iter_permission_names(item)
    else:
        raise TypeError(
            f"Cannot interpret {value!r} as a permission; expected a permission name, "
            "discord.Permissions flag, PermissionTemplate, or an iterable of those."
        )


def _resolve_permissions(value: PermissionInput) -> set[str]:
    """Normalise any :data:`PermissionInput` into a set of canonical permission names."""
    return set(_iter_permission_names(value))


class PermissionTemplate:
    """A reusable, named set of permissions.

    Templates are the preferred way to gate commands: rather than repeating ad-hoc
    string lists at every decorator, a command declares the *role* it requires
    (``PermissionTemplate.mod``, ``PermissionTemplate.manager``, ...). Templates are
    immutable and composable -- combine them with ``|`` to build richer requirements::

        user_permissions=PermissionTemplate.roles | PermissionTemplate.channels

    Each template stores its permissions as *canonical* flag names, so the gate always
    matches what :class:`discord.Permissions` reports for a member.
    """

    __slots__ = ("permissions",)

    permissions: frozenset[str]

    # -- Access-level composites -------------------------------------------------
    #: The baseline permissions the bot needs to respond at all.
    bot: ClassVar[PermissionTemplate]
    #: Full administrator.
    admin: ClassVar[PermissionTemplate]
    #: Server manager (``manage_guild``) -- the general "configure the server" gate.
    manager: ClassVar[PermissionTemplate]
    #: Light moderator: delete messages and ban.
    mod: ClassVar[PermissionTemplate]
    #: Full moderator: kick, ban and delete messages.
    moderator: ClassVar[PermissionTemplate]
    #: Supporter/helper: kick, manage roles and delete messages.
    sup: ClassVar[PermissionTemplate]
    #: Channel setup: manage roles, channels and messages.
    setup: ClassVar[PermissionTemplate]

    # -- Single-permission conveniences (for one-permission user gates) ----------
    kick: ClassVar[PermissionTemplate]
    ban: ClassVar[PermissionTemplate]
    messages: ClassVar[PermissionTemplate]
    roles: ClassVar[PermissionTemplate]
    channels: ClassVar[PermissionTemplate]
    emojis: ClassVar[PermissionTemplate]
    webhooks: ClassVar[PermissionTemplate]
    threads: ClassVar[PermissionTemplate]
    nicknames: ClassVar[PermissionTemplate]
    timeout: ClassVar[PermissionTemplate]

    def __init__(self, *permissions: PermissionInput) -> None:
        object.__setattr__(self, "permissions", frozenset(_resolve_permissions(permissions)))

    def __iter__(self) -> Iterator[str]:
        return iter(self.permissions)

    def __or__(self, other: PermissionInput) -> PermissionTemplate:
        return PermissionTemplate(self, other)

    __ror__ = __or__

    def __contains__(self, item: object) -> bool:
        return item in self.permissions

    def __setattr__(self, key: str, value: object) -> None:  # pragma: no cover - immutability guard
        raise AttributeError(f"PermissionTemplate is immutable; cannot set {key!r} to {value!r}")

    def __repr__(self) -> str:
        return f"PermissionTemplate({', '.join(sorted(self.permissions))})"


# The baseline the bot needs to reply. ``use_external_emojis`` is deliberately *not* here:
# the bot degrades gracefully to unicode when it lacks it (see ``Context.send_*``), so gating
# every command on it would break that fallback. (Before names were canonicalised the alias
# silently never matched, so this was never enforced -- keep that effective behaviour.)
PermissionTemplate.bot = PermissionTemplate(
    "read_message_history", "view_channel", "send_messages", "embed_links"
)
PermissionTemplate.admin = PermissionTemplate("administrator")
PermissionTemplate.manager = PermissionTemplate("manage_guild")
PermissionTemplate.mod = PermissionTemplate("ban_members", "manage_messages")
PermissionTemplate.moderator = PermissionTemplate("kick_members", "ban_members", "manage_messages")
PermissionTemplate.sup = PermissionTemplate("kick_members", "manage_roles", "manage_messages")
PermissionTemplate.setup = PermissionTemplate("manage_roles", "manage_channels", "manage_messages")

PermissionTemplate.kick = PermissionTemplate("kick_members")
PermissionTemplate.ban = PermissionTemplate("ban_members")
PermissionTemplate.messages = PermissionTemplate("manage_messages")
PermissionTemplate.roles = PermissionTemplate("manage_roles")
PermissionTemplate.channels = PermissionTemplate("manage_channels")
PermissionTemplate.emojis = PermissionTemplate("manage_emojis")
PermissionTemplate.webhooks = PermissionTemplate("manage_webhooks")
PermissionTemplate.threads = PermissionTemplate("manage_threads")
PermissionTemplate.nicknames = PermissionTemplate("manage_nicknames")
PermissionTemplate.timeout = PermissionTemplate("moderate_members")


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

        Both sets are fresh copies: ``PermissionTemplate.bot`` is shared, so aliasing it
        here would let one command's ``bot_permissions`` leak into every other command
        (and into the template itself) via the in-place ``update``.
        """
        return cls(user=set(), bot=set(PermissionTemplate.bot))

    def update(
        self,
        permissions: PermissionInput,
        destination: Literal["user", "bot"],
    ) -> None:
        """Add the given permissions to ``destination``.

        ``permissions`` accepts any :data:`PermissionInput`: a
        :class:`PermissionTemplate`, :class:`discord.Permissions` flag(s)
        (``discord.Permissions.manage_roles``), a fully-built :class:`discord.Permissions`,
        a raw permission name, or an iterable mixing those. Everything is normalised to
        canonical flag names before being stored.
        """
        resolved = _resolve_permissions(permissions)

        if destination == "user":
            self.user.update(resolved)
            return

        self.bot.update(resolved)

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
