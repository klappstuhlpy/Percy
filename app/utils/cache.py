from __future__ import annotations

import asyncio
import enum
import functools
import inspect
import time
from typing import TYPE_CHECKING, Any, ParamSpec, Protocol, TypeVar, cast

from lru import LRU

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Generator, Hashable, Iterator, MutableMapping

    from app.utils.constants import Coro, NCoro

R = TypeVar("R")
P = ParamSpec("P")

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


T = TypeVar("T")


class AwaitableObj[T]:
    def __init__(self, value: T) -> None:
        self.value: T = value

    def __await__(self) -> Generator[Any, None, T]:
        return asyncio.sleep(0, result=self.value).__await__()


class CacheProtocol(Protocol[P, R]):
    cache: MutableMapping[str, object] | LRU[Hashable, Any] | dict[Any, Any] | ExpiringCache

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> asyncio.Task[R] | R: ...

    def get_key(self, *args: Any, **kwargs: Any) -> str:
        """Builds the cache key for the given arguments."""
        ...

    def invalidate(self, *args: Any, **kwargs: Any) -> bool:
        """Invalidate a cache entry with the given arguments."""
        ...

    def invalidate_containing(self, key: str) -> None:
        """Invalidate all cache entries containing the given key."""
        ...

    def get_stats(self) -> tuple[int, int]:
        """Get the current cache size and capacity (the TTL for ``Strategy.TIMED``)."""
        ...

    def cache_info(self) -> dict[str, int]:
        """Hit/miss counters plus ``size`` and ``maxsize``."""
        ...


#: Distinguishes "absent" from a legitimately stored ``None`` in :meth:`ExpiringCache.pop`.
_SENTINEL: Any = object()

#: Entry cap for ``Strategy.TIMED`` caches (whose ``maxsize`` is the TTL, not a size).
TIMED_MAX_ENTRIES = 4096


class ExpiringCache(dict):
    """A cache that expires after a given amount of time.

    Entries are stored internally as ``(value, inserted_at)``; every read path unwraps that
    pair, so callers only ever see the value they put in. Writes move the key to the end,
    so the dict stays ordered oldest-first and pruning stops at the first live entry
    instead of scanning everything on every read. ``maxlen`` (optional) caps the entry
    count by evicting the oldest entries.
    """

    def __init__(self, seconds: float, maxlen: int | None = None) -> None:
        self.__ttl: float = seconds
        self.__maxlen: int | None = maxlen
        super().__init__()

    def __verify_cache_integrity(self) -> None:
        current_time = time.monotonic()
        expired = []
        for k, (_, t) in super().items():  # oldest first
            if current_time <= t + self.__ttl:
                break
            expired.append(k)
        for k in expired:
            super().__delitem__(k)

    def __contains__(self, key: object) -> bool:
        self.__verify_cache_integrity()
        return super().__contains__(key)

    def __getitem__(self, key: str) -> Any:
        self.__verify_cache_integrity()
        # Unwrap the (value, inserted_at) pair this cache stores, so subscripting returns
        # what was put in -- exactly like ``get``/``values``/``items`` already do.
        return super().__getitem__(key)[0]

    def pop(self, key: str, *default: Any) -> Any:
        """Removes an entry and returns the stored value (not the internal timestamped pair)."""
        self.__verify_cache_integrity()
        entry = super().pop(key, _SENTINEL)
        if entry is _SENTINEL:
            if default:
                return default[0]
            raise KeyError(key)
        return entry[0]

    def get(self, key: str, default: Any = None) -> Any:
        self.__verify_cache_integrity()
        v = super().get(key, default)
        if v is default:
            return default
        return v[0]

    def __setitem__(self, key: str, value: Any) -> None:
        super().pop(key, None)  # re-insert at the end so the dict stays oldest-first
        super().__setitem__(key, (value, time.monotonic()))
        if self.__maxlen is not None:
            while super().__len__() > self.__maxlen:
                super().__delitem__(next(iter(super().keys())))

    def __len__(self) -> int:
        self.__verify_cache_integrity()
        return super().__len__()

    def __iter__(self) -> Iterator[Any]:
        self.__verify_cache_integrity()
        return super().__iter__()

    def values(self) -> list[Any]:  # type: ignore[override]
        self.__verify_cache_integrity()
        return [x[0] for x in super().values()]

    def items(self) -> list[tuple[str, Any]]:  # type: ignore[override]
        self.__verify_cache_integrity()
        return [(x[0], x[1][0]) for x in super().items()]


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
    action: Callable[[Any], None] | None = None,
) -> Callable[
    [Callable[P, Coroutine[Any, Any, R] | R]],
    CacheProtocol[P, R],
]:
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
        Whether to leave keyword-only parameters out of the cache key. Defaults to `False`.
        Positional-or-keyword parameters are always keyed, however they are passed.
    action: Callable[[Any], None]
        Called on invalidation with the value that was invalidated. Defaults to `None`.
        Skipped for a coroutine entry that is still running or that failed.

    Returns
    -------
    Callable[[Callable[..., Coroutine[Any, Any, R] | R], CacheProtocol[R]]
        The actual decorator.

    Notes
    -----
    Coroutine results are cached as the :class:`asyncio.Task`, so concurrent misses for one
    key share a single in-flight call. A task that raises or is cancelled evicts itself, so
    a failed fetch is retried by the next caller instead of being served forever.
    """

    def decorator(func: Coro | NCoro) -> CacheProtocol[P, R]:
        """The actual decorator."""
        match strategy:
            case Strategy.LRU:
                _internal_cache = LRU(maxsize)
            case Strategy.RAW:
                _internal_cache = {}
            case Strategy.TIMED:
                # ponytail: fixed entry cap so per-query keys (search strings, pages) cannot grow
                # without bound inside one TTL; make it a parameter if a cache needs more.
                _internal_cache = ExpiringCache(maxsize, maxlen=TIMED_MAX_ENTRIES)
            case _:
                raise ValueError(f"Invalid cache strategy {strategy!r}.")

        _counters = {"hits": 0, "misses": 0}

        def _stats() -> tuple[int, int]:
            # (current size, capacity) for every strategy -- what ``stats.py`` unpacks. The
            # lru-dict ``get_stats`` returns (hits, misses) instead, so it is not used here.
            return len(_internal_cache), maxsize

        def _info() -> dict[str, int]:
            return {**_counters, "size": len(_internal_cache), "maxsize": maxsize}

        if not func.__doc__:
            # Add a default docstring if none is present
            func.__doc__ = DEFAULT_DOCSTRING

        def _true_repr(o: Any) -> str:
            if o.__class__.__repr__ is object.__repr__:
                return f"<{o.__class__.__module__}.{o.__class__.__name__}>"
            return repr(o)

        def _make_key(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
            """Generate a cache key from raw positional/keyword arguments.

            Fallback for calls that cannot be bound to the signature.
            """
            key_parts = [f"{func.__module__}.{func.__name__}"]

            for arg in args:
                key_parts.append(_true_repr(arg))  # noqa: PERF401

            if not ignore_kwargs:
                for k in kwargs:
                    if k in ("connection", "pool"):
                        continue
                    key_parts.append(f"{_true_repr(k)}={_true_repr(kwargs[k])}")

            return ":".join(key_parts)

        # Compute once at decoration time rather than on every call.
        _signature = inspect.signature(func)
        _skip_first_param: bool = any(p.name in ("self", "cls") for p in _signature.parameters.values())
        _is_coroutine: bool = inspect.iscoroutinefunction(func)
        # A view of the signature with the bound ``self``/``cls`` removed, so that a
        # call's *logical* arguments (everything after ``self``) can be bound and
        # normalised regardless of whether they were passed positionally or by keyword.
        _sig_logical = _signature.replace(
            parameters=[p for p in _signature.parameters.values() if p.name not in ("self", "cls")]
        )
        _skipped_names = {"connection", "pool"}
        if ignore_kwargs:
            _skipped_names |= {
                p.name for p in _sig_logical.parameters.values() if p.kind is inspect.Parameter.KEYWORD_ONLY
            }

        def _key_for_call(logical_args: tuple[Any, ...], logical_kwargs: dict[str, Any]) -> str:
            """Build a cache key that is invariant to positional/keyword call style.

            ``logical_args`` must already have ``self``/``cls`` stripped. Binding the
            arguments to the signature (with defaults applied) means ``f(123)``,
            ``f(guild_id=123)`` and ``f(123, flag=<default>)`` all map to the same key, so
            signal-based invalidation (which always fires positionally) reliably busts
            entries cached by keyword callers, and vice versa.
            """
            try:
                bound = _sig_logical.bind(*logical_args, **logical_kwargs)
            except TypeError:
                # *args/**kwargs or arity mismatch — fall back to the raw key.
                return _make_key(logical_args, logical_kwargs)
            bound.apply_defaults()
            key_parts = [f"{func.__module__}.{func.__name__}"]
            for name, value in bound.arguments.items():
                if name in _skipped_names:
                    continue
                key_parts.append(f"{name}={_true_repr(value)}")
            return ":".join(key_parts)

        def _evict_on_failure(key: str, task: asyncio.Task[Any]) -> None:
            if not (task.cancelled() or task.exception() is not None):
                return
            # Only drop the entry if it is still this task (it may have been invalidated
            # and replaced meanwhile).
            try:
                if _internal_cache[key] is task:
                    del _internal_cache[key]
            except KeyError:
                pass

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> asyncio.Task[R] | R:
            """The actual wrapper for the cache to be assigned to the corresponding function."""

            # Strip the bound ``self``/``cls`` (always the first positional) before keying.
            logical_args = args[1:] if _skip_first_param else args
            key = _key_for_call(logical_args, kwargs)

            try:
                task = _internal_cache[key]
            except KeyError:
                _counters["misses"] += 1
                if _is_coroutine:
                    task = asyncio.create_task(func(*args, **kwargs))
                    _internal_cache[key] = task
                    task.add_done_callback(functools.partial(_evict_on_failure, key))
                else:
                    _internal_cache[key] = task = func(*args, **kwargs)
                return task
            else:
                _counters["hits"] += 1
                return task

        def _run_action(item: Any) -> None:
            if action is None:
                return
            if isinstance(item, asyncio.Task):
                if not item.done() or item.cancelled() or item.exception() is not None:
                    return
                item = item.result()
            action(item)

        def _invalidate(*args: Any, **kwargs: Any) -> bool:
            """Invalidate a cache entry.

            Callers pass the *logical* arguments (no ``self``), matching how the wrapper
            keys entries after stripping ``self``.
            """
            try:
                item = _internal_cache.pop(_key_for_call(args, kwargs))
            except KeyError:
                return False
            _run_action(item)
            return True

        def _invalidate_containing(key: str) -> None:
            """Invalidate all cache entries containing the given key."""
            for k in [k for k in list(_internal_cache.keys()) if key in k]:
                try:
                    item = _internal_cache.pop(k)
                except KeyError:
                    continue
                _run_action(item)

        def _get_key(*args: Any, **kwargs: Any) -> str:
            """Get the cache key for the given (logical, ``self``-less) arguments."""
            return _key_for_call(args, kwargs)

        wrapper: Callable[P, asyncio.Task[R] | R]
        result = cast('CacheProtocol[P, R]', wrapper)
        result.cache = _internal_cache
        result.get_key = _get_key
        result.get_stats = _stats
        result.cache_info = _info
        result.invalidate = _invalidate
        result.invalidate_containing = _invalidate_containing
        return result

    return decorator
