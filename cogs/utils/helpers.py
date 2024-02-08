from __future__ import annotations

import datetime
import enum
import json
import time
from typing import TypeVar, Self, Callable, Optional, Any, overload, TYPE_CHECKING, Type, Protocol, List, Dict

import asyncpg
import discord
from discord.ext.commands import TooManyFlags, MissingRequiredFlag, TooManyArguments, MissingFlagArgument
from discord.ext import commands

from cogs.utils.context import Context

T = TypeVar('T', bound='BaseFlags')


class BaseFlags:
    """
    This is base class for numeric flags.

    This class can be used to create certain flags that can be toggled on and off.

    Example
    --------
    ````py
        class MyFlags(BaseFlags):
            foo = 1 << 0
            bar = 1 << 1
            baz = 1 << 2

        >> flags = MyFlags()
        >> flags.has_flag(MyFlags.foo)
        False
        >> flags.set_flag(MyFlags.foo, True)
        >> flags.has_flag(MyFlags.foo)
        True
        >> print(flags)
        <MyFlags value=1>
    ```

    All flags are stored therefore as a single integer value, which can be accessed through the `value` attribute.
    """

    __slots__ = ('value',)

    def __init__(self, value: int = 0) -> None:
        """
        Initialize a new instance of the `BaseFlags` class.

        Parameters
        ----------
        value : int
            The value of the flags.
        """
        self.value = value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, self.__class__) and self.value == other.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __repr__(self) -> str:
        return f'<{self.__class__.__name__} value={self.value}>'

    def is_empty(self) -> bool:
        """Returns true if the flags are empty (i.e. a zero value)"""
        return self.value == 0

    def has_flag(self, o: int) -> bool:
        """
        Returns whether the flag is set or not.

        Parameters
        ----------
        o : int
            The flag to check for.

        Returns
        -------
        bool
            Whether the flag is set or not.
        """
        return (self.value & o) == o

    def set_flag(self, o: int, toggle: bool) -> None:
        """
        Sets the flag to the given value.

        Parameters
        ----------
        o : int
            The flag to set.
        toggle : bool
            The value to set the flag to.

        Returns
        -------
        None
        """
        if toggle is True:
            self.value |= o
        elif toggle is False:
            self.value &= ~o
        else:
            raise TypeError(f'Value to set for {self.__class__.__name__} must be a bool.')


class flag_value(Protocol[T]):
    """A descriptor that returns whether the flag is set or not.

    This is used to create a descriptor that returns a boolean value for a flag.

    Can be used in combination with `BaseFlags` to create a flag that can be toggled on and off.
    """
    def __init__(self, func: Callable[[Any], int]):
        self.flag: int = func(None)
        self.__doc__: Optional[str] = func.__doc__

    @overload
    def __get__(self, instance: None, owner: type[Any]) -> Self:
        ...

    @overload
    def __get__(self, instance: T, owner: type[T]) -> bool:
        ...

    def __get__(self, instance: Optional[T], owner: type[T]) -> Any:
        if instance is None:
            return self
        return instance.has_flag(self.flag)

    def __set__(self, instance: BaseFlags, value: bool) -> None:
        instance.set_flag(self.flag, value)

    def __repr__(self) -> str:
        return f'<flag_value flag={self.flag!r}>'


class PostgresItemMeta(type):
    if TYPE_CHECKING:
        __ignore_record__: bool

    def __new__(
            cls,
            name: str,
            bases: tuple[Type],
            attrs: dict[str, any],
            *,
            ignore_record: bool = False,
    ) -> 'PostgresItemMeta':
        attrs['__ignore_record__'] = ignore_record
        return super().__new__(cls, name, bases, attrs)

    def __call__(cls, *args, **kwargs):
        if cls is PostgresItem:
            raise TypeError('`PostgresItem` cannot be instantiated directly.')
        return super().__call__(*args, **kwargs)


class PostgresItem(metaclass=PostgresItemMeta):
    """
    The base class for representing PostgreSQL fetched rows.

    This class facilitates the creation of a class that maps to a PostgreSQL row. It automatically maps the record's
    values to the class attributes, providing convenient access to the data.

    By overriding the `__slots__` attribute, you can specify which attributes should be mapped to the record. Additional
    attributes, which are not mapped to the record, can be added by specifying them in the `__init__` method and calling
    `super().__init__()`.

    Attributes
    ----------
    record : Optional[asyncpg.Record]
        The PostgreSQL record associated with the item.

    Parameters
    ----------
    record : Optional[asyncpg.Record]
        The PostgreSQL record to be associated with the item.

    Raises
    ------
    TypeError
        If a subclass of `PostgresItem` is instantiated without providing a `record` keyword argument, and the class
        does not specify to ignore the record.

    Examples
    --------
    .. code-block:: python3

            class User(PostgresItem):
                __slots__ = ('id', 'name', 'age')

                def __init__(year: int, **kwargs):
                    self.year = year
                    super().__init__(**kwargs)

                @property
                def display_text() -> str:
                    return f'{self.name} ({self.age} Years old) - {self.year}'

            >> user = User(record=asyncpg.Record)
            >> print(user.display_text)
            John Doe (20 Years old) - 2021
    """

    __slots__ = ('record',)

    def __init__(self, *args, **kwargs: dict[str, Any] | asyncpg.Record) -> None:
        """
        Initialize a new instance of the `PostgresItem` class.

        Parameters
        ----------
        record : Optional[asyncpg.Record]
            The PostgreSQL record to be associated with the item.

        Raises
        ------
        TypeError
            If a subclass of `PostgresItem` is instantiated without providing a `record` keyword argument, and the
            class does not specify to ignore the record.
        """
        record: Optional[asyncpg.Record] = kwargs.pop('record', None)

        if record is None and not self.__class__.__ignore_record__:
            raise TypeError('Subclasses of `PostgresItem` must provide a `record` keyword argument.')

        self.record: asyncpg.Record = record
        if record:
            for k, v in record.items():
                if k not in self.__slots__:
                    continue
                setattr(self, k, v)

        if kwargs:
            for k, v in kwargs.items():
                if k not in self.__slots__:
                    continue
                setattr(self, k, v)

    @classmethod
    def __subclasshook__(cls, subclass: type[Any]) -> bool:
        """Returns whether the subclass has a record attribute."""
        return hasattr(subclass, 'record')

    def __iter__(self) -> dict[str, Any]:
        """An iterator over the record's values."""
        return {k: v for k, v in self.record.items() if not k.startswith('_')}

    def __repr__(self) -> str:
        args = ['%s=%r' % (k, v) for k, v in (self.record.items() if self.record else self.__dict__.items())]
        return '<%s.%s(%s)>' % (self.__class__.__module__, self.__class__.__name__, ', '.join(args))

    def __eq__(self, other: object) -> bool:
        """Returns whether the item's ID is equal to the other item's ID."""
        if isinstance(other, self.__class__):
            return getattr(self, 'id', None) == getattr(other, 'id', None)
        return False

    def __getitem__(self, item: str):
        """Returns the value of the item's record."""
        if not self.record:
            raise TypeError('Cannot get item from an item without a record.')
        return self.record[item]

    def __setitem__(self, item: str, value: Any):
        """Sets the value of the item's record and the internal attributes."""
        if not self.record:
            self.record = {}  # type: ignore # setting a 'fake' record
        self.record[item] = value

        if item in self.__slots__:
            setattr(self, item, value)

    def __delitem__(self, item: str):
        """Deletes an item from the item's record and internal attributes."""
        del self.record[item]

        if item in self.__slots__:
            delattr(self, item)

    def __contains__(self, item: str) -> bool:
        """Returns whether the item's record contains the given item."""
        return item in self.record or item in self.__slots__

    def __len__(self) -> int:
        """Returns the length of the item's record."""
        return len(self.record)

    def __bool__(self) -> bool:
        """Returns whether the item has a record."""
        return bool(getattr(self, 'record', None))

    def __hash__(self) -> int:
        """Returns the hash of the item's ID."""
        return hash(getattr(self, 'id', 0))

    @classmethod
    def temporary(cls, *args, **kwargs) -> 'PostgresItem':
        """Creates a temporary instance of this class."""
        self = ignore_record()(cls)(*args, **kwargs)  # type: ignore
        return self

    def get(self, key: str, default: Any = None) -> Any:
        """Returns the value of the item's record."""
        return self.record.get(key, default)


class FlagConverter(commands.FlagConverter):
    """
    This is an enhanced version of discord.py's FlagConverter that offers greater flexibility.

    With this converter, you can specify flags with no prefix by applying the `without_prefix` attribute to the flag.
    This allows the use of the flag without a prefix, parsing it as a flag. Useful for commands with one required flag
    followed by optional flags.

    Class Parameters
    ----------
    case_insensitive: bool
        Toggle case insensitivity of flag parsing. If True, flags are parsed in a case-insensitive manner.
        Defaults to False.
    prefix: str
        The prefix that all flags must be prefixed with. By default, there is no prefix.
    delimiter: str
        The delimiter that separates a flag's argument from the flag's name. By default, this is `:`.

    Example
    --------
    .. code-block:: python3

            class MyFlags(commands.FlagConverter):
                foo: bool = commands.Flag(aliases=['bar'], without_prefix=True)
                bar: str = commands.Flag(description='A flag that requires a string argument.')

            >> Discord: !mycommand this is my foo flag --bar this is my bar flag
            >> MyFlags(foo=True, bar='this is my bar flag')
    """

    @classmethod
    def parse_flags(cls, argument: str, *, ignore_extra: bool = True) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {}
        flags = cls.__commands_flags__
        aliases = cls.__commands_flag_aliases__

        case_insensitive = cls.__commands_flag_case_insensitive__
        last_position = 0
        flag_first_positions = []
        last_flag: Optional[commands.Flag] = None

        for match in cls.__commands_flag_regex__.finditer(argument):
            begin, end = match.span(0)
            key = aliases.get(match.group('flag').casefold(), match.group('flag'))

            flag = flags.get(key)
            if last_position and last_flag is not None:
                value = argument[last_position: begin - 1].lstrip()
                if not value and not hasattr(last_flag, 'without_prefix'):
                    raise MissingFlagArgument(last_flag)

                name = last_flag.name.casefold() if case_insensitive else last_flag.name
                result.setdefault(name, []).append(value)

            last_position = end
            flag_first_positions.append(begin)
            last_flag = flag

        # Handle left flags that use a prefix
        value = argument[last_position:].strip()

        # Add the remaining string to the last available flag
        if last_flag is not None:
            if not value:
                raise MissingFlagArgument(last_flag)

            name = last_flag.name.casefold() if case_insensitive else last_flag.name
            result.setdefault(name, []).append(value)
        elif value and not ignore_extra:
            raise TooManyArguments(f'Too many arguments passed to {cls.__name__}')

        # Handle left flags that do not use a prefix (escape_prefix=True)
        value = argument[:min(flag_first_positions) if flag_first_positions else len(argument)].rstrip()

        if any(hasattr(flag, 'without_prefix') for flag in flags.values()):
            last_flag = next((flag for flag in flags.values() if hasattr(flag, 'without_prefix')), None)

        # Add the remaining string to the last available flag
        if last_flag is not None:
            if not value:
                raise MissingFlagArgument(last_flag)

            name = last_flag.name.casefold() if case_insensitive else last_flag.name
            result.setdefault(name, []).append(value)
        elif value and not ignore_extra:
            raise TooManyArguments(f'Too many arguments passed to {cls.__name__}')

        return result

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Self:
        """|coro|

        The method that actually converters an argument to the flag mapping.

        Parameters
        ----------
        ctx: :class:`Context`
            The invocation context.
        argument: :class:`str`
            The argument to convert from.

        Raises
        --------
        FlagError
            A flag related parsing error.

        Returns
        --------
        :class:`FlagConverter`
            The flag converter instance with all flags parsed.
        """

        ignore_extra = True
        if (
                ctx.command is not None
                and ctx.current_parameter is not None
                and ctx.current_parameter.kind == ctx.current_parameter.KEYWORD_ONLY
        ):
            ignore_extra = ctx.command.ignore_extra

        arguments = cls.parse_flags(argument, ignore_extra=ignore_extra)
        flags = cls.__commands_flags__

        self = cls.__new__(cls)
        for name, flag in flags.items():
            try:
                values = arguments[name]
            except KeyError:
                if flag.required:
                    raise MissingRequiredFlag(flag)
                else:
                    if callable(flag.default):
                        # Type checker does not understand flag.default is a Callable
                        default = await maybe_coroutine(flag.default, ctx)  # type: ignore
                        setattr(self, flag.attribute, default)
                    else:
                        setattr(self, flag.attribute, flag.default)
                    continue

            if 0 < flag.max_args < len(values):
                if flag.override:
                    values = values[-flag.max_args:]
                else:
                    raise TooManyFlags(flag, values)

            # Special case:
            if flag.max_args == 1:
                value = await commands.flags.convert_flag(ctx, values[0], flag)
                setattr(self, flag.attribute, value)
                continue

            values = [await commands.flags.convert_flag(ctx, value, flag) for value in values]
            if flag.cast_to_dict:
                values = dict(values)

            setattr(self, flag.attribute, values)

        return self


def ignore_record() -> Callable[[T], T]:
    r"""A decorator that bypasses the `record` keyword argument check for `PostgresItem` subclasses."""

    def decorator(func: T) -> T:
        func.__ignore_record__ = True
        return func

    return decorator


_TC = TypeVar('_TC', asyncpg.Connection, asyncpg.Pool)


class AcquireProtocol(Protocol[_TC]):
    """A protocol for objects that can be used in a `maybe_acquire` context manager.

    This is useful to completly cleanup and release the connection or pool after the context manager is done.
    """

    def __init__(self, connection: Optional[asyncpg.Connection], *, pool: asyncpg.Pool) -> None:
        self.connection: Optional[asyncpg.Connection] = connection
        self.pool: asyncpg.Pool = pool
        self._cleanup: bool = False

    async def __aenter__(self) -> _TC:
        if self.connection is None:
            self._cleanup = True
            self._connection = c = await self.pool.acquire()
            return c
        return self.connection

    async def __aexit__(self, *args) -> None:
        if self._cleanup:
            await self.pool.release(self._connection)


class Colour(discord.Colour):
    """A subclass of `discord.Colour` with some extra colours."""

    @classmethod
    def darker_red(cls) -> Self:
        return cls(0xE32636)

    @classmethod
    def transparent(cls) -> Self:
        return cls(0x2b2d31)

    @classmethod
    def lime_green(cls) -> Self:
        return cls(0x3AFF76)

    @classmethod
    def light_red(cls) -> Self:
        return cls(0xFF6666)

    @classmethod
    def light_orange(cls) -> Self:
        return cls(0xFF8000)

    @classmethod
    def electric_violet(cls) -> Self:
        return cls(0x9b00ff)

    @classmethod
    def royal_blue(cls) -> Self:
        return cls(0x133549)

    @classmethod
    def black(cls) -> Self:
        return cls(0x000000)


class BasicJSONEncoder(json.JSONEncoder):
    """A basic JSON encoder that encodes `NamedDict` objects."""

    def default(self, o: Any) -> Any:
        if isinstance(o, datetime.datetime):
            return o.isoformat()
        elif isinstance(o, datetime.timedelta):
            return o.total_seconds()
        elif isinstance(o, enum.Enum):
            return o.value
        return super().default(o)


class TimeMesh(Protocol):
    """A context manager that measures the time it takes to execute a block of code."""

    A = TypeVar('A', bound='TimeMesh')

    def __init__(self):
        self._start = None
        self._end = None

    def __enter__(self) -> A:
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._end = time.perf_counter()

    def __int__(self) -> int:
        return round(self.time)

    def __float__(self) -> float:
        return self.time

    def __str__(self) -> str:
        return str(self.time)

    def __hash__(self) -> int:
        return hash(self.time)

    def __repr__(self) -> str:
        return f'<TimeMesh time={self.time} start={self._start} end={self._end}>'

    @property
    def time(self) -> int:
        if self._end is None:
            raise ValueError('TimeMesh has not yet ended.')
        return self._end - self._start
