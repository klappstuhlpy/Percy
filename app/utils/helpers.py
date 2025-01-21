from __future__ import annotations

import asyncio
import datetime
import enum
import threading
from collections import OrderedDict, deque
from collections.abc import Hashable, MutableMapping
from time import perf_counter_ns
from typing import TYPE_CHECKING, Any, Generic, Protocol, Self, TypeVar, overload, Type, Callable

import discord
from discord.ext import commands
from lru import LRU

T = TypeVar('T')
V = TypeVar('V')

__all__ = (
    'ListedRateLimit',
    'RateLimit',
    'CancellableQueue',
    'Timer',
    'Colour',
    'TemporaryAttribute',
    'BaseFlags',
    'flag_value',
    'NotCaseSensitiveEnum',
    'HealthBarBuilder',
    'HashableT'
)


def find_method_in_class_hierarchy(cls: object | Type[object], method_name: str) -> Callable[[Any], ...] | None:
    """Finds a method in a class hierarchy.

    This function is used to find a method in a class hierarchy. It will search for the method in the class and its
    bases. If the method is not found, it will return None.

    Parameters
    ----------
    cls : object | Type[object]
        The class to search in.
    method_name : str
        The name of the method to search for.
    """
    if hasattr(cls, method_name):
        return getattr(cls, method_name)

    if hasattr(cls, '__bases__') and cls.__bases__:
        for base_cls in cls.__bases__:
            if issubclass(cls, base_cls):
                method = find_method_in_class_hierarchy(base_cls, method_name)
                if method is not None:
                    return method

    return None


C = TypeVar('C', bound=Type[object])


def copy_dict(cls: Type[object]) -> Callable[[Type[object]], C]:
    """Copies the dictionary of a class to another class."""

    def decorator(subclass: Type[object]) -> C:
        for key, value in cls.__dict__.items():
            if key not in subclass.__dict__ and not key.startswith('__'):
                setattr(subclass, key, value)
        return subclass

    return decorator


HashableT = TypeVar('HashableT', bound=Hashable)


class RateLimit(Generic[V, HashableT]):
    """A rate limit implementation.

    This is a simple rate limit implementation that uses an LRU cache to store
    the last time a key was used. This is useful for things like command
    cooldowns.

    Parameters
    ----------
    rate: :class:`int`
        The number of times a key can be used.
    per: :class:`float`
        The number of seconds before the rate limit resets.
    key: :class:`Callable[[discord.Message], V]`
        A callable that takes a message and returns a key.
    tagger: :class:`Callable[[discord.Message], HashableT]`
        A callable that takes a value and returns a hashable tag.
    maxsize: :class:`int`
        The maximum size of the LRU cache.
    """

    if TYPE_CHECKING:
        _lookup: MutableMapping[V, datetime.datetime | tuple[datetime.datetime, set[HashableT]]]

    def __init__(
            self,
            rate: int,
            per: float,
            *,
            key: Callable[[discord.Message], V],
            tagger: Callable[[discord.Message], HashableT] | None = None,
            maxsize: int = 256
    ) -> None:
        self._lookup = LRU(maxsize)  # type: ignore

        self.rate = rate
        self.per = per
        self.key = key
        self.tagger = tagger

    @property
    def ratio(self) -> float:
        return self.per / self.rate

    def is_ratelimited(self, message: discord.Message) -> bool | list[HashableT]:
        now = message.created_at
        key = self.key(message)
        tat = now
        tagged = set()

        value = self._lookup.get(key)
        if value is not None and isinstance(value, tuple):
            tat = max(value[0], now)
            tagged = value[1] if self.tagger is not None else tagged

            if value[0] < now and self.tagger is not None:
                tagged.clear()

        if self.tagger is not None:
            tag = self.tagger(message)
            tagged.add(tag)

        diff = (tat - now).total_seconds()
        max_interval = self.per - self.ratio
        if diff > max_interval:
            if self.tagger is not None:
                copy = list(tagged)
                tagged.clear()
                return copy
            else:
                return True

        new_tat = max(tat, now) + datetime.timedelta(seconds=self.ratio)
        self._lookup[key] = (new_tat, tagged) if self.tagger is not None else new_tat
        return False


class TTLCache(Generic[T, V]):
    """A simple TTL cache implementation."""

    def __init__(self, *, ttl: int = 0) -> None:
        self.ttl: int = ttl
        self._internal_cache: dict[T, V] = {}

    def set(self, key: T, value: V) -> None:
        """Sets a value in the cache with a TTL thats set in the constructor.

        Parameters
        ----------
        key: :class:`T`
            The key to set.
        value: :class:`V`
            The value to set.
        """
        self._internal_cache[key] = value
        if self.ttl > 0:
            ttl_timeout = threading.Timer(self.ttl, self.delete, (key,))
            ttl_timeout.start()

    def get(self, key: T, fallback: Any | None = None) -> V | None:
        """Gets a value from the cache.

        Parameters
        ----------
        key: :class:`T`
            The key to get.
        fallback: Any | None
            The fallback value to return if the key is not found.

        Returns
        -------
        :class:`V` | Any | None
            The value found in the cache or the fallback value.
        """
        return self._internal_cache.get(key, fallback)

    def delete(self, key: T) -> None:
        """Deletes a value from the cache.

        Parameters
        ----------
        key: :class:`T`
            The key to delete.

        Raises
        ------
        KeyError
            The key was not found in the cache.
        """
        self._internal_cache.pop(key)

    @property
    def size(self) -> int:
        """Returns the size of the cache.

        Returns
        -------
        :class:`int`
            The size of the cache.
        """
        return len(self._internal_cache.keys())


class NotCaseSensitiveEnum(enum.Enum):
    """Supports a non-case-insensitive enum converter for discord.py commands.

    .. versionadded:: 2.0.0

    .. note::
        The enum values are looked up by their title-cased name.
    """

    @classmethod
    async def convert(cls, _, argument: str) -> NotCaseSensitiveEnum:
        by_value = '__by_value__' in cls.__members__
        for case in [str.title, str.upper, str.lower, int]:
            if argument.startswith('__') or argument.endswith('__'):
                break
            try:
                if by_value:
                    return cls[case(argument)]  # type: ignore
                return cls(case(argument))  # type: ignore
            except (ValueError, KeyError):
                continue
        raise commands.BadArgument(f"Invalid format. Choose from {', '.join(
            str(m.value).title() if by_value else m.name.title() for m in cls.__members__.values())}.")


class ListedRateLimit(Generic[V]):
    """A rate limit implementation that uses a set to store the keys.

    Parameters
    ----------
    rate: :class:`int`
        The number of times a key can be used.
    per: :class:`float`
        The number of seconds before the rate limit resets.
    key: :class:`Callable[[Any], datetime.datetime]`
        A callable that takes a value and returns a key.
        This must return a datetime object.
    """

    if TYPE_CHECKING:
        _lookup: set[V]

    def __init__(
            self,
            rate: int,
            per: float,
            *,
            key: Callable[[Any], datetime.datetime]
    ) -> None:
        self._lookup: set[V] = set()

        self.rate = rate
        self.per = per
        self.key = key
        self.tat = discord.utils.utcnow()

    @property
    def ratio(self) -> float:
        return self.per / self.rate

    def is_ratelimited(self, obj: Any) -> list[discord.Member]:
        now = self.key(obj) or discord.utils.utcnow()
        if not isinstance(now, datetime.datetime):
            raise TypeError(f'Key function must return a datetime object, not {now.__class__.__name__!r}')

        tat = max(self.tat, now)
        diff = (tat - now).total_seconds()
        max_interval = self.per - self.ratio

        if self.tat < now:
            self._lookup.clear()

        self._lookup.add(obj)

        if diff > max_interval:
            copy = list(self._lookup)
            self._lookup.clear()
            return copy

        new_tat = max(tat, now) + datetime.timedelta(seconds=self.ratio)
        self.tat = new_tat
        return []


class CancellableQueue(Generic[T, V]):
    """A queue that lets you cancel the items pending for work by a provided unique ID."""

    if TYPE_CHECKING:
        _waiters: deque[asyncio.Future[None]]
        _data: OrderedDict[T, V]
        _loop: asyncio.AbstractEventLoop

    def __init__(self, hook_check: Callable[..., Any] | None = None) -> None:
        self._hook_check = hook_check
        self._waiters: deque[asyncio.Future[None]] = deque()
        self._data: OrderedDict[T, V] = OrderedDict()
        self._loop = asyncio.get_running_loop()

    def __wakeup_next(self) -> None:
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)
                break

    def __repr__(self) -> str:
        return f'<{self.__class__.__name__} data={self._data!r} getters[{len(self._waiters)}]>'

    def __len__(self) -> int:
        return len(self._data)

    def is_empty(self) -> bool:
        """:class:`bool`: Returns ``True`` if the queue is empty."""
        return not self._data

    def put(self, key: T, value: V) -> None:
        """Puts an item into the queue.
        If the key is the same as one that already exists then it's overwritten.
        This wakes up the first coroutine waiting for the queue.
        """
        self._data[key] = value
        self.__wakeup_next()

    async def get(self) -> V:
        """Removes and returns an item from the queue.
        If the queue is empty then it waits until one is available.
        """
        while self.is_empty() and not self._hook_check():
            getter = self._loop.create_future()
            self._waiters.append(getter)

            try:
                await getter
            except:
                getter.cancel()
                try:
                    self._waiters.remove(getter)
                except ValueError:
                    pass

                if not self.is_empty() and not getter.cancelled():
                    self.__wakeup_next()

                raise

        _, value = self._data.popitem(last=False)
        return value

    def is_pending(self, key: T) -> bool:
        """Returns ``True`` if the key is currently pending in the queue."""
        return key in self._data

    def cancel(self, key: T) -> V | None:
        """Attempts to cancel the queue item at the given key and returns it if so."""
        return self._data.pop(key, None)

    def cancel_all(self) -> None:
        """Cancels all the queue items"""
        self._data.clear()


class Timer:
    """A context manager that measures the time it takes to execute a block of code."""

    __slots__ = ('start', 'end')

    def __init__(self) -> None:
        self.start = None
        self.end = None

    def __enter__(self) -> Timer:
        self.start = perf_counter_ns()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.end = perf_counter_ns()

    def __int__(self) -> int:
        return round(self._time)

    def __float__(self) -> float:
        return self._time

    def __str__(self) -> str:
        return str(self._time)

    def __hash__(self) -> int:
        return hash(self._time)

    def __repr__(self) -> str:
        return f'<Timer time={self._time} start={self.start} end={self.end}>'

    @property
    def _time(self) -> float:
        """:class:`float`: Returns the time it took to execute the block of code."""
        if self.end is None:
            raise ValueError('Timer has not ended yet.')
        return self.end - self.start

    @property
    def seconds(self) -> float:
        """:class:`float`: Returns the time it took to execute the block of code in seconds."""
        return self._time / 1_000_000_000

    @property
    def milliseconds(self) -> float:
        """:class:`float`: Returns the time it took to execute the block of code in milliseconds."""
        return self._time / 1_000_000

    @property
    def microseconds(self) -> float:
        """:class:`float`: Returns the time it took to execute the block of code in microseconds."""
        return self._time / 1_000

    @property
    def nanoseconds(self) -> float:
        """:class:`float`: Returns the time it took to execute the block of code in nanoseconds."""
        return self._time

    def reset(self) -> float:
        """Resets the timer and returns the time it took."""
        time = (perf_counter_ns() - self.start) / 1_000_000_000
        self.start = perf_counter_ns()
        return time


class HealthBarBuilder:
    """Represents a health bar consiting of partially animated emojis that dynamically decrease."""

    HEART_HIT: str = '<a:heart_hit:1322337609061630022>'
    HEART_NORMAL: str = '<:heart_normal:1322337601553829988>'
    HEART_GAMEOVER: str = '<a:heart_gameover:1322337617424945202>'

    BAR_HIT: str = '<a:heart_bar_hit:1322337651398807583>'
    BAR_HIT_LOOSE: str = '<a:heart_bar_hit_loose:1322337625935450193>'
    BAR_BLANK: str = '<:heart_bar_hit_blank:1322337643190685796>'
    BAR_BORDER: str = '<:heart_bar_border:1322337671443517570>'
    BAR_FULL: str = '<:heart_bar_full:1322337661968584734>'

    def __init__(self, max_health: int = 10) -> None:
        self.max_health = max_health

        self._current_health = max_health

    def __str__(self) -> str:
        return self.build()

    def __isub__(self, damage: int) -> HealthBarBuilder:
        self._current_health -= damage
        return self

    def build(self) -> str:
        health = self._current_health
        length = self.max_health

        if health <= 0:
            return self.HEART_GAMEOVER + (self.BAR_BLANK * length) + self.BAR_BORDER

        if health == length:
            return self.HEART_NORMAL + (self.BAR_FULL * length) + self.BAR_BORDER

        base = self.HEART_HIT
        base += self.BAR_HIT * health
        base += self.BAR_HIT_LOOSE
        base += self.BAR_BLANK * (length - health - 1)
        base += self.BAR_BORDER
        return base


class Colour(discord.Colour):
    """A subclass of `discord.Colour` with some extra colours."""

    @classmethod
    def darker_red(cls) -> Self:
        return cls(0xE32636)

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

    @classmethod
    def coral(cls) -> Self:
        return cls(0xFF7F50)

    @classmethod
    def mirage(cls) -> Self:
        # A darker blue
        return cls(0x1d2439)

    @classmethod
    def di_sierra(cls) -> Self:
        # A sandy
        return cls(0xDDA453)

    @classmethod
    def transparent(cls) -> Self:
        # Discord background
        return cls(0x2b2d31)

    @classmethod
    def white(cls) -> Self:
        return cls(0xFFFFFF)

    @classmethod
    def burgundy(cls) -> Self:
        # A dark red
        return cls(0x99002b)

    @classmethod
    def ocean_green(cls) -> Self:
        return cls(0x43B581)

    @classmethod
    def energy_yellow(cls) -> Self:
        # Lighter yellow
        return cls(0xF8DB5E)

    @classmethod
    def lighter_black(cls) -> Self:
        return cls(0x1A1A1A)


_ATTR_MISSING = object()


class TemporaryAttribute(Generic[T, V]):
    """Supports adding a temporary attribute to an object by using a context manager."""

    __slots__ = ('obj', 'attr', 'value', 'original')

    def __init__(self, obj: T, attr: str, value: V) -> None:
        self.obj: T = obj
        self.attr: str = attr
        self.value: V = value
        self.original: V = getattr(obj, attr, _ATTR_MISSING)

    def __enter__(self) -> T:
        setattr(self.obj, self.attr, self.value)
        return self.obj

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.original is _ATTR_MISSING:
            return delattr(self.obj, self.attr)

        setattr(self.obj, self.attr, self.original)


class BaseFlags:
    """This is a base class for numeric flags.

    This class can be used to create certain flags that can be toggled on and off.
    All flags are stored therefore as a single integer value, which can be accessed through the `value` attribute.

    Notes
    -----
    Every flag value must be a power of 2 to its previous value, starting from 1.
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

    def has_flag(self, other: int) -> bool:
        """
        Returns whether the flag is set or not.

        Parameters
        ----------
        other : int
            The flag to check.

        Returns
        -------
        bool
            Whether the flag is set or not.
        """
        return (self.value & other) == other

    def set_flag(self, other: int, toggle: bool) -> None:
        """
        Sets the flag to the given value.

        Parameters
        ----------
        other : int
            The flag to set.
        toggle : bool
            The value to set the flag to.
        """
        if toggle is True:
            self.value |= other
        elif toggle is False:
            self.value &= ~other
        else:
            raise TypeError(f'Value to set for {self.__class__.__name__} must be a bool.')


class flag_value(Protocol[T]):
    """A descriptor that returns whether the flag is set or not.

    This is used to create a descriptor that returns a boolean value for a flag.

    Can be used in combination with `BaseFlags` to create a flag that can be toggled on and off.
    """
    def __init__(self, func: Callable[[Any], int]) -> None:
        self.flag: int = func(None)
        self.__doc__: str | None = func.__doc__

    @overload
    def __get__(self, instance: None, owner: type[Any]) -> Self:
        ...

    @overload
    def __get__(self, instance: T, owner: type[T]) -> bool:
        ...

    def __get__(self, instance: T | None, owner: type[T]) -> Any:
        if instance is None:
            return self
        return instance.has_flag(self.flag)

    def __set__(self, instance: BaseFlags, value: bool) -> None:
        instance.set_flag(self.flag, value)

    def __repr__(self) -> str:
        return f'<flag_value flag={self.flag!r}>'
