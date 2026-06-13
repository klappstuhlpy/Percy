from __future__ import annotations

import logging
from typing import Any

__all__ = ("CacheSignal", "CacheSignalHub")

log = logging.getLogger(__name__)


class CacheSignal:
    """A signal that can invalidate associated caches when fired.

    Subscribers register cache functions and the arguments to invalidate.
    When the signal fires, all subscribed invalidation callbacks run.
    """

    __slots__ = ("_name", "_subscribers")

    def __init__(self, name: str) -> None:
        self._name = name
        self._subscribers: list[tuple[Any, tuple[Any, ...]]] = []

    def connect(self, cached_func: Any, *args: Any) -> None:
        """Register a cached function to be invalidated when this signal fires.

        Parameters
        ----------
        cached_func:
            A function decorated with ``@cache.cache()`` (must have ``.invalidate``).
        *args:
            The positional arguments to pass to ``.invalidate()`` — typically the
            guild_id or user_id that identifies the cached entry.
            Use ``None`` as a placeholder for dynamic args passed at fire time.
        """
        self._subscribers.append((cached_func, args))

    def fire(self, *dynamic_args: Any) -> int:
        """Fire the signal, invalidating all connected caches.

        Parameters
        ----------
        *dynamic_args:
            Replaces any ``None`` placeholders in subscriber args, left-to-right.

        Returns
        -------
        int
            Number of caches that were actually invalidated (had a matching entry).
        """
        invalidated = 0
        for cached_func, static_args in self._subscribers:
            resolved = []
            dynamic_iter = iter(dynamic_args)
            for arg in static_args:
                if arg is None:
                    resolved.append(next(dynamic_iter, None))
                else:
                    resolved.append(arg)

            try:
                if cached_func.invalidate(*resolved):
                    invalidated += 1
            except Exception as e:
                log.warning("Signal %r: invalidation failed for %r: %s", self._name, cached_func, e)

        return invalidated

    def __repr__(self) -> str:
        return f"<CacheSignal name={self._name!r} subscribers={len(self._subscribers)}>"


class CacheSignalHub:
    """Central registry of named cache invalidation signals.

    Usage::

        hub = CacheSignalHub()

        # In Database setup:
        hub.register("guild_config_changed")
        hub["guild_config_changed"].connect(db.get_guild_config, None)

        # In repository after mutation:
        hub.fire("guild_config_changed", guild_id)
    """

    def __init__(self) -> None:
        self._signals: dict[str, CacheSignal] = {}

    def register(self, name: str) -> CacheSignal:
        """Register (or retrieve) a named signal."""
        if name not in self._signals:
            self._signals[name] = CacheSignal(name)
        return self._signals[name]

    def fire(self, name: str, *args: Any) -> int:
        """Fire a named signal with dynamic arguments."""
        signal = self._signals.get(name)
        if signal is None:
            return 0
        return signal.fire(*args)

    def __getitem__(self, name: str) -> CacheSignal:
        return self._signals[name]

    def __contains__(self, name: str) -> bool:
        return name in self._signals

    @property
    def registered(self) -> list[str]:
        return list(self._signals.keys())
