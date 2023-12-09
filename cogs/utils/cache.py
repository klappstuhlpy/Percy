from __future__ import annotations

import asyncio
import enum
import functools
import time

from typing import Any, Callable, Coroutine, MutableMapping, TypeVar, Protocol, Generic, Generator
from lru import LRU

from cogs.utils.constants import Coro

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
    cache: MutableMapping[str, asyncio.Task[R]]

    def __call__(self, *args: Any, **kwds: Any) -> asyncio.Task[R]:
        ...

    def get_key(self, *args: Any, **kwargs: Any) -> str:
        ...

    def invalidate(self, *args: Any, **kwargs: Any) -> bool:
        ...

    def invalidate_containing(self, key: str) -> None:
        ...

    def get_stats(self) -> tuple[int, int]:
        ...

    def refactor(self, *args: Any, **kwargs: Any) -> None:
        ...

    def refactor_containing(self, key: str, replace: Any) -> None:
        ...


class ExpiringCache(dict):
    def __init__(self, seconds: float):
        self.__ttl: float = seconds
        super().__init__()

    def __verify_cache_integrity(self):
        # Have to do this in two steps...
        current_time = time.monotonic()
        to_remove = [k for (k, (v, t)) in super().items() if current_time > (t + self.__ttl)]
        for k in to_remove:
            del self[k]

    def __contains__(self, key: str):
        self.__verify_cache_integrity()
        return super().__contains__(key)

    def __getitem__(self, key: str):
        self.__verify_cache_integrity()
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None):
        v = super().get(key, default)
        if v is default:
            return default
        return v[0]

    def __setitem__(self, key: str, value: Any):
        super().__setitem__(key, (value, time.monotonic()))

    def values(self):
        return map(lambda x: x[0], super().values())

    def items(self):
        return map(lambda x: (x[0], x[1][0]), super().items())


class Strategy(enum.Enum):
    LRU = 1
    RAW = 2
    TIMED = 3
    ADDITIVE = 4


def cache(
    maxsize: int = 128,
    strategy: Strategy = Strategy.LRU,
    ignore_kwargs: bool = False,
) -> Callable[[Callable[..., Coroutine[Any, Any, R]]], CacheProtocol[R]]:
    """A decorator that caches the result of a coroutine to its internal cache.

    Parameters
    ----------
    maxsize: int
        The maximum size of the cache. Defaults to ``128``.
    strategy: Strategy
        The strategy to use for the cache. Defaults to :class:`Strategy.LRU`.
    ignore_kwargs: bool
        Whether to ignore keyword arguments when generating the cache key. Defaults to ``False``.

    Returns
    -------
    Callable[[Callable[..., Coroutine[Any, Any, R]]], CacheProtocol[R]]
        The actual decorator.
    """

    def decorator(func: Coro) -> CacheProtocol[R]:
        """The actual decorator."""
        if strategy is Strategy.LRU:
            _internal_cache = LRU(maxsize)
            _stats = _internal_cache.get_stats
        elif strategy in (Strategy.RAW, Strategy.ADDITIVE):
            _internal_cache = {}
            def _stats(): return len(_internal_cache), maxsize
        elif strategy is Strategy.TIMED:
            _internal_cache = ExpiringCache(maxsize)
            def _stats(): return len(_internal_cache), maxsize
        else:
            raise ValueError(f'Invalid cache strategy {strategy!r}.')

        if not func.__doc__:
            # *Add a default docstring if none is present*
            func.__doc__ = DEFAULT_DOCSTRING

        def _make_key(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
            """Generate a cache key from the given arguments."""
            def _true_repr(o):
                if o.__class__.__repr__ is object.__repr__:
                    return f'<{o.__class__.__module__}.{o.__class__.__name__}>'
                return repr(o)

            key_parts = [f'{func.__module__}.{func.__name__}']  # type: ignore
            key_parts.extend(_true_repr(o) for o in args)
            if not ignore_kwargs:
                for k, v in kwargs.items():
                    if k == 'connection' or k == 'pool':
                        continue

                    key_parts.append(_true_repr(k))
                    key_parts.append(_true_repr(v))

            return ':'.join(key_parts)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any):
            """The actual wrapper for the cache to be assigned to the corresponding function."""
            key = _make_key(args, kwargs)
            try:
                task = _internal_cache[key]
            except KeyError:
                _internal_cache[key] = task = asyncio.create_task(func(*args, **kwargs))
                return task
            else:
                return task

        def _invalidate(*args: Any, **kwargs: Any) -> bool:
            """Invalidate a cache entry."""
            try:
                del _internal_cache[_make_key(args, kwargs)]
            except KeyError:
                return False
            else:
                return True

        def _invalidate_containing(key: str) -> None:
            """Invalidate all cache entries containing the given key."""
            keys_to_delete = [
                k for k in _internal_cache.keys() if key in k
            ]
            for k in keys_to_delete:
                try:
                    del _internal_cache[k]
                except KeyError:
                    continue

        def _refactor(replace: str, /, *args: Any, **kwargs: Any) -> None:
            """Replace a cache entry with the given value."""
            key = _make_key(args, kwargs)

            if not hasattr(replace, '__await__'):
                # Turn the obj into an awaitable in order to resolve TypeErrors
                # when calling the assigned cache function without a Task wrapper.
                replace = AwaitableObj(replace)

            try:
                _internal_cache[key] = replace
            except KeyError:
                pass

        def _refactor_containing(key: str, replace: str) -> None:
            """Replace all cache entries containing the given key with the given value."""
            keys_to_refactor = [
                k for k in _internal_cache.keys() if key in k
            ]

            if not keys_to_refactor:
                return

            if not hasattr(replace, '__await__'):
                replace = AwaitableObj(replace)

            for k in keys_to_refactor:
                try:
                    _internal_cache[k] = replace
                except KeyError:
                    continue

        def _get_key(*args: Any, **kwargs: Any) -> str:
            """Get the cache key for the given arguments."""
            return _make_key(args, kwargs)

        wrapper.cache = _internal_cache
        wrapper.get_key = _get_key
        wrapper.invalidate = _invalidate
        wrapper.get_stats = _stats
        wrapper.invalidate_containing = _invalidate_containing

        if strategy == Strategy.ADDITIVE:
            # Adds the ability to replace a cache entry with the given value.
            # TODO: overwork strategy to be more efficient
            wrapper.refactor = _refactor
            wrapper.refactor_containing = _refactor_containing
        return wrapper

    return decorator
