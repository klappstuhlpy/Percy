from __future__ import annotations

import datetime
import enum
import json
import time
from typing import TypeVar, Self, Callable, Optional, Any, overload, TYPE_CHECKING, Type, Protocol

import asyncpg
import discord

T = TypeVar('T', bound='BaseFlags')


class BaseFlags:
    __slots__ = ('value',)

    def __init__(self, value: int = 0) -> None:
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
        return (self.value & o) == o

    def set_flag(self, o: int, toggle: bool) -> None:
        if toggle is True:
            self.value |= o
        elif toggle is False:
            self.value &= ~o
        else:
            raise TypeError(f'Value to set for {self.__class__.__name__} must be a bool.')


class flag_value(Protocol[T]):
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
        _ignore_record: bool

    def __new__(
            cls,
            name: str,
            bases: tuple[Type],
            attrs: dict[str, any],
            *,
            ignore_record: bool = False,
    ) -> 'PostgresItemMeta':
        attrs['_ignore_record'] = ignore_record
        return super().__new__(cls, name, bases, attrs)

    def __call__(cls, *args, **kwargs):
        if cls is PostgresItem:
            raise TypeError('`PostgresItem` cannot be instantiated directly.')
        return super().__call__(*args, **kwargs)


class PostgresItem(metaclass=PostgresItemMeta):
    """The base class for PostgreSQL fetched rows."""

    __slots__ = ('record',)

    def __init__(self, *args, **kwargs) -> None:
        record: Optional[asyncpg.Record] = kwargs.pop('record', None)

        if record is None and not self.__class__._ignore_record:
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
            self.record = {}  # setting a 'fake' record
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


def ignore_record() -> Callable[[T], T]:
    r"""A decorator that bypasses the `record` keyword argument check for `PostgresItem` subclasses."""

    def decorator(func: T) -> T:
        func._ignore_record = True
        return func

    return decorator


_TC = TypeVar('_TC', asyncpg.Connection, asyncpg.Pool)


class AcquireProtocol(Protocol[_TC]):
    """A protocol for objects that can be used in a `maybe_acquire` context manager."""

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
