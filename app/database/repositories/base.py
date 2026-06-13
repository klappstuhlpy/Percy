from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable

    import asyncpg

    from app.core import Bot
    from app.database.base import Database

__all__ = ('BaseRepository',)


class BaseRepository:
    """The base class for all data-access repositories.

    A repository groups together the SQL queries for a single domain (moderation,
    guilds, users, ...) so that cogs and UI code never have to write raw SQL. Each
    repository is handed the shared :class:`~app.database.base.Database` instance and
    reuses its connection pool, exposing thin ``execute``/``fetch`` wrappers as well as
    ``acquire`` for transactions.

    Subclasses should only add domain-specific query methods that return plain data
    (records, scalars, primitives) and keep all Discord-specific mapping in the caller.
    """

    __slots__ = ('db',)

    def __init__(self, database: Database) -> None:
        self.db = database

    @property
    def bot(self) -> Bot:
        """:class:`~app.core.Bot`: The bot instance that owns the database."""
        return self.db.bot

    def acquire(self, *, timeout: float | None = None) -> asyncpg.pool.PoolAcquireContext:
        """Acquires a connection from the pool, for use in transactions."""
        return self.db.acquire(timeout=timeout)

    def execute(self, query: str, *args: Any, timeout: float | None = None) -> Awaitable[str]:
        return self.db.execute(query, *args, timeout=timeout)

    def fetch(self, query: str, *args: Any, timeout: float | None = None) -> Awaitable[list[Any]]:
        return self.db.fetch(query, *args, timeout=timeout)

    def fetchrow(self, query: str, *args: Any, timeout: float | None = None) -> Awaitable[asyncpg.Record]:
        return self.db.fetchrow(query, *args, timeout=timeout)

    def fetchval(self, query: str, *args: Any, column: str | int = 0, timeout: float | None = None) -> Awaitable[Any]:
        return self.db.fetchval(query, *args, column=column, timeout=timeout)

    def invalidate_cache(self, signal_name: str, *args: Any) -> int:
        """Fire a cache invalidation signal by name.

        Repositories call this after mutating data to auto-bust related caches.
        Returns the number of caches that were actually invalidated.
        """
        return self.db.signals.fire(signal_name, *args)
