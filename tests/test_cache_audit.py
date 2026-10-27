"""Regression tests for the cache audit (2026-10-27): failed fetches, defaults, ignore_kwargs, TIMED bounds."""

from __future__ import annotations

import asyncio

import pytest

from app.utils import cache


async def test_failed_fetch_is_not_cached() -> None:
    calls: list[int] = []

    class Service:
        @cache.cache()
        async def get(self, guild_id: int) -> int:
            calls.append(guild_id)
            if len(calls) == 1:
                raise RuntimeError("db down")
            return guild_id

    service = Service()
    with pytest.raises(RuntimeError):
        await service.get(1)
    await asyncio.sleep(0)  # let the done-callback run
    assert await service.get(1) == 1
    assert calls == [1, 1]


async def test_cancelled_fetch_is_not_cached() -> None:
    gate = asyncio.Event()

    class Service:
        @cache.cache()
        async def get(self, guild_id: int) -> int:
            await gate.wait()
            return guild_id

    service = Service()
    task = service.get(1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    gate.set()
    assert await service.get(1) == 1


async def test_concurrent_misses_share_one_call() -> None:
    calls: list[int] = []

    class Service:
        @cache.cache()
        async def get(self, guild_id: int) -> int:
            calls.append(guild_id)
            await asyncio.sleep(0)
            return guild_id

    service = Service()
    assert await asyncio.gather(service.get(1), service.get(guild_id=1)) == [1, 1]
    assert calls == [1]
    assert Service.get.cache_info()["hits"] == 1


async def test_explicit_default_shares_key_with_omitted_default() -> None:
    class Service:
        @cache.cache()
        async def get(self, guild_id: int, verbose: bool = False) -> int:
            return guild_id

    assert Service.get.get_key(1) == Service.get.get_key(1, verbose=False) == Service.get.get_key(1, False)


async def test_ignore_kwargs_still_keys_positional_params_passed_by_keyword() -> None:
    """``is_plonked(gid, mid, channel=ch)`` must not share one entry across every channel."""

    class Service:
        @cache.cache(ignore_kwargs=True)
        async def check(self, guild_id: int, member_id: int, channel: int | None = None, *, bypass: bool = True) -> int:
            return channel or 0

    service = Service()
    assert await service.check(1, 2, channel=10) == 10
    assert await service.check(1, 2, channel=20) == 20
    assert Service.check.get_key(1, 2, channel=10, bypass=False) == Service.check.get_key(1, 2, 10)


async def test_invalidate_containing_guild_prefix_still_matches() -> None:
    class Service:
        @cache.cache(ignore_kwargs=True)
        async def check(self, guild_id: int, member_id: int, channel: int | None = None) -> bool:
            return True

    service = Service()
    await service.check(123, 456, channel=None)
    Service.check.invalidate_containing(f"{123!r}:")
    assert Service.check.get_stats()[0] == 0


async def test_action_is_skipped_for_a_pending_task() -> None:
    seen: list[int] = []
    gate = asyncio.Event()

    class Service:
        @cache.cache(action=seen.append)
        async def get(self, guild_id: int) -> int:
            await gate.wait()
            return guild_id

    service = Service()
    task = service.get(1)
    assert Service.get.invalidate(1) is True  # used to raise InvalidStateError
    gate.set()
    await task
    assert seen == []


def test_get_stats_is_size_and_capacity_for_lru() -> None:
    @cache.cache(maxsize=10)
    def get(x: int) -> int:
        return x

    get(1)
    get(1)
    get(2)
    assert get.get_stats() == (2, 10)
    assert get.cache_info() == {"hits": 1, "misses": 2, "size": 2, "maxsize": 10}


def test_expiring_cache_maxlen_evicts_oldest_and_rewrite_refreshes_order(monkeypatch) -> None:
    now = [0.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: now[0])
    c = cache.ExpiringCache(seconds=10.0, maxlen=2)
    c[1] = "a"
    now[0] = 1.0
    c[2] = "b"
    now[0] = 2.0
    c[1] = "a2"  # moves 1 to the end
    c[3] = "c"  # evicts 2, the oldest
    assert sorted(c) == [1, 3]
    now[0] = 11.5  # 1 (t=2) and 3 (t=2) still live
    assert c.get(1) == "a2"
    now[0] = 12.5
    assert len(c) == 0
