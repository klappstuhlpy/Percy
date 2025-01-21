from __future__ import annotations

import asyncio
import enum
import functools
import inspect
import time
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar, overload

from lru import LRU

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Generator, MutableMapping

    from app.utils.constants import Coro, NCoro

R = TypeVar('R')

# Can't use ParamSpec due to https://github.com/python/typing/discussions/946


DEFAULT_DOCSTRING = """|coro| @cached

A cached version of the original function.

Parameters
----------
*args: Any
    The arguments to pass to the original function.
**kwargs: Any
    The keyword arguments to pass to the original function.
"""


T = TypeVar('T')


class AwaitableObj(Generic[T]):
    def __init__(self, value: T):
        self.value: T = value

    def __await__(self) -> Generator[Any, None, T]:
        return asyncio.sleep(0, result=self.value).__await__()


class CacheProtocol(Protocol[R]):
    cache: MutableMapping[str, asyncio.Task[R] | R]

    @overload
    def __call__(self, *args: Any, **kwds: Any) -> R:
        ...

    def __call__(self, *args: Any, **kwds: Any) -> asyncio.Task[R]:
        ...

    def get_key(self, *args: Any, **kwargs: Any) -> str:
        """Builds the cache key for the given arguments."""
        ...

    @overload
    def invalidate(self, *args: Any, **kwargs: Any) -> bool:
        ...

    def invalidate(self, *args: Any, **kwargs: Any) -> bool:
        """Invalidate a cache entry with the given arguments."""
        ...

    def invalidate_containing(self, key: str) -> None:
        """Invalidate all cache entries containing the given key."""
        ...

    def get_stats(self) -> tuple[int, int]:
        """Get the current cache stats."""
        ...


class ExpiringCache(dict):
    """A cache that expires after a given amount of time."""
    def __init__(self, seconds: float):
        self.__ttl: float = seconds
        super().__init__()

    def __verify_cache_integrity(self) -> None:
        # Have to do this in two steps...
        current_time = time.monotonic()
        to_remove = [k for (k, (v, t)) in super().items() if current_time > (t + self.__ttl)]
        for k in to_remove:
            del self[k]

    def __contains__(self, key: str) -> bool:
        self.__verify_cache_integrity()
        return super().__contains__(key)

    def __getitem__(self, key: str) -> Any:
        self.__verify_cache_integrity()
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None):
        v = super().get(key, default)
        if v is default:
            return default
        return v[0]

    def __setitem__(self, key: str, value: Any) -> None:
        super().__setitem__(key, (value, time.monotonic()))

    def values(self) -> list[Any]:
        return list(x[0] for x in super().values())

    def items(self) -> list[tuple[str, Any]]:
        return list((x[0], x[1][0]) for x in super().items())


class Strategy(enum.Enum):
    """The strategy to use for the cache.

    Attributes
    ----------
    LRU: int
        The Least Recently Used strategy that can store up to `maxsize` items.
    RAW: int
        The raw strategy.
    TIMED: int
        The timed strategy that expires after the given `maxsize`."""
    LRU = 1
    RAW = 2
    TIMED = 3


def cache(
    maxsize: int = 128,
    strategy: Strategy = Strategy.LRU,
    ignore_kwargs: bool = False,
    action: Callable[..., T] | None = None,
) -> Callable[[Callable[..., Coroutine[Any, Any, R] | R]], CacheProtocol[R]]:
    """A decorator that caches the result of a coroutine to its internal cache.

    This can be used on both coroutines and regular functions.

    Parameters
    ----------
    maxsize: int
        The maximum size of the cache. Defaults to `128`.
        If you use Strategy.TIMED, this will be the expiration time in seconds.
    strategy: Strategy
        The strategy to use for the cache. Defaults to :class:`Strategy.LRU`.
    ignore_kwargs: bool
        Whether to ignore keyword arguments when generating the cache key. Defaults to `False`.
    action: Callable[[str, str], T]
        The action to perform on the given strings. Defaults to `None`.
        This will be called on invalidation with the item that was invalidated.

    Returns
    -------
    Callable[[Callable[..., Coroutine[Any, Any, R] | R], CacheProtocol[R]]
        The actual decorator.
    """

    def decorator(func: Coro | NCoro) -> CacheProtocol[R]:
        """The actual decorator."""
        _stats = None
        match strategy:
            case Strategy.LRU:
                _internal_cache = LRU(maxsize)
                _stats = _internal_cache.get_stats
            case Strategy.RAW:
                _internal_cache = {}
            case Strategy.TIMED:
                _internal_cache = ExpiringCache(maxsize)
            case _:
                raise ValueError(f'Invalid cache strategy {strategy!r}.')

        if _stats is None:
            def _stats() -> tuple[int, int]:
                return len(_internal_cache), maxsize

        if not func.__doc__:
            # Add a default docstring if none is present
            func.__doc__ = DEFAULT_DOCSTRING

        def _make_key(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
            """Generate a cache key from the given arguments."""
            def _true_repr(o: Any) -> str:
                if o.__class__.__repr__ is object.__repr__:
                    return f'<{o.__class__.__module__}.{o.__class__.__name__}>'
                return repr(o)

            key_parts = [f'{func.__module__}.{func.__name__}']

            for arg in args:
                key_parts.append(_true_repr(arg))

            if not ignore_kwargs:
                for k in kwargs:
                    if k in ('connection', 'pool'):
                        continue
                    key_parts.append(f'{_true_repr(k)}={_true_repr(k)}')

            return ':'.join(key_parts)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> asyncio.Task[R] | R:
            """The actual wrapper for the cache to be assigned to the corresponding function."""

            # we only want to cache the positional arguments
            params: list[inspect.Parameter] = list(filter(
                lambda x: x._kind != inspect.Parameter.KEYWORD_ONLY, inspect.signature(func).parameters.values()
            ))
            if any(param.name in ('self', 'cls') for param in params):
                params: list[tuple[Any, inspect.Parameter]] = zip(args, params)
                key = _make_key(tuple([arg for arg, param in params if param.name not in ('self', 'cls')]), kwargs)
            else:
                key = _make_key(args, kwargs)

            try:
                task = _internal_cache[key]
            except KeyError:
                if asyncio.iscoroutinefunction(func):
                    _internal_cache[key] = task = asyncio.create_task(func(*args, **kwargs))
                else:
                    _internal_cache[key] = task = func(*args, **kwargs)
                return task
            else:
                return task

        def _invalidate(*args: Any, **kwargs: Any) -> bool:
            """Invalidate a cache entry.

            If a call is provided, execute the method from the cached object.
            The call should be a string representing the method to call, for example: ``'a.b.c()'``.
            """
            try:
                item = _internal_cache.pop(_make_key(args, kwargs))
            except KeyError:
                return False
            else:
                if action is not None:
                    if isinstance(item, asyncio.Task):
                        item = item.result()
                    action(item)
                return True

        def _invalidate_containing(key: str) -> None:
            """Invalidate all cache entries containing the given key."""
            _cache_keys = _internal_cache.keys() if strategy is Strategy.LRU else _internal_cache
            keys_to_delete = [k for k in _cache_keys if key in k]

            for k in keys_to_delete:
                try:
                    item = _internal_cache[k]
                except KeyError:
                    continue
                else:
                    del _internal_cache[k]
                    if action is not None:
                        action(item)

        def _get_key(*args: Any, **kwargs: Any) -> str:
            """Get the cache key for the given arguments."""
            return _make_key(args, kwargs)

        wrapper.cache = _internal_cache
        wrapper.get_key = _get_key
        wrapper.get_stats = _stats
        wrapper.invalidate = _invalidate
        wrapper.invalidate_containing = _invalidate_containing
        return wrapper

    return decorator
