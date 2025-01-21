from __future__ import annotations

import copy
import functools
import io
import re
from collections import OrderedDict
from contextlib import suppress
from functools import wraps
from typing import (
    Any,
    Callable,
    ClassVar,
    Generic,
    Literal,
    NamedTuple,
    ParamSpec,
    TYPE_CHECKING,
    TypeVar,
    Iterable,
    overload,
    Protocol,
    override,
    Type,
    Union,
    runtime_checkable
)

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import GroupMixin
from discord.utils import MISSING, cached_property

from app.core.flags import ConsumeUntilFlag, FlagMeta, Flags
from app.core.views import ConfirmationView, DisambiguatorView
from app.utils import AnsiColor, AnsiStringBuilder, helpers, truncate
from app.utils import TemporaryAttribute, AsyncCallable
from config import Emojis

if TYPE_CHECKING:
    from datetime import datetime
    from typing_extensions import Self

    from app.core import Bot, FlagNamespace
    from app.database import Database

    P = ParamSpec('P')
    R = TypeVar('R')

CogT = TypeVar('CogT', bound='Cog')
T = TypeVar('T')

__all__ = (
    'HybridContext',
    'BadArgument',
    'AppBadArgument',
    'EmbedBuilder',
    'Cog',
    'CogT',
    'Command',
    'HybridCommand',
    'HybridGroupCommand',
    'GroupCommand',
    'Context',
    'PermissionSpec',
    'PermissionTemplate',
    'command',
    'group',
    'cooldown',
    'user_max_concurrency',
    'guild_max_concurrency',
    'guilds',
    'describe',
)


async def _dummy_context(ctx: Context) -> None:  # noqa
    pass


class AppBadArgument(app_commands.AppCommandError):
    """The base exception for all application command argument errors."""

    def __init__(self, message: str, namespace: str | None = None, /) -> None:
        self.namespace: str = namespace
        super().__init__(message)


class BadArgument(commands.BadArgument):
    """The base exception for all command argument errors.

    Using the `namespace` parameter, the name of a parameter will be passed down to the final error handler
    to specify the parameter of the command that should be highlighted responsible for the error.

    If the parameter is found in the command, this overrides the `Context.current_parameter` value.

    Note: The parsing is handled in the final error handler.
    """

    def __init__(self, message: str, namespace: str | None = None, /) -> None:
        self.namespace: str = namespace
        super().__init__(message)


class EmbedBuilder(discord.Embed):
    """A subclass of :class:`discord.Embed` that adds a few more features to it.

    This is used to provide a more fluent interface for creating embeds.
    """

    @override
    def __init__(
            self,
            *,
            colour: helpers.Colour | int | None = helpers.Colour.white(),
            timestamp: datetime | None = None,
            fields: Iterable[tuple[str, str, bool]] | list[dict[str, str | bool]] = (),
            **kwargs: Any,
    ) -> None:
        super().__init__(colour=colour, timestamp=timestamp, **kwargs)
        if fields:
            self.add_fields(fields)

        self.description: str = kwargs.get('description', '')

    @staticmethod
    def _resolve_field_dicts(
            fields: Iterable[tuple[str, str, bool]] | list[dict[str, str | bool]]
    ) -> Iterable[tuple[str, str, bool]]:
        first_item_checker = type(next(iter(fields), None))
        if first_item_checker is dict:
            return [(field['name'], field['value'], field['inline']) for field in fields]
        return fields

    def add_fields(self, fields: Iterable[tuple[str, str, bool]] | list[dict[str, str | bool]]) -> EmbedBuilder:
        """Adds multiple fields to the embed.

        Parameters
        ----------
        fields: tuple[str, str, bool]
            The fields to add to the embed.

        Returns
        -------
        `EmbedBuilder`
            The embed builder.
        """
        for name, value, inline in self._resolve_field_dicts(fields):
            self.add_field(name=name, value=value, inline=inline)
        return self

    @classmethod
    def to_factory(cls: Type[Self], embed: discord.Embed, **kwargs: Any) -> Self:
        """Create a new embed from an existing embed.

        Parameters
        ----------
        embed: `discord.Embed`
            The embed to copy from.
        **kwargs: `Any`
            Additional keyword arguments to pass to the embed builder.

        Returns
        -------
        `EmbedBuilder`
            The new embed builder.
        """
        copied_embed = copy.copy(embed)
        copied_embed.colour = helpers.Colour(copied_embed.colour.value)

        return cls.from_dict(copied_embed.to_dict(), **kwargs)

    @classmethod
    def from_message(
            cls,
            message: discord.Message,
            **kwargs: Any,
    ) -> Self:
        """Create a new embed from a message.

        Parameters
        ----------
        message: `discord.Message`
            The message to create the embed from.
        **kwargs: `Any`
            Additional keyword arguments to pass to the embed builder.

        Returns
        -------
        `EmbedBuilder`
            The new embed builder.
        """
        if embeds := message.embeds:
            return cls.to_factory(embeds[0], **kwargs)

        author: discord.User | discord.Member = message.author
        instance = cls(**kwargs)

        instance.description = message.content
        instance.set_author(name=author.display_name, icon_url=author.display_avatar)

        if (
                message.attachments
                and message.attachments[0].content_type
                and message.attachments[0].content_type.startswith("image")
        ):
            instance.set_image(url=message.attachments[0].url)

        return instance

    @classmethod
    def factory(cls, ctx: Context | discord.Interaction) -> Self:
        """Factory function to create an embed instance from a context or interaction.

        Parameters
        ----------
        ctx: `Context` | `discord.Interaction`
            The context or interaction to create the embed from.

        Returns
        -------
        `EmbedBuilder`
            The new embed builder.
        """
        if ctx.is_interaction:
            _origin = ctx.interaction.message.embeds[0] if ctx.interaction.message.embeds else None
        else:
            _origin = ctx.message.embeds[0] if ctx.message.embeds else None

        if _origin:
            return cls.to_factory(_origin)
        return cls()

    def build(self) -> Self:
        """Returns a shallow copy of the embed.

        Returns
        -------
        `EmbedBuilder`
            The shallow copy of the embed.
        """
        return copy.copy(self)


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


class PermissionTemplate:
    """Permission Templates for the bot and user.

    This implements basic permission sets for easy access to permissions.
    """

    bot: ClassVar[set[str]] = {'read_message_history', 'view_channel', 'send_messages', 'embed_links',
                               'use_external_emojis'}
    mod: ClassVar[set[str]] = {'ban_members', 'manage_messages'}
    admin: ClassVar[set[str]] = {'administrator'}
    manager: ClassVar[set[str]] = {'manage_guild'}


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
        """
        return cls(user=set(), bot=PermissionTemplate.bot)

    def update(
            self,
            permissions: Iterable[str],
            destination: Literal['user', 'bot'],
    ):
        """Updates the permissions of the given destination."""
        false = [permission for permission in permissions if permission not in VALID_FLAGS]
        if false:
            raise ValueError(f'Invalid permission(s): {", ".join(false)}')

        if destination == 'user':
            return self.user.update(permissions)
        self.bot.update(permissions)

    @staticmethod
    def permission_as_str(permission: str) -> str:
        """Takes the attribute name of a permission and turns it into a capitalized, readable one."""
        return (
            permission.title()
            .replace('_', ' ')
            .replace('Tts', 'TTS')
            .replace('Guild', 'Server')
        )

    @staticmethod
    def _is_owner(bot: Bot, user: discord.User) -> bool:
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


class ParamInfo(NamedTuple):
    """Parameter information.

    Parameters
    ----------
    name: str
        The name of the parameter.
    required: bool
        Whether the parameter is required.
    default: Any
        The default value of the parameter.
    greedy: bool
        Whether the parameter is greedy.
    choices: list[str | int | bool] | None
        The choices of the parameter.
    show_default: bool
        Whether the default value should be shown.
    flag: bool
        Whether the parameter is a flag.
    store_true: bool
        Whether the parameter should store a boolean value.
    """

    name: str
    required: bool
    default: Any
    greedy: bool
    choices: list[str | int | bool] | None
    show_default: bool
    flag: bool
    store_true: bool

    def is_flag(self) -> bool:
        return self.flag


@discord.utils.copy_doc(commands.Command)
class Command(commands.Command):
    """The base class for all commands.

    This inherits from :class:`discord.ext.commands.Command` and adds a few more features to it.

    This supports custom permission specifications and extended flag parameters to support
    the :class:`app.core.flags.Flags` class with special consume until flag keyword-only parameters
    and store-true flags for text commands (this supports a boolean typed parameter implemention for app_commands).

    Attributes
    ----------
    custom_flags: FlagMeta | None
        The custom flags class for the command.
    """

    def __init__(self, func: AsyncCallable[..., Any], **kwargs: Any) -> None:
        self._permissions: PermissionSpec = PermissionSpec.new()
        if user_permissions := kwargs.pop('user_permissions', {}):
            self._permissions.update(user_permissions, 'user')

        if bot_permissions := kwargs.pop('bot_permissions', {}):
            self._permissions.update(bot_permissions, 'bot')

        self.custom_flags: FlagMeta[Any] | None = None

        super().__init__(func, **kwargs)
        self.add_check(self._permissions.check)

    @property
    def permissions(self) -> PermissionSpec:
        """:class:`PermissionSpec` : Return the permission specification for this command."""
        return self._permissions

    def _ensure_assignment_on_copy(self, other: Command) -> Command:
        super()._ensure_assignment_on_copy(other)

        other._permissions = self._permissions
        other.custom_flags = self.custom_flags
        return other

    async def can_run(self, ctx: Context, /) -> bool:
        """Checks if the command can be run in the given context.

        This overrides the default implementation to support early command abortion
        if the command is restricted to certain guilds.

        This still calls the original implementation to check if the command can be run.
        """
        guild_ids_check = getattr(self.callback, '__guild_ids__', None)
        if guild_ids_check:
            if ctx.guild and ctx.guild.id not in guild_ids_check:
                return False
        return await super().can_run(ctx)

    @property
    def parents(self) -> list[GroupMixin[Any]]:
        """list[GroupMixin[Any]] : Returns all parent commands of this command.

        This is sorted by the length of :attr:`.qualified_name` from highest to lowest.
        If the command has no parents, this will be an empty list.
        """
        cmd = self.parent
        entries = []
        while cmd is not None:
            entries.append(cmd)
            cmd = cmd.parent
        return sorted(entries, key=lambda x: len(x.qualified_name), reverse=True)

    def transform_flag_parameters(self) -> None:
        """Transforms a with a subclass of `Flags` annotated parameter
        in the command signature into a valid flag parameter.

        This is used to support the :class:`.Flags` class and its special parameters.
        This supports transformation for consume-until-flag keyword-only parameters and store-true flags.

        Notes
        -----
        This method needs to be called before the command is finnaly added to the bot to ensure the correct
        parameter transformation.
        """
        first_consume_rest: str | None = None

        for name, param in self.params.items():
            if param.kind is not param.KEYWORD_ONLY:
                continue

            try:
                is_flags = issubclass(param.annotation, Flags)
            except TypeError:
                is_flags = False

            if is_flags:
                self.custom_flags = param.annotation
                try:
                    default = self.custom_flags.default
                except ValueError:
                    pass
                else:
                    self.params[name] = param.replace(default=default)

                if not first_consume_rest:
                    break

                target = self.params[first_consume_rest]
                default = MISSING if target.default is param.empty else target.default
                annotation = None if target.annotation is param.empty else target.annotation

                self.params[first_consume_rest] = target.replace(
                    annotation=ConsumeUntilFlag(annotation, default),
                    kind=param.POSITIONAL_OR_KEYWORD,
                )
                break

            elif not first_consume_rest:
                first_consume_rest = name

        if first_consume_rest and self.custom_flags:  # A kw-only has been transformed into a pos-or-kw, reverse this here
            @wraps(original := self.callback)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                """A wrapper to reverse the transformation of the first consume rest parameter."""
                idx = 2 if self.cog else 1

                for i, (arg, (k, v)) in enumerate(zip(args[idx:], self.params.items())):
                    if k == first_consume_rest:
                        args = args[:i + idx]
                        kwargs[k] = arg
                        break

                return await original(*args, **kwargs)

            self._callback = wrapper

    @classmethod
    def ansi_signature_of(cls, command: Command, /) -> AnsiStringBuilder:
        """Returns the ANSI signature of the given command.

        This temporarily sets the `custom_flags` attribute to `None` if the Command is not an instance of our
        custom command class to avoid raising AttributeErrors.
        """
        if isinstance(command, cls):
            return command.ansi_signature  # type: ignore

        with TemporaryAttribute(command, attr='custom_flags', value=None):
            return cls.ansi_signature.fget(command)

    @staticmethod
    def _disect_param(param: commands.Parameter) -> tuple:
        """Disects a parameter into it's annotation, greedy, optional, and origin.

        This is basically a separate implemention of the original method in the `commands.Command` class
        to support the `app.core.flags.Flags` class and it's special parameters.
        """
        greedy = isinstance(param.annotation, commands.Greedy)
        optional = False

        # for typing.Literal[...], typing.Optional[typing.Literal[...]], and Greedy[typing.Literal[...]], the
        # parameter signature is a literal list of it's values
        annotation = param.annotation.converter if greedy else param.annotation
        origin = getattr(annotation, '__origin__', None)
        if not greedy and origin is Union:
            none_cls = type(None)
            union_args = annotation.__args__
            optional = union_args[-1] is none_cls

            if len(union_args) == 2 and optional:
                annotation = union_args[0]
                origin = getattr(annotation, '__origin__', None)

        return annotation, greedy, optional, origin

    @property
    def param_info(self) -> OrderedDict[str, ParamInfo]:
        """Returns a dict mapping parameter names to their rich info.

        This turns the parameters of the command into a rich info dict that includes the parameter name, whether it's
        required, the default value, the choices, whether the default value should be shown, and whether the parameter
        is a flag or not.

        This supports our custom flag behavior and the `app.core.flags.Flags` class.
        """
        result = OrderedDict()
        params = self.clean_params
        if not params:
            return result

        for name, param in params.items():
            annotation, greedy, optional, origin = Command._disect_param(param)
            default = param.default

            if isinstance(annotation, FlagMeta) and self.custom_flags:
                for flag in self.custom_flags.walk_flags():
                    optional = not flag.required
                    name = '--' + flag.name
                    default = param.empty

                    if not flag.store_true and flag.default or flag.default is False:
                        default = flag.default
                        optional = True

                    result[name] = ParamInfo(
                        name=name,
                        required=not optional,
                        show_default=bool(flag.default) or flag.default is False,
                        default=default,
                        choices=None,
                        greedy=greedy,
                        flag=True,
                        store_true=flag.store_true,
                    )
                continue

            choices = annotation.__args__ if origin is Literal else None

            if default is not param.empty:
                show_default = bool(default) if isinstance(default, str) else default is not None
                optional = True
            else:
                show_default = False

            if param.kind is param.VAR_POSITIONAL:
                optional = not self.require_var_positional
            elif param.default is param.empty:
                optional = optional or greedy

            result[name] = ParamInfo(
                name=name,
                required=not optional,
                show_default=show_default,
                default=default,
                choices=choices,
                greedy=greedy,
                flag=False,
                store_true=False,
            )

        return result

    @property
    def ansi_signature(self) -> AnsiStringBuilder:
        """Returns an ANSI builder for the signature of this command.

        This custom property returns a fully markdownified signature of the command in ANSI format.
        """
        if self.usage is not None:
            return AnsiStringBuilder.from_string(self.usage)

        params = self.clean_params
        result = AnsiStringBuilder()
        if not params:
            return result

        for name, param in params.items():
            annotation, greedy, optional, origin = Command._disect_param(param)

            if isinstance(annotation, FlagMeta) and self.custom_flags:
                if annotation.__commands_flag_compress_usage__:
                    required = any(flag.required for flag in self.custom_flags.walk_flags())
                    start, end = '<>' if required else '[]'
                    result.append(start, color=AnsiColor.gray, bold=True)
                    result.append(name + '...', color=AnsiColor.yellow if required else AnsiColor.blue)
                    result.append(end + ' ', color=AnsiColor.gray, bold=True)
                    continue

                for flag in self.custom_flags.walk_flags():
                    start, end = '<>' if flag.required else '[]'
                    base = '--' + flag.name

                    result.append(start, bold=True, color=AnsiColor.gray)
                    result.append(base, color=AnsiColor.yellow if flag.required else AnsiColor.blue)

                    if not flag.store_true:
                        result.append(' <', color=AnsiColor.gray, bold=True)
                        result.append(flag.dest, color=AnsiColor.magenta)

                        if flag.default or flag.default is False:
                            result.append('=', color=AnsiColor.gray)
                            result.append(str(flag.default), color=AnsiColor.cyan)

                        result.append('>', color=AnsiColor.gray, bold=True)

                    result.append(end + ' ', color=AnsiColor.gray, bold=True)

                continue

            if origin is Literal:
                name = '|'.join(f'"{v}"' if isinstance(v, str) else str(v) for v in annotation.__args__)

            if param.default is not param.empty:
                # We don't want None or '' to trigger the [name=value] case, and instead it should
                # do [name] since [name=None] or [name=] are not exactly useful for the user.
                should_print = param.default if isinstance(param.default, str) else param.default is not None
                result.append('[', color=AnsiColor.gray, bold=True)
                result.append(name, color=AnsiColor.blue)

                if should_print:
                    result.append('=', color=AnsiColor.gray, bold=True)
                    result.append(str(param.default), color=AnsiColor.cyan)
                    extra = '...' if greedy else ''
                else:
                    extra = ''

                result.append(']' + extra + ' ', color=AnsiColor.gray, bold=True)
                continue

            elif param.kind == param.VAR_POSITIONAL:
                if self.require_var_positional:
                    start = '<'
                    end = '...>'
                else:
                    start = '['
                    end = '...]'

            elif greedy:
                start = '['
                end = ']...'

            elif optional:
                start, end = '[]'
            else:
                start, end = '<>'

            result.append(start, color=AnsiColor.gray, bold=True)
            result.append(name, color=AnsiColor.blue if start == '[' else AnsiColor.yellow)
            result.append(end + ' ', color=AnsiColor.gray, bold=True)

        return result


@discord.utils.copy_doc(commands.Context)
class Context(commands.Context, Generic[CogT]):

    if TYPE_CHECKING:
        bot: Bot
        cog: CogT
        command: Command | GroupCommand
        invoked_subcommand: Command | GroupCommand | None

    def __init__(self, **attrs) -> None:
        self._message: discord.Message | None = None
        super().__init__(**attrs)

    @property
    def session(self) -> aiohttp.ClientSession:
        """:class:`aiohttp.ClientSession`: The session for the bot"""
        return self.bot.session

    @property
    def user(self) -> discord.Member:
        """Alias for :attr:`author`."""
        return self.author

    @property
    def client(self) -> Bot:
        """Alias for :attr:`bot`."""
        return self.bot

    @property
    def guild_id(self) -> int:
        """Alias for :attr:`guild.id`."""
        return self.guild.id

    @property
    def db(self) -> Database:
        """The database instance for the current context."""
        return self.bot.db

    @property
    def now(self) -> datetime:
        """Returns when the message of this context was created at."""
        return self.message.created_at

    @cached_property
    def flags(self) -> Flags:
        """The flag arguments passed.

        Only available if the flags were a keyword argument.
        """
        return discord.utils.find(lambda v: isinstance(v, FlagNamespace), self.kwargs.values())

    @staticmethod
    def utcnow() -> datetime:
        """A shortcut for :func:`discord.utils.utcnow`."""
        return discord.utils.utcnow()

    @property
    def clean_prefix(self) -> str:
        """This is preferred over the base implementation as I feel like regex, which was used in the base implementation, is simply unnecessary for this."""
        if self.prefix is None:
            return ''

        user = self.bot.user
        MENTIONED_REGEX = re.compile(rf'<@!?{user.id}>')
        return MENTIONED_REGEX.sub(f'@{user.name}', self.prefix)

    @property
    def is_interaction(self) -> bool:
        """Whether an interaction is attached to this context."""
        return self.interaction is not None

    @discord.utils.cached_property
    def replied_reference(self) -> discord.MessageReference | None:
        ref = self.message.reference
        if ref and isinstance(ref.resolved, discord.Message):
            return ref.resolved.to_reference()
        return None

    @discord.utils.cached_property
    def replied_message(self) -> discord.Message | None:
        ref = self.message.reference
        if ref and isinstance(ref.resolved, discord.Message):
            return ref.resolved
        return None

    async def confirm(
            self,
            content: str = None,
            *,
            view: ConfirmationView = None,
            user: discord.Member | discord.User = None,
            timeout: float = 60.,
            true: str = 'Yes',
            false: str = 'No',
            interaction: discord.Interaction = None,
            hook: AsyncCallable[[discord.Interaction], None] = None,
            **kwargs,
    ) -> bool | None:
        """|coro|

        Sends a ConfirmationView or a custom view that waits for a interaction and returns a parameter called `value`.

        Parameters
        ----------
        content: str
            The content to send with the view.
        view: ConfirmationView
            The view to use for the confirmation.
        user: discord.Member | discord.User
            The user to send the confirmation to.
        timeout: float
            The timeout for the confirmation.
        true: str
            The string to use for the true value.
        false: str
            The string to use for the false value.
        interaction: discord.Interaction
            The interaction to use for the confirmation.
        hook: Callable[[discord.Interaction], None]
            A hook to call when the interaction is received.
            This gets passed to the ConfirmationView and is handled there, it takes
            exactly one argument which is the interaction.
        **kwargs
            Additional keyword arguments to pass to the send method.
        """
        author = user or self.author
        view = view or ConfirmationView(
            author,
            true=true,
            false=false,
            hook=hook,
            timeout=timeout
        )

        if interaction is not None:
            await interaction.response.send_message(content, view=view, **kwargs)
            await view.wait()
            return view.value

        view.message = await self.send(content, view=view, **kwargs)

        await view.wait()
        with suppress(discord.HTTPException):
            await view.message.delete()
        return view.value

    async def disambiguate(self, matches: list[T], entry: Callable[[T], Any], *, ephemeral: bool = False) -> T:
        if len(matches) == 0:
            raise ValueError('No results found.')

        if len(matches) == 1:
            return matches[0]

        if len(matches) > 25:
            raise ValueError('Too many results... sorry.')

        view = DisambiguatorView(self, matches, entry)
        view.message = await self.send_info(
            'There are too many matches... Please specify your choice by selecting a result.', view=view,
            ephemeral=ephemeral
        )
        await view.wait()
        return view.selected

    async def send_success(self, content: str, **kwargs: Any) -> discord.Message:
        """Sends a success message."""
        emoji = Emojis.success if self.bot_permissions.use_external_emojis else '\N{WHITE HEAVY CHECK MARK}'
        return await self.send(f'{emoji} {content}', **kwargs)

    async def send_error(self, content: str, **kwargs: Any) -> discord.Message:
        """Sends an error message."""
        kwargs.setdefault('delete_after', 15)
        kwargs.setdefault('ephemeral', True)
        kwargs.setdefault('reference', self.message)
        emoji = Emojis.error if self.bot_permissions.use_external_emojis else '\N{CROSS MARK}'
        return await self.send(f'{emoji} {content}', **kwargs)

    async def send_info(self, content: str, **kwargs: Any) -> discord.Message:
        """Sends an info message."""
        emoji = Emojis.info if self.bot_permissions.use_external_emojis else '\N{INFORMATION SOURCE}'
        return await self.send(f'{emoji} {content}', **kwargs)

    async def send_warning(self, content: str, **kwargs: Any) -> discord.Message:
        """Sends a warning message."""
        kwargs.setdefault('delete_after', 15)
        kwargs.setdefault('ephemeral', True)
        kwargs.setdefault('reference', self.message)
        emoji = Emojis.warning if self.bot_permissions.use_external_emojis else '\N{WARNING SIGN}'
        return await self.send(f'{emoji} {content}', **kwargs)

    async def send(self, content: Any = None, **kwargs: Any) -> discord.Message:
        if kwargs.get('embed') and kwargs.get('embeds') is not None:
            kwargs['embeds'].append(kwargs['embed'])
            del kwargs['embed']

        if kwargs.get('file') and kwargs.get('files') is not None:
            kwargs['files'].append(kwargs['file'])
            del kwargs['file']

        if kwargs.pop('edit', False) and self._message:
            kwargs.pop('files', None)
            kwargs.pop('reference', None)

            await self.maybe_edit(content, **kwargs)
            return self._message

        if self.is_interaction and not self.interaction.is_expired() and not self.interaction.response.is_done():
            # If there is a pending interaction from maybe a hybrid app command left, we should use that instead
            kwargs.pop('reference', None)
            kwargs.pop('mention_author', None)
            kwargs.pop('nonce', None)
            kwargs.pop('stickers', None)
            self._message = result = await self.interaction.response.send_message(content, **kwargs)
        else:
            self._message = result = await super().send(content, **kwargs)
        return result

    async def maybe_edit(self, message: discord.Message | None = None, content: Any = None, **kwargs: Any) -> discord.Message | None:
        """Edits the message silently."""
        message = message or self._message
        try:
            await message.edit(content=content, **kwargs)
        except (AttributeError, discord.NotFound):
            if not message or message.channel == self.channel:
                return await self.send(content, **kwargs)

            return await message.channel.send(content, **kwargs)

    async def maybe_delete(self, message: discord.Message | None = None, *args: Any, **kwargs: Any) -> None:
        """Deletes the message silently if it exists."""
        message = message or self._message
        try:
            await message.delete(*args, **kwargs)
        except (AttributeError, discord.NotFound, discord.Forbidden):
            pass

    async def defer(self, *, ephemeral: bool = False, typing: bool = False) -> None:
        """Defers the response of the interaction or starts typing if it's a regular message."""
        if self.is_interaction and not self.interaction.is_expired() and not self.interaction.response.is_done():
            await self.interaction.response.defer(ephemeral=ephemeral)
        else:
            if typing:
                await self.typing()

    async def safe_send(self, content: str, *, escape_mentions: bool = True, **kwargs) -> discord.Message:
        if escape_mentions:
            content = discord.utils.escape_mentions(content)

        if len(content) > 2000:
            fp = io.BytesIO(content.encode())
            kwargs.pop('file', None)
            return await self.send(file=discord.File(fp, filename='message_too_long.txt'), **kwargs)
        else:
            return await self.send(content)


class _app_command_override(app_commands.Command):
    """An override for the application command class to support the hybrid command implemention.

    This is used to ensure that the application command is properly copied over to the hybrid command.
    """
    def copy(self) -> Self:
        """Ensure the app command is properly copied."""
        bindings = {
            self.binding: self.binding,
        }
        return self._copy_with(
            parent=self.parent,
            binding=self.binding,
            bindings=bindings,
            set_on_binding=False,
        )


ContextT = TypeVar('ContextT', bound=Context)


@runtime_checkable
class HybridContextProtocol(Protocol[ContextT]):
    """Protocol to match the :class:`.Context` class for hybrid command implementions."""

    async def full_invoke(self, *args: P.args, **kwargs: P.kwargs) -> Any:
        """|coro|

        Fully invokes the command with the given arguments and keyword arguments.

        Notes
        -----
        The full invoke function for the command, used to invoke the parent command implemention.
        The passed arguments must follow exactly the same signature as the command's hybrid callback.

        `self` and `ctx` parameter are automatically added to the arguments.

        Parameters
        ----------
        args: Any
            The arguments to pass to the command.
        kwargs: Any
            The keyword arguments to pass to the command.
        """
        ...


class HybridContext(Context, HybridContextProtocol):
    """A Context type especially for application command implementions
    that were defined by using the :func:`.define_app_command()` decorator.

    This can only be used on application commands that derive from hybrid commands and are defined seperately.

    Attributes
    ----------
    interaction: discord.Interaction
        The interaction that triggered the command.
    """
    interaction: discord.Interaction


def define_app_command_impl(
        source: HybridCommand | HybridGroupCommand,
        cls: type[app_commands.Command | app_commands.Group],
        **kwargs: Any,
) -> Callable[[AsyncCallable[..., Any]], None]:
    def decorator(func: AsyncCallable | AsyncCallable[..., Any]) -> None:
        @functools.wraps(func)
        async def wrapper(self: Cog, inter: discord.Interaction, *args: Any, **kwds: Any) -> Any:
            source.cog = self
            ctx = await self.bot.get_context(inter)
            ctx.command = source

            async def invoker(*iargs: P.args, **ikwargs: P.kwargs) -> Any:
                ctx.args = [ctx.cog, ctx, *iargs]
                ctx.kwargs = ikwargs

                with TemporaryAttribute(ctx.command, '_parse_arguments', _dummy_context):
                    return await ctx.bot.invoke(ctx)

            ctx.full_invoke = invoker
            ctx.interaction = inter
            return await func(self, ctx, *args, **kwds)

        wrapper.__globals__.update(func.__globals__)  # type: ignore
        source.app_command = cls(
            name=source.name,
            # description cant be none!
            description=source.short_doc or truncate(source.description, 100),
            parent=source.parent.app_command if isinstance(source.parent, HybridGroupCommand) else None,
            callback=wrapper,
            **kwargs,
        )

        @source.app_command.error
        async def on_error(_, interaction: discord.Interaction, error: BaseException) -> None:
            interaction.client.dispatch('command_error', interaction._baton, error)

    return decorator


@discord.utils.copy_doc(commands.HybridCommand)
class HybridCommand(Command, commands.HybridCommand):
    def define_app_command(self, **kwargs: Any) -> Callable[[AsyncCallable[..., Any]], None]:
        """Define an application command for this hybrid command."""
        return define_app_command_impl(self, _app_command_override, **kwargs)


@discord.utils.copy_doc(commands.Group)
class GroupCommand(commands.Group, Command):
    @discord.utils.copy_doc(commands.Group.command)
    def command(self, *args: Any, **kwargs: Any) -> Callable[..., Command]:
        def decorator(func: AsyncCallable[..., Any]) -> Command:
            _resolve_kwargs_inheritance(kwargs, self)
            result = command(*args, **kwargs)(func)
            self.add_command(result)  # type: ignore
            return result

        return decorator

    @discord.utils.copy_doc(commands.Group.group)
    def group(self, *args: Any, **kwargs: Any) -> Callable[..., GroupCommand]:
        def decorator(func: AsyncCallable[..., Any]) -> GroupCommand:
            _resolve_kwargs_inheritance(kwargs, self)
            result = group(*args, **kwargs)(func)
            self.add_command(result)  # type: ignore
            return result

        return decorator


@discord.utils.copy_doc(commands.HybridGroup)
class HybridGroupCommand(GroupCommand, commands.HybridGroup):
    def define_app_command(self, **kwargs: Any) -> Callable[[AsyncCallable[..., Any]], None]:
        return define_app_command_impl(self, app_commands.Group, **kwargs)

    def copy(self) -> Self:
        _copy = super().copy()
        # Ensure app commands are properly copied over
        if self.app_command is not None:
            children = _copy.app_command._children
            for key, cmd in self.app_command._children.items():
                if key in children:
                    continue
                children[key] = cmd

        return _copy


CommandInstance = Command | HybridCommand | HybridGroupCommand | GroupCommand


# noinspection PyShadowingBuiltins
def _resolve_command_kwargs(
        cls: type,
        *,
        name: str = MISSING,
        alias: str = MISSING,
        aliases: Iterable[str] = MISSING,
        usage: str = MISSING,
        brief: str = MISSING,
        help: str = MISSING,
) -> dict[str, Any]:
    kwargs = {'cls': cls}

    if name is not MISSING:
        kwargs['name'] = name

    if alias is not MISSING and aliases is not MISSING:
        raise TypeError('cannot have alias and aliases kwarg filled')

    if alias is not MISSING:
        kwargs['aliases'] = (alias,)

    if aliases is not MISSING:
        kwargs['aliases'] = tuple(aliases)

    if usage is not MISSING:
        kwargs['usage'] = usage

    if brief is not MISSING:
        kwargs['brief'] = brief

    if help is not MISSING:
        kwargs['help'] = help

    return kwargs


def _resolve_kwargs_inheritance(new: dict[str, Any], parent: GroupCommand):
    new.setdefault('guild_only', parent.__original_kwargs__.get('guild_only', False))
    new.setdefault('parent', parent)
    new.setdefault('hybrid', isinstance(parent, (HybridGroupCommand, HybridCommand)))
    new.setdefault('hidden', parent.hidden)
    return new


@overload
def command(
        name: str = MISSING,
        *,
        alias: str = MISSING,
        aliases: Iterable[str] = MISSING,
        usage: str = MISSING,
        brief: str = MISSING,
        help: str = MISSING,
        examples: list[str] = MISSING,
        hybrid: Literal[True] = False,
        guild_only: Literal[True] = False,
        nsfw: Literal[True] = False,
        **other_kwargs: Any,
) -> Callable[..., HybridCommand]:
    ...


@overload
def command(
        name: str = MISSING,
        *,
        alias: str = MISSING,
        aliases: Iterable[str] = MISSING,
        usage: str = MISSING,
        brief: str = MISSING,
        help: str = MISSING,
        examples: list[str] = MISSING,
        hybrid: Literal[False] = False,
        guild_only: Literal[False] = False,
        nsfw: Literal[False] = False,
        **other_kwargs: Any,
) -> Callable[..., Command]:
    ...


def command(
        name: str = MISSING,
        *,
        alias: str = MISSING,
        aliases: Iterable[str] = MISSING,
        usage: str = MISSING,
        brief: str = MISSING,
        help: str = MISSING,
        examples: list[str] = MISSING,
        hybrid: bool = False,
        guild_only: bool = False,
        nsfw: bool = False,
        **other_kwargs: Any,
) -> Callable[..., Command | HybridCommand]:
    """A decorator that turns a function into a command.

    This supports core and hybrid command behavior.
    """
    kwargs = _resolve_command_kwargs(
        HybridCommand if hybrid else Command,
        name=name, alias=alias, aliases=aliases, brief=brief, help=help, usage=usage,
    )

    extras = other_kwargs.setdefault('extras', {})
    if examples is not MISSING:
        extras['examples'] = examples

    other_kwargs.setdefault('guild_only', guild_only)
    other_kwargs.setdefault('nsfw', nsfw)

    # Apply decorators
    def decorator(func: AsyncCallable[..., Any]) -> Command:
        func = commands.command(**kwargs, **other_kwargs)(func)

        if nsfw:
            func = commands.is_nsfw()(func)
        if guild_only:
            func = commands.guild_only()(func)
        return func

    return decorator


@overload
def group(
        name: str = MISSING,
        *,
        alias: str = MISSING,
        aliases: Iterable[str] = MISSING,
        usage: str = MISSING,
        brief: str = MISSING,
        help: str = MISSING,
        hybrid: Literal[True] = False,
        iwc: bool = True,
        **other_kwargs: Any,
) -> Callable[..., HybridGroupCommand]:
    ...


@overload
def group(
        name: str = MISSING,
        *,
        alias: str = MISSING,
        aliases: Iterable[str] = MISSING,
        usage: str = MISSING,
        brief: str = MISSING,
        help: str = MISSING,
        hybrid: Literal[False] = False,
        iwc: bool = True,
        **other_kwargs: Any,
) -> Callable[..., GroupCommand]:
    ...


def group(
        name: str = MISSING,
        *,
        alias: str = MISSING,
        aliases: Iterable[str] = MISSING,
        usage: str = MISSING,
        brief: str = MISSING,
        help: str = MISSING,
        hybrid: bool = False,
        iwc: bool = True,
        **other_kwargs: Any,
) -> Callable[..., GroupCommand | HybridGroupCommand]:
    """A decorator that turns a function into a group command.

    This supports core and hybrid command behavior.
    """
    kwargs = _resolve_command_kwargs(
        HybridGroupCommand if hybrid else GroupCommand,
        name=name, alias=alias, aliases=aliases, brief=brief, help=help, usage=usage,
    )

    other_kwargs.setdefault('invoke_without_command', iwc)

    return commands.group(**kwargs, **other_kwargs)


@discord.utils.copy_doc(commands.cooldown)
def cooldown(rate: int, per: float, bucket: commands.BucketType = commands.BucketType.user) -> T:
    return commands.cooldown(rate, per, bucket)


@discord.utils.copy_doc(commands.max_concurrency)
def user_max_concurrency(count: int, *, wait: bool = False) -> T:
    return commands.max_concurrency(count, commands.BucketType.user, wait=wait)


@discord.utils.copy_doc(commands.max_concurrency)
def guild_max_concurrency(count: int, *, wait: bool = False) -> T:
    return commands.max_concurrency(count, commands.BucketType.guild, wait=wait)


def guilds(*guild_ids: int) -> T:
    """A decorator that adds guild specification for an (app)command."""

    def decorator(func: T) -> T:
        func.__guild_ids__ = guild_ids
        func = app_commands.guilds(*guild_ids)(func)
        return func

    return decorator


def describe(**parameters: str) -> T:
    """A decorator that adds description to the parameters of a command.

    This also descripting app commands if the command is an instance of `CommandInstance`.
    """

    def decorator(func: T) -> T:
        if isinstance(func, CommandInstance):
            for param in func.params.values():
                if param.name in parameters and param.description is None:
                    param._description = parameters[param.name]
        else:
            func = app_commands.describe(**parameters)(func)
        return func

    return decorator
