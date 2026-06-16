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

    Generic helpers
    ---------------
    - :meth:`update_returning` — parameterized ``UPDATE ... SET ... WHERE pk RETURNING *``.
      Use when a repository method updates a row by primary key and needs the refreshed
      record back (replaces per-table boilerplate).
    - :meth:`delete_where` — parameterized ``DELETE FROM ... WHERE pk``. Use for simple
      primary-key deletions (replaces one-line ``execute("DELETE ...")`` methods).
    - :meth:`invalidate_cache` — fires a named cache signal on the shared
      :class:`~app.utils.signals.CacheSignalHub` so the memoized config getters stay
      consistent after mutations.

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

    async def update_returning(
        self,
        table: str,
        pk_columns: tuple[str, ...],
        pk_values: tuple[Any, ...],
        values: dict[str, Any],
        *,
        connection: asyncpg.Connection | None = None,
    ) -> asyncpg.Record | None:
        """Generic ``UPDATE ... SET ... WHERE ... RETURNING *`` helper.

        Builds a parameterized UPDATE targeting ``table`` where ``pk_columns``
        match ``pk_values``, setting every key in ``values`` to the supplied value.
        Returns the full updated row (or ``None`` if the WHERE matched nothing).

        Parameters
        ----------
        table:
            The table name to update.
        pk_columns:
            Column names forming the WHERE clause (e.g. ``("id",)`` or
            ``("user_id", "guild_id")`` for composite keys).
        pk_values:
            Corresponding values for each pk column, in the same order.
        values:
            Column-name → new-value mapping for the SET clause.
        connection:
            Optional connection override (for use inside transactions).
        """
        n_pk = len(pk_columns)
        set_clause = ", ".join(
            f'"{col}" = ${i}' for i, col in enumerate(values.keys(), start=n_pk + 1)
        )
        where_clause = " AND ".join(
            f'"{col}" = ${i}' for i, col in enumerate(pk_columns, start=1)
        )
        query = f'UPDATE "{table}" SET {set_clause} WHERE {where_clause} RETURNING *;'
        return await (connection or self.db).fetchrow(query, *pk_values, *values.values())

    async def delete_where(
        self,
        table: str,
        pk_columns: tuple[str, ...],
        pk_values: tuple[Any, ...],
        *,
        connection: asyncpg.Connection | None = None,
    ) -> str:
        """Generic ``DELETE FROM ... WHERE ...`` helper.

        Builds a parameterized DELETE targeting ``table`` where ``pk_columns``
        match ``pk_values``. Returns the asyncpg status string (e.g. ``'DELETE 1'``).

        Parameters
        ----------
        table:
            The table name to delete from.
        pk_columns:
            Column names forming the WHERE clause.
        pk_values:
            Corresponding values for each pk column, in the same order.
        connection:
            Optional connection override (for use inside transactions).
        """
        where_clause = " AND ".join(
            f'"{col}" = ${i}' for i, col in enumerate(pk_columns, start=1)
        )
        query = f'DELETE FROM "{table}" WHERE {where_clause};'
        return await (connection or self.db).execute(query, *pk_values)
