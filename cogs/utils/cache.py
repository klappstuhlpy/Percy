from __future__ import annotations

import asyncio
import enum
import functools
import time

from typing import Any, Callable, Coroutine, MutableMapping, TypeVar, Protocol
from lru import LRU

R = TypeVar('R')

# Can't use ParamSpec due to https://github.com/python/typing/discussions/946


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


class ExpiringCache(dict):
    def __init__(self, seconds: float):
        self.__ttl: float = seconds
        super().__init__()

    def __verify_cache_integrity(self):
        # Have to do this in two steps...
        current_time = time.monotonic()
        to_remove = [k for (k, (v, t)) in self.items() if current_time > (t + self.__ttl)]
        for k in to_remove:
            del self[k]

    def __contains__(self, key: str):
        self.__verify_cache_integrity()
        return super().__contains__(key)

    def __getitem__(self, key: str):
        self.__verify_cache_integrity()
        return super().__getitem__(key)

    def __setitem__(self, key: str, value: Any):
        super().__setitem__(key, (value, time.monotonic()))


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
    """

    def decorator(func: Callable[..., Coroutine[Any, Any, R]]) -> CacheProtocol[R]:
        if strategy is Strategy.LRU:
            _internal_cache = LRU(maxsize)
            _stats = _internal_cache.get_stats
        elif strategy is Strategy.RAW or strategy is Strategy.ADDITIVE:
            _internal_cache = {}
            def _stats(): return len(_internal_cache), maxsize
        elif strategy is Strategy.TIMED:
            _internal_cache = ExpiringCache(maxsize)
            def _stats(): return len(_internal_cache), maxsize
        else:
            raise ValueError(f'Invalid cache strategy {strategy!r}.')

        def _make_key(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
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
            key = _make_key(args, kwargs)
            try:
                task = _internal_cache[key]
            except KeyError:
                _internal_cache[key] = task = asyncio.create_task(func(*args, **kwargs))
                return task
            else:
                return task

        def _invalidate(*args: Any, **kwargs: Any) -> bool:
            try:
                del _internal_cache[_make_key(args, kwargs)]
            except KeyError:
                return False
            else:
                return True

        def _invalidate_containing(key: str) -> None:
            keys_to_delete = [
                k for k in _internal_cache.keys() if key in k
            ]
            for k in keys_to_delete:
                try:
                    del _internal_cache[k]
                except KeyError:
                    continue

        def _refactor(*args: Any, **kwargs: Any) -> None:
            replic = kwargs.pop('replic', None)
            if replic is None:
                return

            key = _make_key(args, kwargs)
            try:
                _internal_cache[key] = replic
            except KeyError:
                pass

        wrapper.cache = _internal_cache
        wrapper.get_key = lambda *args, **kwargs: _make_key(args, kwargs)
        wrapper.invalidate = _invalidate
        wrapper.get_stats = _stats
        wrapper.invalidate_containing = _invalidate_containing

        if strategy == Strategy.ADDITIVE:
            wrapper.refactor = _refactor
        return wrapper

    return decorator
