from __future__ import annotations

import functools
from collections import OrderedDict
from functools import wraps
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, TypeVar, Union

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import MISSING

from app.core.flags import ConsumeUntilFlag, FlagMeta, Flags
from app.core.permissions import PermissionSpec
from app.utils import AnsiColor, AnsiStringBuilder, TemporaryAttribute, truncate

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from typing import Self

    from discord.ext.commands import GroupMixin

    from app.core.context import Context
    from app.core.models import Cog
    from app.utils import AsyncCallable

T = TypeVar("T")

__all__ = (
    "Command",
    "CommandInstance",
    "GroupCommand",
    "HybridCommand",
    "HybridGroupCommand",
    "ParamInfo",
    "command",
    "cooldown",
    "describe",
    "group",
    "guild_max_concurrency",
    "guilds",
    "user_max_concurrency",
)


async def _dummy_context(ctx: Context) -> None:
    pass


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
    and store-true flags for text commands (this supports a boolean typed parameter implementation for app_commands).

    Attributes
    ----------
    custom_flags: FlagMeta | None
        The custom flags class for the command.
    """

    def __init__(self, func: AsyncCallable[Any, Any], **kwargs: Any) -> None:
        self._permissions: PermissionSpec = PermissionSpec.new()
        if user_permissions := kwargs.pop("user_permissions", {}):
            self._permissions.update(user_permissions, "user")

        if bot_permissions := kwargs.pop("bot_permissions", {}):
            self._permissions.update(bot_permissions, "bot")

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
        guild_ids_check = getattr(self.callback, "__guild_ids__", None)
        if guild_ids_check and ctx.guild and ctx.guild.id not in guild_ids_check:
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
            cmd = getattr(cmd, "parent", None)
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
                assert self.custom_flags is not None
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
                        args = args[: i + idx]
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
            return command.ansi_signature

        with TemporaryAttribute(command, attr="custom_flags", value=None):
            assert cls.ansi_signature.fget is not None
            return cls.ansi_signature.fget(command)

    @staticmethod
    def _disect_param(param: commands.Parameter) -> tuple:
        """Disects a parameter into it's annotation, greedy, optional, and origin.

        This is basically a separate implementation of the original method in the `commands.Command` class
        to support the `app.core.flags.Flags` class and it's special parameters.
        """
        greedy = isinstance(param.annotation, commands.Greedy)
        optional = False

        # for typing.Literal[...], typing.Optional[typing.Literal[...]], and Greedy[typing.Literal[...]], the
        # parameter signature is a literal list of it's values
        annotation = param.annotation.converter if greedy else param.annotation
        origin = getattr(annotation, "__origin__", None)
        if not greedy and origin is Union:
            none_cls = type(None)
            union_args = annotation.__args__
            optional = union_args[-1] is none_cls

            if len(union_args) == 2 and optional:
                annotation = union_args[0]
                origin = getattr(annotation, "__origin__", None)

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
                    name = "--" + flag.name
                    default = param.empty

                    if (not flag.store_true and flag.default) or flag.default is False:
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
                    start, end = "<>" if required else "[]"
                    result.append(start, color=AnsiColor.gray, bold=True)
                    result.append(name + "...", color=AnsiColor.yellow if required else AnsiColor.blue)
                    result.append(end + " ", color=AnsiColor.gray, bold=True)
                    continue

                for flag in self.custom_flags.walk_flags():
                    start, end = "<>" if flag.required else "[]"
                    base = "--" + flag.name

                    result.append(start, bold=True, color=AnsiColor.gray)
                    result.append(base, color=AnsiColor.yellow if flag.required else AnsiColor.blue)

                    if not flag.store_true:
                        result.append(" <", color=AnsiColor.gray, bold=True)
                        result.append(flag.dest, color=AnsiColor.magenta)

                        if flag.default or flag.default is False:
                            result.append("=", color=AnsiColor.gray)
                            result.append(str(flag.default), color=AnsiColor.cyan)

                        result.append(">", color=AnsiColor.gray, bold=True)

                    result.append(end + " ", color=AnsiColor.gray, bold=True)

                continue

            if origin is Literal:
                name = "|".join(f'"{v}"' if isinstance(v, str) else str(v) for v in annotation.__args__)

            if param.default is not param.empty:
                # We don't want None or '' to trigger the [name=value] case, and instead it should
                # do [name] since [name=None] or [name=] are not exactly useful for the user.
                should_print = param.default if isinstance(param.default, str) else param.default is not None
                result.append("[", color=AnsiColor.gray, bold=True)
                result.append(name, color=AnsiColor.blue)

                if should_print:
                    result.append("=", color=AnsiColor.gray, bold=True)
                    result.append(str(param.default), color=AnsiColor.cyan)
                    extra = "..." if greedy else ""
                else:
                    extra = ""

                result.append("]" + extra + " ", color=AnsiColor.gray, bold=True)
                continue

            elif param.kind == param.VAR_POSITIONAL:
                if self.require_var_positional:
                    start = "<"
                    end = "...>"
                else:
                    start = "["
                    end = "...]"

            elif greedy:
                start = "["
                end = "]..."

            elif optional:
                start, end = "[]"
            else:
                start, end = "<>"

            result.append(start, color=AnsiColor.gray, bold=True)
            result.append(name, color=AnsiColor.blue if start == "[" else AnsiColor.yellow)
            result.append(end + " ", color=AnsiColor.gray, bold=True)

        return result


class _app_command_override(app_commands.Command):
    """An override for the application command class to support the hybrid command implementation.

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


def define_app_command_impl(
    source: HybridCommand | HybridGroupCommand,
    cls: type[app_commands.Command | app_commands.Group],
    **kwargs: Any,
) -> Callable[[AsyncCallable], None]:
    def decorator(func: AsyncCallable) -> None:
        @functools.wraps(func)
        async def wrapper(self: Cog, inter: discord.Interaction, *args: Any, **kwds: Any) -> Any:
            source.cog = self
            ctx = await self.bot.get_context(inter)
            ctx.command = source

            async def invoker(*iargs: Any, **ikwargs: Any) -> Any:
                ctx.args = [ctx.cog, ctx, *iargs]
                ctx.kwargs = ikwargs

                with TemporaryAttribute(ctx.command, "_parse_arguments", _dummy_context):
                    return await ctx.bot.invoke(ctx)

            ctx.full_invoke = invoker
            ctx.interaction = inter  # type: ignore
            return await func(self, ctx, *args, **kwds)

        wrapper.__globals__.update(func.__globals__)
        source.app_command = cls(  # type: ignore
            name=source.name,
            # description cant be none!
            description=source.short_doc or truncate(source.description, 100),
            parent=source.parent.app_command if isinstance(source.parent, HybridGroupCommand) else None,
            callback=wrapper,
            **kwargs,
        )

        @source.app_command.error
        async def on_error(_: Any, interaction: discord.Interaction, error: BaseException) -> None:
            interaction.client.dispatch("command_error", interaction._baton, error)

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
        def decorator(func: AsyncCallable) -> Command:
            _resolve_kwargs_inheritance(kwargs, self)
            result = command(*args, **kwargs)(func)
            self.add_command(result)
            return result

        return decorator

    @discord.utils.copy_doc(commands.Group.group)
    def group(self, *args: Any, **kwargs: Any) -> Callable[..., GroupCommand]:
        def decorator(func: AsyncCallable) -> GroupCommand:
            _resolve_kwargs_inheritance(kwargs, self)
            result = group(*args, **kwargs)(func)
            self.add_command(result)
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
    kwargs: dict[str, Any] = {"cls": cls}

    if name is not MISSING:
        kwargs["name"] = name

    if alias is not MISSING and aliases is not MISSING:
        raise TypeError("cannot have alias and aliases kwarg filled")

    if alias is not MISSING:
        kwargs["aliases"] = (alias,)

    if aliases is not MISSING:
        kwargs["aliases"] = tuple(aliases)

    if usage is not MISSING:
        kwargs["usage"] = usage

    if brief is not MISSING:
        kwargs["brief"] = brief

    if help is not MISSING:
        kwargs["help"] = help

    return kwargs


def _resolve_kwargs_inheritance(new: dict[str, Any], parent: GroupCommand) -> dict[str, Any]:
    new.setdefault("guild_only", parent.__original_kwargs__.get("guild_only", False))
    new.setdefault("parent", parent)
    new.setdefault("hybrid", isinstance(parent, (HybridGroupCommand, HybridCommand)))
    new.setdefault("hidden", parent.hidden)
    return new


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
        name=name,
        alias=alias,
        aliases=aliases,
        brief=brief,
        help=help,
        usage=usage,
    )

    extras = other_kwargs.setdefault("extras", {})
    if examples is not MISSING:
        extras["examples"] = examples

    other_kwargs.setdefault("guild_only", guild_only)
    other_kwargs.setdefault("nsfw", nsfw)

    # Apply decorators
    def decorator(func: AsyncCallable) -> Command:
        func = commands.command(**kwargs, **other_kwargs)(func)

        if nsfw:
            func = commands.is_nsfw()(func)
        if guild_only:
            func = commands.guild_only()(func)
        return func

    return decorator


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
        name=name,
        alias=alias,
        aliases=aliases,
        brief=brief,
        help=help,
        usage=usage,
    )

    other_kwargs.setdefault("invoke_without_command", iwc)

    return commands.group(**kwargs, **other_kwargs)


@discord.utils.copy_doc(commands.cooldown)
def cooldown(rate: int, per: float, bucket: commands.BucketType = commands.BucketType.user) -> Any:
    return commands.cooldown(rate, per, bucket)


@discord.utils.copy_doc(commands.max_concurrency)
def user_max_concurrency(count: int, *, wait: bool = False) -> Any:
    return commands.max_concurrency(count, commands.BucketType.user, wait=wait)


@discord.utils.copy_doc(commands.max_concurrency)
def guild_max_concurrency(count: int, *, wait: bool = False) -> Any:
    return commands.max_concurrency(count, commands.BucketType.guild, wait=wait)


def guilds(*guild_ids: int) -> Any:
    """A decorator that adds guild specification for an (app)command."""

    def decorator(func: T) -> T:
        func.__guild_ids__ = guild_ids
        func = app_commands.guilds(*guild_ids)(func)
        return func

    return decorator


def describe(**parameters: str) -> Any:
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
