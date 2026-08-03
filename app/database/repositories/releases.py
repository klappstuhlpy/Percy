"""Data access for the comic catalogue: ``comic_releases``, ``comic_creators`` and
``comic_characters`` (see ``migrations/V40__comic_catalogue.sql``).

Separate from :class:`~app.database.repositories.content.ComicsRepository`, which owns
``comic_config`` -- the per-guild feed *schedule*. This one owns the *content*: what was
published, by whom, about whom. None of these tables are behind a ``@cache.cache()`` getter,
so methods here never fire cache-invalidation signals.

Named ``releases`` rather than ``comics`` because film/TV releases join these tables' shape in
a later phase; the subscription and delivery tables land here too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import asyncpg

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import datetime
    from collections.abc import Sequence

__all__ = ('ReleasesRepository',)

#: Columns :meth:`ReleasesRepository.upsert_release` writes, in statement order.
_RELEASE_COLUMNS = (
    'brand', 'external_id', 'slug', 'title', 'series_slug', 'series_name', 'issue_number',
    'release_date', 'cover_url', 'description', 'price', 'page_count', 'format', 'publisher',
    'url',
)

#: Everything except the identity columns -- what an upsert of a known issue refreshes.
_RELEASE_UPDATE_COLUMNS = tuple(c for c in _RELEASE_COLUMNS if c not in ('brand', 'external_id'))

#: Hard ceiling on a single browse page, so a bad ``limit`` cannot ask for the whole archive.
MAX_PAGE_SIZE = 100


class ReleasesRepository(BaseRepository):
    """Data access for the persisted comic catalogue.

    Split into the ingest (write) side, which is safe to re-run over unchanged issues, and the
    browse (read) side used by the Discord commands and the internal API.
    """

    # -- Ingest (write) ----------------------------------------------------

    async def upsert_release(self, row: dict[str, Any]) -> tuple[int, bool]:
        """Inserts or refreshes one release, returning ``(id, created)``.

        Identity is ``(brand, external_id)``. ``first_seen`` is set by the table default on
        insert and never updated, so "new this week" survives a restart; ``updated_at`` moves
        on every write.

        ``slug`` also carries a ``UNIQUE (brand, slug)`` constraint, and two different issues
        in one brand can slugify identically (a reprint under the same title, an annual with
        no issue number). That collides on the *slug* index rather than the identity index, so
        it is retried once with the external id appended -- deterministic from then on,
        because the resolved slug is what gets stored.
        """
        try:
            return await self._upsert_release(row)
        except asyncpg.UniqueViolationError:
            return await self._upsert_release({**row, 'slug': f'{row["slug"]}-{row["external_id"]}'})

    async def _upsert_release(self, row: dict[str, Any]) -> tuple[int, bool]:
        placeholders = ', '.join(f'${i}' for i in range(1, len(_RELEASE_COLUMNS) + 1))
        assignments = ', '.join(f'{column} = EXCLUDED.{column}' for column in _RELEASE_UPDATE_COLUMNS)
        query = f"""
            INSERT INTO comic_releases ({', '.join(_RELEASE_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT (brand, external_id) DO UPDATE
                SET {assignments}, updated_at = (now() AT TIME ZONE 'utc')
            RETURNING id, (xmax = 0) AS created;
        """
        record = await self.fetchrow(query, *(row.get(column) for column in _RELEASE_COLUMNS))
        return record['id'], record['created']

    async def replace_creators(self, release_id: int, creators: Sequence[tuple[str, str]]) -> None:
        """Replaces a release's credits with ``(name, role)`` pairs.

        Delete-then-insert rather than upsert: a corrected credit list that *drops* a name has
        to actually drop it, which an upsert alone would not do.
        """
        async with self.acquire() as connection, connection.transaction():
            await connection.execute("DELETE FROM comic_creators WHERE release_id = $1;", release_id)
            if creators:
                await connection.executemany(
                    "INSERT INTO comic_creators (release_id, name, role) VALUES ($1, $2, $3);",
                    [(release_id, name, role) for name, role in creators],
                )

    async def replace_characters(self, release_id: int, characters: Sequence[tuple[str, str | None]]) -> None:
        """Replaces a release's character list with ``(name, kind)`` pairs."""
        async with self.acquire() as connection, connection.transaction():
            await connection.execute("DELETE FROM comic_characters WHERE release_id = $1;", release_id)
            if characters:
                await connection.executemany(
                    "INSERT INTO comic_characters (release_id, name, kind) VALUES ($1, $2, $3);",
                    [(release_id, name, kind) for name, kind in characters],
                )

    # -- Browse (read) -----------------------------------------------------

    async def list_releases(
        self,
        *,
        brand: str | None = None,
        series: str | None = None,
        creator: str | None = None,
        character: str | None = None,
        since: datetime.date | None = None,
        until: datetime.date | None = None,
        search: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> list[asyncpg.Record]:
        """Fetches releases newest-first, filtered by any combination of the arguments.

        ``series`` is a ``series_slug``; ``creator`` and ``character`` are names matched
        case-insensitively (the ``lower(name)`` indexes). ``search`` is a trigram-friendly
        substring match on the title.
        """
        where, args = self._filters(
            brand=brand, series=series, creator=creator, character=character,
            since=since, until=until, search=search)
        args.extend((min(max(limit, 1), MAX_PAGE_SIZE), max(offset, 0)))
        query = f"""
            SELECT * FROM comic_releases
            {where}
            ORDER BY release_date DESC NULLS LAST, id DESC
            LIMIT ${len(args) - 1} OFFSET ${len(args)};
        """
        return await self.fetch(query, *args)

    async def count_releases(
        self,
        *,
        brand: str | None = None,
        series: str | None = None,
        creator: str | None = None,
        character: str | None = None,
        since: datetime.date | None = None,
        until: datetime.date | None = None,
        search: str | None = None,
    ) -> int:
        """Counts the releases :meth:`list_releases` would page through."""
        where, args = self._filters(
            brand=brand, series=series, creator=creator, character=character,
            since=since, until=until, search=search)
        return await self.fetchval(f"SELECT COUNT(*) FROM comic_releases {where};", *args) or 0

    @staticmethod
    def _filters(
        *,
        brand: str | None,
        series: str | None,
        creator: str | None,
        character: str | None,
        since: datetime.date | None,
        until: datetime.date | None,
        search: str | None,
    ) -> tuple[str, list[Any]]:
        """Builds the shared ``WHERE`` clause for the list/count pair.

        Both must filter identically or a page count disagrees with its page, so the clause is
        built once here rather than written twice.
        """
        clauses: list[str] = []
        args: list[Any] = []

        def add(clause: str, value: Any) -> None:
            args.append(value)
            clauses.append(clause.format(n=len(args)))

        if brand is not None:
            add('brand = ${n}', brand)
        if series is not None:
            add('series_slug = ${n}', series)
        if since is not None:
            add('release_date >= ${n}', since)
        if until is not None:
            add('release_date <= ${n}', until)
        if search:
            add('title ILIKE ${n}', f'%{search}%')
        if creator is not None:
            add('EXISTS (SELECT 1 FROM comic_creators c WHERE c.release_id = comic_releases.id '
                'AND lower(c.name) = lower(${n}))', creator)
        if character is not None:
            add('EXISTS (SELECT 1 FROM comic_characters ch WHERE ch.release_id = comic_releases.id '
                'AND lower(ch.name) = lower(${n}))', character)

        return (f'WHERE {" AND ".join(clauses)}' if clauses else ''), args

    async def get_release(self, brand: str, slug: str) -> asyncpg.Record | None:
        """Fetches one release by its brand-scoped slug."""
        return await self.fetchrow(
            "SELECT * FROM comic_releases WHERE brand = $1 AND slug = $2;", brand, slug)

    async def get_credits(self, release_id: int) -> list[asyncpg.Record]:
        """Fetches a release's credits, ordered by role then name."""
        return await self.fetch(
            "SELECT name, role FROM comic_creators WHERE release_id = $1 ORDER BY role, name;", release_id)

    async def get_characters(self, release_id: int) -> list[asyncpg.Record]:
        """Fetches a release's characters, ordered by name."""
        return await self.fetch(
            "SELECT name, kind FROM comic_characters WHERE release_id = $1 ORDER BY name;", release_id)

    async def list_series(self, brand: str, *, limit: int = 200) -> list[asyncpg.Record]:
        """Fetches a brand's series with issue counts and latest release date."""
        query = """
            SELECT series_slug, MIN(series_name) AS series_name,
                   COUNT(*) AS issue_count, MAX(release_date) AS latest
            FROM comic_releases
            WHERE brand = $1 AND series_slug IS NOT NULL
            GROUP BY series_slug
            ORDER BY latest DESC NULLS LAST
            LIMIT $2;
        """
        return await self.fetch(query, brand, limit)

    async def list_names(self, table: str, *, brand: str | None = None, limit: int = 200) -> list[asyncpg.Record]:
        """Fetches distinct creator or character names with appearance counts.

        ``table`` must be ``'comic_creators'`` or ``'comic_characters'`` -- it is interpolated
        into the statement, so it is checked against that pair rather than trusted.
        """
        if table not in ('comic_creators', 'comic_characters'):
            raise ValueError(f'unknown name table {table!r}')
        query = f"""
            SELECT n.name, COUNT(*) AS appearances
            FROM {table} n
            JOIN comic_releases r ON r.id = n.release_id
            WHERE ($1::text IS NULL OR r.brand = $1)
            GROUP BY n.name
            ORDER BY appearances DESC, n.name
            LIMIT $2;
        """
        return await self.fetch(query, brand, limit)
