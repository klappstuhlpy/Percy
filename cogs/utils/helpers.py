from __future__ import annotations
import json
import time
from pathlib import Path
from typing import TypeVar, Self, Callable, Optional, Any, overload, Dict, TYPE_CHECKING, Type

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

    def _has_flag(self, o: int) -> bool:
        return (self.value & o) == o

    def _set_flag(self, o: int, toggle: bool) -> None:
        if toggle is True:
            self.value |= o
        elif toggle is False:
            self.value &= ~o
        else:
            raise TypeError(f'Value to set for {self.__class__.__name__} must be a bool.')


class flag_value:
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
        return instance._has_flag(self.flag)

    def __set__(self, instance: BaseFlags, value: bool) -> None:
        instance._set_flag(self.flag, value)

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
            raise TypeError("`PostgresItem` cannot be instantiated directly.")
        return super().__call__(*args, **kwargs)


class PostgresItem(metaclass=PostgresItemMeta):
    """The base class for PostgreSQL fetched rows."""

    __slots__ = ('record',)

    def __init__(self, **kwargs) -> None:
        record: asyncpg.Record = kwargs.pop('record', None)

        if record is None and not self.__class__._ignore_record:
            raise TypeError("Subclasses of `PostgresItem` must provide a `record` keyword argument.")

        self.record: asyncpg.Record = record
        if record:
            for k, v in record.items():
                setattr(self, k, v)

    @classmethod
    def __subclasshook__(cls, subclass: type[Any]) -> bool:
        """Returns whether the subclass has a record attribute."""
        return hasattr(subclass, 'record')

    def __iter__(self):
        """An iterator over the record's values."""
        return ((k, v) for k, v in self.record.items() if not k.startswith('_'))

    def __repr__(self):
        args = ['%s=%r' % (k, v) for k, v in self.record.items()]
        return '<%s.%s(%s)>' % (self.__class__.__module__, self.__class__.__name__, ', '.join(args))

    def __eq__(self, other: object) -> bool:
        """Returns whether the item's ID is equal to the other item's ID."""
        if isinstance(other, self.__class__):
            return getattr(self, 'id', None) == getattr(other, 'id', None)
        return False

    def __bool__(self) -> bool:
        """Returns whether the item has a record."""
        return bool(getattr(self, 'record', None))

    def __hash__(self) -> int:
        """Returns the hash of the item's ID."""
        return hash(getattr(self, 'id', 0))

    @classmethod
    def temporary(cls, **kwargs) -> 'PostgresItem':
        """Creates a temporary instance of this class."""
        return cls(**kwargs)


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


class NamedDict:
    def __init__(self, name: str = 'NamedDict', layer: dict = {}) -> None:  # noqa
        self.__name__ = name
        self.__dict__.update(layer)
        self.__dict__['__shape_set__'] = 'shape' in layer

    def __len__(self):
        return len(self.__dict__)

    def __repr__(self):
        return f'{self.__name__}(%s)' % ', '.join(
            ('%s=%r' % (k, v) for k, v in self.__dict__.items() if not k.startswith('_')))

    def __getattr__(self, attr):
        if attr == 'shape':
            if not self.__dict__['__shape_set__']:
                return None
        try:
            return self.__dict__[attr]
        except KeyError:
            setattr(self, attr, NamedDict())
            return self.__dict__[attr]

    def __setattr__(self, key, value):
        self.__dict__[key] = value

    def _to_dict(self, include_names: bool = False) -> dict:
        data = {}
        for k, v in self.__dict__.items():
            if isinstance(v, NamedDict):
                data[k] = v._to_dict(include_names=include_names)
            else:
                if k != '__shape_set__':
                    if k == '__name__' and not include_names:
                        continue
                    data[k] = v
        return data

    @classmethod
    def _from_dict(cls, data: dict) -> 'NamedDict':
        named = cls(name=data.pop('__name__', 'NamedDict'))
        _dict = named.__dict__
        for k, v in data.items():
            if isinstance(v, dict):
                _dict[k] = cls._from_dict(v)
            else:
                _dict[k] = v
        return named


class config_file:
    """A class for getting and setting the config.json file."""

    def __init__(self, column: str) -> None:
        self.column = column

    path = Path(__file__).parent.parent.parent / "config.json"

    def set(self, **params: dict | Any) -> None:
        """Sets the paremeters in the column of the config.json file."""
        payload = self.load

        if self.column not in payload:
            payload[self.column] = {}

        with open(self.path, "w") as file:
            payload[self.column].update(params)
            json.dump(payload, file, indent=4)

    @property
    def load(self) -> Dict[str, Any]:
        """Loads the column of the config.json file."""
        with open(self.path, 'r', encoding='utf8') as f:
            data = json.load(f)
        if self.column not in data:
            data[self.column] = {}
        return data[self.column]


class TimeMesh:
    def __init__(self):
        self._start = None
        self._end = None

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._end = time.perf_counter()

    def __int__(self):
        return round(self.time)

    def __float__(self):
        return self.time

    def __str__(self):
        return str(self.time)

    def __repr__(self):
        return f"<TimeMesh time={self.time}>"

    @property
    def time(self) -> int:
        if self._end is None:
            raise ValueError("TimeMesh has not yet ended.")
        return self._end - self._start
