"""Tests for the database connector's query path and pool diagnostics.

These exercise :class:`~app.database.base._Database` in isolation — instantiated via
``object.__new__`` so no real PostgreSQL pool or event-loop connection is needed — to verify
that every query is timed/tracked, that failures are re-raised (and still recorded), that the
``pool`` accessor fails clearly before initialisation, and that ``pool_stats`` maps the pool's
internals into a typed snapshot.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest

from app.database.base import PoolConnectionState, PoolStats, _Database
from app.utils.query_tracker import QueryTracker


class FakeQueue:
    def __init__(self, waiters: int) -> None:
        self._getters = list(range(waiters))


class FakeCon:
    def is_closed(self) -> bool:
        return False


class FakeHolder:
    def __init__(self, *, generation: int, in_use: bool, closed: bool) -> None:
        self._generation = generation
        self._in_use = object() if in_use else None
        self._con = None if closed else FakeCon()


class FakePool:
    """Stands in for ``asyncpg.Pool`` — records calls and exposes the introspection surface."""

    def __init__(self, *, raise_on: str | None = None) -> None:
        self.calls: list[tuple] = []
        self.raise_on = raise_on
        self._holders = [
            FakeHolder(generation=1, in_use=True, closed=False),
            FakeHolder(generation=1, in_use=False, closed=False),
            FakeHolder(generation=0, in_use=False, closed=True),
        ]
        self._queue = FakeQueue(waiters=2)
        self._generation = 1

    async def _maybe_raise(self, op: str) -> None:
        if self.raise_on == op:
            raise asyncpg.PostgresError("boom")

    async def execute(self, query: str, *args: object, timeout: float | None = None) -> str:
        self.calls.append(("execute", query, args, timeout))
        await self._maybe_raise("execute")
        return "EXECUTE 1"

    async def fetch(self, query: str, *args: object, timeout: float | None = None) -> list[int]:
        self.calls.append(("fetch", query, args, timeout))
        return [1, 2, 3]

    async def fetchrow(self, query: str, *args: object, timeout: float | None = None) -> dict[str, int]:
        self.calls.append(("fetchrow", query, args, timeout))
        return {"a": 1}

    async def fetchval(self, query: str, *args: object, column: int = 0, timeout: float | None = None) -> int:
        self.calls.append(("fetchval", query, args, column, timeout))
        return 42

    def get_size(self) -> int:
        return 3

    def get_idle_size(self) -> int:
        return 1

    def get_min_size(self) -> int:
        return 2

    def get_max_size(self) -> int:
        return 5


def make_db(pool: FakePool | None) -> _Database:
    db = object.__new__(_Database)
    db._internal_pool = pool  # type: ignore[assignment]
    db._ready = asyncio.Event()
    db._ready.set()
    db.query_tracker = QueryTracker(threshold_ms=100.0)
    return db


async def test_query_helpers_delegate_and_track() -> None:
    pool = FakePool()
    db = make_db(pool)

    assert await db.execute("SELECT 1") == "EXECUTE 1"
    assert await db.fetch("SELECT 2") == [1, 2, 3]
    assert await db.fetchrow("SELECT 3") == {"a": 1}
    assert await db.fetchval("SELECT 4", column=2) == 42

    assert [c[0] for c in pool.calls] == ["execute", "fetch", "fetchrow", "fetchval"]
    assert pool.calls[-1] == ("fetchval", "SELECT 4", (), 2, None)  # column forwarded
    assert db.query_tracker.total_queries == 4


async def test_failed_query_is_recorded_and_reraised() -> None:
    db = make_db(FakePool(raise_on="execute"))

    with pytest.raises(asyncpg.PostgresError):
        await db.execute("BAD SQL")

    # Even though it failed, the attempt is still timed/recorded.
    assert db.query_tracker.total_queries == 1


async def test_pool_property_errors_before_initialised() -> None:
    db = make_db(None)
    with pytest.raises(RuntimeError, match="not initialised"):
        _ = db.pool


def test_pool_stats_snapshots_internals() -> None:
    db = make_db(FakePool())

    stats = db.pool_stats()

    assert isinstance(stats, PoolStats)
    assert (stats.size, stats.idle, stats.in_use) == (3, 1, 2)
    assert (stats.min_size, stats.max_size) == (2, 5)
    assert stats.waiting == 2
    assert stats.generation == 1
    assert stats.connections == (
        PoolConnectionState(generation=1, in_use=True, is_closed=False),
        PoolConnectionState(generation=1, in_use=False, is_closed=False),
        PoolConnectionState(generation=0, in_use=False, is_closed=True),
    )
