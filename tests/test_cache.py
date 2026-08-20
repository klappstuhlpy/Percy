"""Tests for the :func:`app.utils.cache.cache` memoization decorator.

The key regression these guard against: cache keys must be invariant to whether an
argument is passed positionally or by keyword. Percy's cached getters
(``get_guild_config`` / ``get_guild_sentinel``) are called both ways across the
codebase, while signal-based invalidation always fires positionally. If the key
generation distinguished ``f(123)`` from ``f(guild_id=123)``, keyword-cached entries
would never be busted -- which is exactly the sentinel "stale config" bug.
"""

from __future__ import annotations

from app.utils import cache


async def test_positional_and_keyword_calls_share_cache_entry() -> None:
    calls: list[int] = []

    class Service:
        @cache.cache()
        async def get(self, guild_id: int) -> object:
            calls.append(guild_id)
            return object()

    service = Service()
    positional = await service.get(123)
    keyword = await service.get(guild_id=123)

    assert positional is keyword  # same cached object
    assert calls == [123]  # computed exactly once


async def test_positional_invalidate_busts_keyword_cached_entry() -> None:
    calls: list[int] = []

    class Service:
        @cache.cache()
        async def get(self, guild_id: int) -> int:
            calls.append(guild_id)
            return guild_id * 2

    service = Service()
    await service.get(guild_id=5)  # cached via keyword call

    # Signal-style invalidation fires positionally; it must still match.
    assert Service.get.invalidate(5) is True

    await service.get(guild_id=5)
    assert calls == [5, 5]  # recomputed after invalidation


async def test_keyword_invalidate_busts_positional_cached_entry() -> None:
    class Service:
        @cache.cache()
        async def get(self, guild_id: int) -> int:
            return guild_id

    service = Service()
    await service.get(7)  # cached via positional call

    assert Service.get.invalidate(guild_id=7) is True
    assert Service.get.invalidate(guild_id=7) is False  # already gone


async def test_action_receives_cached_value_on_invalidate() -> None:
    seen: list[str] = []

    class Service:
        @cache.cache(action=seen.append)
        async def get(self, guild_id: int) -> str:
            return f"val-{guild_id}"

    service = Service()
    await service.get(1)
    Service.get.invalidate(guild_id=1)

    assert seen == ["val-1"]


async def test_ignore_kwargs_excludes_keyword_only_args() -> None:
    calls: list[tuple[int, bool]] = []

    class Service:
        @cache.cache(ignore_kwargs=True)
        async def check(self, guild_id: int, *, flag: bool = True) -> bool:
            calls.append((guild_id, flag))
            return flag

    service = Service()
    await service.check(1, flag=True)
    await service.check(1, flag=False)  # keyword-only arg ignored in the key

    assert calls == [(1, True)]  # second call served from cache


async def test_timed_strategy_returns_the_cached_value_not_the_internal_pair() -> None:
    """``ExpiringCache`` stores ``(value, inserted_at)``; a cache hit must unwrap it.

    Before this was fixed, the second call returned the raw tuple, so an awaited cached
    coroutine raised ``TypeError: object tuple can't be used in 'await' expression``.
    """
    calls: list[int] = []

    class Service:
        @cache.cache(maxsize=60, strategy=cache.Strategy.TIMED)
        async def get(self, guild_id: int) -> str:
            calls.append(guild_id)
            return f"val-{guild_id}"

    service = Service()
    assert await service.get(1) == "val-1"
    assert await service.get(1) == "val-1"  # served from the cache, still unwrapped
    assert calls == [1]


async def test_timed_strategy_invalidate_passes_the_value_to_the_action() -> None:
    seen: list[str] = []

    class Service:
        @cache.cache(maxsize=60, strategy=cache.Strategy.TIMED, action=seen.append)
        async def get(self, guild_id: int) -> str:
            return f"val-{guild_id}"

    service = Service()
    await service.get(1)
    assert Service.get.invalidate(1) is True
    assert seen == ["val-1"]
    assert Service.get.invalidate(1) is False  # already gone
