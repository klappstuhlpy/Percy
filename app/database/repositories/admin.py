from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import asyncpg

__all__ = ('AdminRepository',)


class AdminRepository(BaseRepository):
    """Database introspection queries for the owner-only ``Admin`` cog.

    These are fixed maintenance/diagnostic queries against the PostgreSQL catalog
    and ``INFORMATION_SCHEMA``; they do not touch any domain table. The arbitrary
    SQL console (``sql``) intentionally stays in the cog, as it evaluates
    user-supplied statements rather than a fixed query.
    """

    async def get_table_schema(self, table_name: str) -> list[asyncpg.Record]:
        """Describes the columns of a table."""
        query = """
            SELECT column_name, data_type, column_default, is_nullable
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE table_name = $1
            ORDER BY ordinal_position;
        """
        return await self.fetch(query, table_name)

    async def list_tables(self) -> list[asyncpg.Record]:
        """Lists all base tables in the public schema."""
        query = """
            SELECT table_name
            FROM INFORMATION_SCHEMA.TABLES
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """
        return await self.fetch(query)

    async def get_table_sizes(self) -> list[asyncpg.Record]:
        """Returns the 20 largest relations with their on-disk size and row estimate."""
        query = """
            SELECT nspname || '.' || relname                   AS "relation",
                   pg_size_pretty(pg_relation_size(C.oid))     AS "size",
                   COALESCE(pg_row_estimate(relname::text), 0) AS "rows"
            FROM pg_class C
                     LEFT JOIN pg_namespace N ON (N.oid = C.relnamespace)
            WHERE nspname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY pg_relation_size(C.oid) DESC
            LIMIT 20;
        """
        return await self.fetch(query)

    async def explain_query(self, query: str, *, analyze: bool) -> asyncpg.Record | None:
        """Returns the JSON ``EXPLAIN`` plan for a query, optionally running ``ANALYZE``."""
        options = 'ANALYZE, COSTS, VERBOSE, BUFFERS, FORMAT JSON' if analyze else 'COSTS, VERBOSE, FORMAT JSON'
        return await self.fetchrow(f'EXPLAIN ({options})\n{query}')
