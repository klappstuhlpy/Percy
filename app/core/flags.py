from __future__ import annotations as _

import inspect
import re
import sys
from argparse import ArgumentParser as _ArgumentParser, Namespace
from dataclasses import dataclass
from typing import Annotated, Any, Collection, Generic, Iterator, TYPE_CHECKING, Type, TypeVar, Union, Literal

from discord import AppCommandOptionType, app_commands, Interaction
from discord.ext import commands
from discord.ext.commands import BadArgument, Converter, MissingRequiredArgument, run_converters, TooManyFlags
from discord.ext.commands.flags import validate_flag_name, convert_flag
from discord.ext.commands.view import StringView
from discord.utils import MISSING, resolve_annotation, maybe_coroutine

if TYPE_CHECKING:
    from app.core.models import Command, Context

FlagMetaT = TypeVar('FlagMetaT', bound='FlagMeta')
D = TypeVar('D')
T = TypeVar('T')

WS_SPLIT_REGEX: re.Pattern[str] = re.compile(r'(\s+\S+)')

__all__ = (
    'flag',
    'store_true',
    'MockFlags',
    'Flags',
    'FlagMeta',
    'ConsumeUntilFlag',
    'FlagNamespace',
)


class MockFlags:
    """A mock flags class that basically is like a namespace to store keyword arguments
    and access them later on but keep silent if the attribute to get does not exist
    and return the default value if it does not exist.

    It can be initialized only one time and is afterwards frozen with the given attributes.
    """
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

        for key, value in kwargs.items():
            setattr(self, key, value)

    def __repr__(self):
        return f'<MockFlags {self.__dict__}>'

    def __getattribute__(self, item):
        try:
            return super().__getattribute__(item)
        except AttributeError:
            return None

    def __getitem__(self, item) -> Any:
        try:
            return self.__dict__[item]
        except KeyError:
            return MockFlags()

    def __contains__(self, item) -> bool:
        return item in self.__dict__

    def __bool__(self):
        return bool(self.__dict__)


class ArgumentParser(_ArgumentParser):
    def error(self, message: str) -> None:
        raise BadArgument(message)


@dataclass
class Flag(Generic[T]):
    """Represents a flag.

    This class is not intended to be created manually, instead, use the :func:`flag` factory.

    Attributes
    ----------
    name: str
        The name of the flag.
    aliases: Collection[str]
        The aliases of the flag.
    dest: str
        The destination of the flag.
        This is basically the attribute name.
    max_args: int
        The maximum amount of arguments the flag can have.
    override: bool
        Whether the flag can override other flags.
    store_true: bool
        Whether the flag is a store true flag.
    converter: Converter[T] | Type[T] | None
        The converter for the flag.
        This shortens the amount of arguments the flag can take if there are too many.
        Otherwise, a :exc:`TooManyFlags` error is raised.
    short: str | None
        The short version of the flag.
    description: str | None
        The description of the flag.
    required: bool
        Whether the flag is required.
    default: T | None
        The default value of the flag.
    """

    name: str = MISSING
    aliases: Collection[str] = ()
    dest: str = MISSING
    max_args: int = MISSING
    override: bool = MISSING
    store_true: bool = False
    converter: Converter[T] | Type[T] = MISSING
    short: str = MISSING
    description: str = MISSING
    required: bool = False
    default: T = MISSING

    cast_to_dict: bool = False

    @property
    def attribute(self) -> str:
        """:class:`str`: Alias for :attr:`dest`."""
        return self.dest

    @property
    def annotation(self) -> Converter[T] | Type[T]:
        """:class:`Any`: The annotation for the flag."""
        return self.converter

    def add_to(self, parser: ArgumentParser, /) -> None:
        """Adds the flag to the parser."""
        if self.name is MISSING:
            raise TypeError('name must be set.')

        if self.dest is MISSING:
            self.dest = self.name.replace('-', '_')

        args = ['--' + self.name] if self.short in (MISSING, None) else ['--' + self.name, '-' + self.short]
        args.extend('--' + alias for alias in self.aliases)

        if not self.store_true:
            parser.add_argument(
                *args,
                nargs='+',
                dest=self.dest,
                required=self.required,
                default=self.default,
            )
            return

        parser.add_argument(*args, dest=self.dest, action='store_true')


def _resolve_aliases(alias: str, aliases: Collection[str]) -> list[str]:
    """Resolve the aliases for the flag."""
    if alias and aliases:
        raise ValueError('`alias` and `aliases` are mutually exclusive.')

    if alias is not MISSING:
        aliases = (alias,)

    if aliases is not MISSING:
        return [alias.casefold() for alias in aliases]

    return []


def flag(
        *,
        name: str = MISSING,
        short: str = MISSING,
        alias: str = MISSING,
        aliases: Collection[str] = MISSING,
        converter: Converter[T] | Type[T] = MISSING,
        override: bool = MISSING,
        description: str = MISSING,
        max_args: int = MISSING,
        required: bool = False,
        default: T | None = None,
) -> Annotated[T, Flag[T]]:
    """Override the default functionality and parameters of the underlying :class:`Flag` class attributes.

    Parameters
    ------------
    name: :class:`str`
        The flag name. If not given, defaults to the attribute name.
    short: :class:`str`
        The short version of the flag.
    alias: :class:`str`
        An alias to the flag name. If not given no alias is set.
    aliases: List[:class:`str`]
        Aliases to the flag name. If not given no aliases are set.
    converter: Any
        The converter to use for this flag. This replaces the annotation at
        runtime which is transparent to type checkers.
    override: :class:`bool`
        Whether multiple given values overrides the previous value. The default
        value depends on the annotation given.
    description: :class:`str`
        The description of the flag. Shown for hybrid commands when they're
        used as application commands.
    max_args: :class:`int`
        The maximum number of arguments the flag can accept.
        A negative value indicates an unlimited amount of arguments.
        The default value depends on the annotation given.
    required: :class:`bool`
        Whether the flag is required. The default value depends on the annotation given.
    default: Any
        The default parameter. This could be either a value or a callable that takes
        :class:`Context` as its sole parameter. If not given then it defaults to
        the default value given to the attribute.
    """
    return Flag(
        name=name and name.casefold(),
        short=short,
        aliases=_resolve_aliases(alias, aliases),
        converter=converter,
        override=override,
        description=description,
        max_args=max_args,
        required=required,
        default=default,
    )


class store_true_dummy_converter(commands.Converter[bool], app_commands.Transformer):
    """
    This converter is used to support store-true flags to work with app_commands because they don't
    exist with app commands by default, so for app commands, we just set the parameter as a boolean
    parameter, so it behaves like a `AppCommandOptionType.boolean`.
    """
    async def convert(self, ctx: Context, argument: str) -> bool:
        return True

    async def transform(self, interaction: Interaction, value: Any, /) -> Any:
        return value

    @property
    def type(self) -> AppCommandOptionType:
        return AppCommandOptionType.boolean


def store_true(
        *,
        name: str = MISSING,
        short: str = None,
        alias: str = MISSING,
        aliases: Collection[str] = (),
        description: str = None,
) -> Flag:
    """A factory that creates a store true flag. This is a flag that is always true once passed.

    Parameters
    ------------
    name: :class:`str`
        The flag name. If not given, defaults to the attribute name.
    short: :class:`str`
        The short version of the flag.
    alias: :class:`str`
        An alias to the flag name. If not given no alias is set.
    aliases: List[:class:`str`]
        Aliases to the flag name. If not given no aliases are set.
    description: :class:`str`
        The description of the flag. Shown for hybrid commands when they're
    """
    return Flag(
        name=name and name.casefold(),
        short=short,
        aliases=_resolve_aliases(alias, aliases),
        store_true=True,
        description=description
    )


class ConsumeUntilFlag(Converter[T]):
    """A converter that consumes all arguments until a flag is found.

    This is done by reading the rest of the arguments until a flag is found.
    If the flag is found, the converter will stop and return the consumed arguments.
    If the flag is not found, the converter will return the default value if given.
    """

    def __init__(self, converter: Converter[T] | Type[T], default: T = MISSING) -> None:
        self.converter: Converter[T] | Type[T] = converter
        self.default: T = default

    async def convert(self, ctx: Context, argument: str) -> T:
        from app.core.models import Command

        if not isinstance(ctx.command, Command) or ctx.command.custom_flags is None:
            raise TypeError

        if ctx.command.custom_flags.is_flag_starter(argument):
            if self.default is not MISSING:
                return self.default

            raise MissingRequiredArgument(ctx.current_parameter)

        ctx.view.undo()
        rest = ctx.view.read_rest()
        parts = WS_SPLIT_REGEX.split(rest)

        valid = []
        for part in parts:
            if not part:
                continue
            if ctx.command.custom_flags.is_flag_starter(part):
                break
            valid.append(part)

        argument = ''.join(valid).strip()
        ctx.view.index = ctx.view.buffer.rfind(argument) + len(argument)

        if not self.converter:
            return argument

        return await run_converters(ctx, self.converter, argument, ctx.current_parameter)


def _get_namespaces(attrs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        global_ns = sys.modules[attrs['__module__']].__dict__
    except KeyError:
        global_ns = {}

    frame = inspect.currentframe()
    try:
        if frame is None:
            local_ns = {}
        else:
            parent = frame if frame.f_back is None else frame.f_back
            local_ns = parent.f_locals
    finally:
        del frame

    return local_ns, global_ns


def _resolve_flag_annotation(flag: Flag[Any], annotation: Any, *args: Any) -> None:
    annotation = resolve_annotation(annotation, *args)
    if annotation is type(None) or not annotation:
        annotation = str

    try:
        origin = annotation.__origin__
    except AttributeError:
        # A regular type hint
        if flag.max_args is MISSING:
            flag.max_args = 1
    else:
        if origin is Union:
            # typing.Union
            if flag.max_args is MISSING:
                flag.max_args = 1
            if annotation.__args__[-1] is type(None) and flag.default is MISSING:
                # typing.Optional
                flag.default = None
        elif origin is tuple:
            # typing.Tuple
            # tuple parsing is e.g. `flag: peter 20`
            # for Tuple[str, int] would give you flag: ('peter', 20)
            if flag.max_args is MISSING:
                flag.max_args = 1
        elif origin is list:
            # typing.List
            if flag.max_args is MISSING:
                flag.max_args = -1
        elif origin is dict:
            # typing.Dict[K, V]
            # Equivalent to:
            # typing.List[typing.Tuple[K, V]]
            flag.cast_to_dict = True
            if flag.max_args is MISSING:
                flag.max_args = -1
            if flag.override is MISSING:
                flag.override = True
        elif origin is Literal:
            if flag.max_args is MISSING:
                flag.max_args = 1
        else:
            raise TypeError(f'Unsupported typing annotation {annotation!r} for {flag.name!r} flag')

    if flag.override is MISSING:
        flag.override = False

    if not flag.store_true:
        flag.converter = annotation
        return

    # If the flag is a store true flag, we need to set the converter to a dummy converter
    # that will always return True, this is because store true flags don't have a value
    # and are always True if they are passed.
    # This needs to be done to work with app commands
    flag.converter = store_true_dummy_converter
    flag.default = False


def _resolve_flags(attrs: dict[str, T]) -> dict[str, Flag[T]]:
    """Resolves the flags from the class attributes.

    This parses the class attributes and resolves the flags from them.
    """
    local_ns, global_ns = _get_namespaces(attrs)
    annotations = attrs.get('__annotations__', {})

    flags = {}
    args = global_ns, local_ns, {}

    for name, value in attrs.items():
        if name.startswith('__') or not isinstance(value, Flag):
            continue

        if value.converter is MISSING:
            _resolve_flag_annotation(value, annotations[name], *args)

        value.dest = name = name.casefold()
        if value.name is MISSING:
            value.name = name

        flags[name] = value

    for name, annotation in annotations.items():
        if name in flags:
            continue

        flags[name] = res = flag(name=name.casefold())
        res.dest = name

        _resolve_flag_annotation(res, annotation, *args)

    return flags


class FlagMeta(type, Generic[T]):
    if TYPE_CHECKING:
        __commands_is_flag__: bool

        __commands_flags__: dict[str, Flag[T]]
        __commands_flag_parser__: ArgumentParser
        __commands_flag_compress_usage__: bool

        __commands_flag_aliases__: dict[str, str]
        __commands_flag_delimiter__: str
        __commands_flag_prefix__: str

    def __new__(
            mcs: Type[FlagMetaT],
            name: str,
            bases: tuple[Type[Any], ...],
            attrs: dict[str, Any],
            *,
            compress_usage: bool = False,
            delimiter: str = ' ',
            prefix: str = '--',
    ) -> FlagMetaT:
        attrs['__commands_is_flag__'] = True

        flags: dict[str, Flag] = {}
        aliases: dict[str, str] = {}

        for base in reversed(bases):
            if base.__dict__.get('__commands_is_flag__', False):
                flags.update(base.__dict__['__commands_flags__'])
                aliases.update(base.__dict__['__commands_flag_aliases__'])
                if delimiter is MISSING:
                    attrs['__commands_flag_delimiter__'] = base.__dict__['__commands_flag_delimiter__']
                if prefix is MISSING:
                    attrs['__commands_flag_prefix__'] = base.__dict__['__commands_flag_prefix__']

        attrs['__commands_flag_delimiter__'] = delimiter
        attrs['__commands_flag_prefix__'] = prefix
        attrs['__commands_flag_compress_usage__'] = compress_usage

        for flag_name, flag in _resolve_flags(attrs).items():
            flags[flag_name] = flag
            aliases.update({alias_name: flag_name for alias_name in flag.aliases})

        forbidden = set(delimiter).union(prefix)
        for flag_name in flags:
            validate_flag_name(flag_name, forbidden)
        for alias_name in aliases:
            validate_flag_name(alias_name, forbidden)

        attrs['__doc__'] = __doc__ = inspect.cleandoc(inspect.getdoc(mcs))
        attrs['__commands_flags__'] = flags
        attrs['__commands_flag_aliases__'] = aliases

        parser = ArgumentParser(description=__doc__)

        for flag in flags.values():
            flag.add_to(parser)

        attrs['__commands_flag_parser__'] = parser

        return super().__new__(mcs, name, bases, attrs)

    @property
    def flags(cls) -> dict[str, Flag[T]]:
        return cls.__commands_flags__.copy()

    @property
    def parser(cls) -> ArgumentParser:
        return cls.__commands_flag_parser__

    @property
    def default(cls) -> FlagNamespace[T]:
        """Returns a Namespace with all flags set to their default values or ``None``.

        Raises
        ------
        ValueError
            If any flag has required set to True.
        """
        if any(flag.required for flag in cls.flags.values()):
            raise ValueError('cannot set as default')

        kwargs = {v.dest: False if v.store_true else v.default for v in cls.flags.values()}

        return FlagNamespace(Namespace(**kwargs), cls)

    def get_flag(cls, name: str) -> Flag[T]:
        """Return a flag parameter by the given name.

        Returns
        -------
        Flag[T]
            The flag with the matching name.

        Raises
        ------
        KeyError
            If a flag with the given name was not found.
        """
        return cls.__commands_flags__[name.casefold()]

    def is_flag_starter(cls, sample: str) -> bool:
        """Return whether the sample starts with a valid flag.

        This checks if the sample starts with a flag, e.g. -a, --name, etc.

        Parameters
        ----------
        sample: :class:`str`
            The sample to check.

        Returns
        -------
        :class:`bool`
            Whether the sample starts with a flag.
        """
        sample, *_ = sample.lstrip().split(' ', maxsplit=1)
        sample, _, _ = sample.replace('\u2014', '--').partition('=')

        if not sample.startswith('-'):
            return False

        for flag in cls.walk_flags():
            if flag.short and sample == f'-{flag.short}':
                # check if the short version matches
                return True
            if flag.name and sample.casefold() == f'--{flag.name}':
                # check if the long version matches
                return True
            if any(sample.casefold() == f'--{alias}' for alias in flag.aliases):
                # check if any of the aliases match
                return True

        for part in sample.split():
            # Check for combined short flag syntax, e.g. -a -b can become -ab
            if part.startswith('--') or not part.startswith('-'):
                continue

            # splits an "-ab" flag into [a, b] for comparison
            if all(any(subject == f.short for f in cls.walk_flags()) for subject in part[1:]):
                return True

        return False

    def walk_flags(cls) -> Iterator[Flag[T]]:
        """Walks through all flags in the class."""
        yield from cls.__commands_flags__.copy().values()

    def inject(cls, command: Command) -> None:
        """Injects the flags into the command."""
        command.custom_flags = cls.__commands_flags__


class FlagNamespace(Generic[T]):
    """Represents a namespace of flags."""

    if TYPE_CHECKING:
        __argparse_namespace__: Namespace
        __flags__: FlagMeta

    def __init__(self, namespace: Namespace, flags: FlagMeta) -> None:
        self.__argparse_namespace__ = namespace
        self.__flags__ = flags

    def __getattr__(self, item: str) -> T:
        return getattr(self.__argparse_namespace__, item)

    def get(self, item: str, default: D = None) -> T | D:
        try:
            return getattr(self, item)
        except AttributeError:
            return default

    __getitem__ = __getattr__

    def __contains__(self, item: str) -> bool:
        return item in self.__argparse_namespace__

    def __iter__(self) -> Iterator[tuple[str, T]]:
        yield from self.__argparse_namespace__.__dict__.items()

    def __repr__(self) -> str:
        return repr(self.__argparse_namespace__)

    def __len__(self) -> int:
        return sum(1 for _ in self)


class _FI(str):
    def isidentifier(self) -> bool:
        return True


class Flags[T](metaclass=FlagMeta):  # type: FlagMeta[T]
    """A custom flags class that allows you to create flags for your commands.

    This class will automatically create an ArgumentParser and inject the flags into the command.

    This custom FlagConverter basically works as the `commands.FlagConverter` but also implements
    store-true flags (flags that are always True if they are passed) and consume-until-flag parameters.

    Parameters
    -----------
    prefix: :class:`str`
        The prefix that all flags must be prefixed with.
        By default, the prefix is ``--``.
    delimiter: :class:`str`
        The delimiter that separates a flag's argument from the flag's name.
        By default, the delimiter is a space.

    Example
    -------
    .. code-block:: python3

        class MyFlags(Flags):
            age: int = flag(description='The age of the user.', default=18)
            is_admin: bool = store_true(description='Whether the user is an admin.')

        class MyCommand(Cog):
            @command()
            async def my_command(self, ctx, *, name: str, flags: MyFlags):
                # name is the consume-until-flag parameter that gets the rest of the arguments
                # that are not part of the flags.
                print(name, flags.age, flags.is_admin)
    """

    def __repr__(self) -> str:
        pairs = ' '.join([f'{flag.dest}={getattr(self, flag.dest)!r}' for flag in self.get_flags().values()])
        return f'<{self.__class__.__name__} {pairs}>'

    def __iter__(self) -> Iterator[tuple[str, Any]]:
        for flag in self.__class__.__commands_flags__.values():
            yield flag.name, getattr(self, flag.attribute)

    @classmethod
    def get_flags(cls) -> dict[str, Flag]:
        """Dict[:class:`str`, :class:`Flag`]: A mapping of flag name to flag object this converter has."""
        return cls.__commands_flags__.copy()

    @classmethod
    def _can_be_constructible(cls) -> bool:
        return all(not flag.required for flag in cls.__commands_flags__.values())

    @classmethod
    async def _construct_default(cls, ctx: Context) -> Flags[T]:
        self = cls.__new__(cls)
        flags = cls.__commands_flags__
        for flag in flags.values():
            if callable(flag.default):
                # Type checker does not understand that flag.default is a Callable
                default = await maybe_coroutine(flag.default, ctx)
                setattr(self, flag.attribute, default)
            else:
                setattr(self, flag.attribute, flag.default)
        return self

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> FlagNamespace[T]:
        """|coro|

        The actual flag converter that converts the given argument into a :class:`FlagNamespace`.
        This also consumes the leftover arguments that are not part of the flags and passes them to the
        respective KEYWORD parameter if one exists.

        Parameters
        ------------
        ctx: :class:`Context`
            The invocation context.
        argument: :class:`str`
            The argument to convert.

        Raises
        --------
        commands.MissingRequiredFlag
            A required flag was not passed.
        commands.TooManyFlags
            Too many flags were passed.
        commands.BadArgument
            An argument is parsed that does not exist.

        Returns
        --------
        FlagNamespace
            The namespace of flags.
        """
        try:
            flags: FlagMeta[T] = ctx.command.custom_flags
        except Exception as exc:
            raise TypeError(f'bad flag annotation: {exc}')

        splitted = WS_SPLIT_REGEX.split(argument)
        buffer: list[str] = []
        args: list[str] = []

        for part in splitted:
            if not part:
                continue

            if flags.is_flag_starter(part):
                if joined := ''.join(buffer):
                    args.append(joined)

                args.append(part.lstrip().replace('\u2014', '--'))
                buffer = []
                continue

            buffer.append(part)

        if joined := ''.join(buffer):
            args.append(joined)

        ns = flags.parser.parse_args(args)
        for name, v in ns.__dict__.items():
            flag = flags.get_flag(name)

            if isinstance(v, list):
                v = ''.join(v)
            if isinstance(v, str):
                v = v.strip()

            converter = flag.converter
            if converter and v is not None and not flag.store_true:
                param: inspect.Parameter = ctx.current_parameter.replace(name=_FI(f'{ctx.current_parameter.name}.{name}'))

                is_list: bool = False
                try:
                    origin = converter.__origin__
                    args = converter.__args__
                except AttributeError:
                    pass
                else:
                    if origin is list:
                        is_list = True

                if is_list:
                    converter = args[0]
                    view = StringView(v)
                    v = []

                    while not view.eof:
                        view.skip_ws()
                        if view.eof:
                            break

                        word = view.get_quoted_word()
                        v.append(await run_converters(ctx, converter, word, param))

                    if 0 < flag.max_args < len(v):
                        if flag.override:
                            v = v[-flag.max_args:]
                        else:
                            raise TooManyFlags(flag, v)

                    # skip the reset and convert only the first value
                    if flag.max_args == 1:
                        v = await convert_flag(ctx, v[0], flag)
                        setattr(ns, name, v)
                        continue

                    # Another special case, tuple parsing.
                    # Tuple parsing is basically converting arguments within the flag
                    # So, the given flag: hello 20 as the input and Tuple[str, int] as the type hint
                    # We would receive ('hello', 20) as the resulting value
                    # This uses the same whitespace and quoting rules as regular parameters.
                    v = [await convert_flag(ctx, value, flag) for value in v]

                    if flag.cast_to_dict:
                        v = dict(v)
                else:
                    v = await run_converters(ctx, converter, v, param)

            elif v is None and flag.required:
                raise commands.MissingRequiredFlag(flag)

            setattr(ns, name, v)

        return FlagNamespace(ns, cls)
