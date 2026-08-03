"""Data access for the universe watchlist feature: the TMDB-synced catalogue
(``watch_universes``, ``watch_titles``, ``watch_paths``, ``watch_title_importance``,
``watch_providers``) and per-user progress/prefs (``watch_progress``, ``watch_prefs``).

None of these tables are behind a ``@cache.cache()`` getter, so methods here never
fire cache-invalidation signals. Methods return raw records/scalars; the ingest
service and the internal-API layer own any further shaping.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import datetime
    from collections.abc import Sequence

    import asyncpg

__all__ = ('WatchlistRepository',)

#: ``sort`` -> title ordering column for :meth:`WatchlistRepository.list_titles`.
_TITLE_SORT_COLUMNS = {'story': 'story_order', 'release': 'release_order'}

#: Valid ``watch_progress.status`` values.
_PROGRESS_STATUSES = ('watched', 'skipped')

#: Valid ``watch_title_importance.importance`` values.
_IMPORTANCE_VALUES = ('essential', 'recommended', 'optional')

#: Columns callers may write via :meth:`WatchlistRepository.upsert_prefs`.
_PREFS_COLUMNS = (
    'region', 'path_slug', 'sort_mode', 'show_spoiler', 'with_credits', 'hide_sub',
    'weekly_hours', 'target_date',
)


class WatchlistRepository(BaseRepository):
    """Data access for the universe watchlist tables.

    Split into three groups: the read-side catalogue browsing queries, the
    ingest (write) methods used by the TMDB sync CLI (all safe to re-run), and
    per-user progress/prefs. All SQL for these tables lives here.
    """

    # -- Universes / catalogue (read) --------------------------------------

    async def list_universes(self, *, published_only: bool = True) -> list[asyncpg.Record]:
        """Fetches universes with aggregate ``title_count`` and ``total_runtime``.

        Ordered by ``sort_order`` then ``name``. When ``published_only`` is set (the
        default), unpublished universes are excluded.
        """
        query = """
            SELECT
                u.*,
                COUNT(t.id) AS title_count,
                COALESCE(SUM(t.runtime), 0) AS total_runtime
            FROM watch_universes u
            LEFT JOIN watch_titles t ON t.universe = u.slug
            WHERE (NOT $1::boolean OR u.published)
            GROUP BY u.slug
            ORDER BY u.sort_order, u.name;
        """
        return await self.fetch(query, published_only)

    async def get_universe(self, slug: str) -> asyncpg.Record | None:
        """Fetches a single universe by slug."""
        return await self.fetchrow("SELECT * FROM watch_universes WHERE slug = $1;", slug)

    async def list_paths(self, universe: str) -> list[asyncpg.Record]:
        """Fetches a universe's curated viewing paths, ordered by ``sort_order``."""
        return await self.fetch(
            "SELECT * FROM watch_paths WHERE universe = $1 ORDER BY sort_order;", universe)

    async def list_titles(
        self, universe: str, *, path_slug: str | None = None, sort: str = 'story',
    ) -> list[asyncpg.Record]:
        """Fetches every title of a universe with its importance for one path.

        ``path_slug`` selects which curated path's importance to join in (LEFT JOIN, so
        a title with no importance row for that path still appears, with ``importance``
        ``NULL``). ``sort`` is ``'story'`` or ``'release'``, mapping to ``story_order`` /
        ``release_order``; any other value raises :class:`ValueError` and is never
        interpolated into the query unchecked. Ordering is deterministic: the chosen
        column, then ``id``.
        """
        order_column = _TITLE_SORT_COLUMNS.get(sort)
        if order_column is None:
            raise ValueError(f"Invalid sort value: {sort!r}")

        query = f"""
            SELECT t.*, wti.importance
            FROM watch_titles t
            LEFT JOIN watch_paths p ON p.universe = t.universe AND p.slug = $2
            LEFT JOIN watch_title_importance wti ON wti.title_id = t.id AND wti.path_id = p.id
            WHERE t.universe = $1
            ORDER BY t.{order_column}, t.id;
        """
        return await self.fetch(query, universe, path_slug)

    async def get_title(self, universe: str, slug: str) -> asyncpg.Record | None:
        """Fetches a single title by universe and slug."""
        return await self.fetchrow(
            "SELECT * FROM watch_titles WHERE universe = $1 AND slug = $2;", universe, slug)

    async def search_titles(
        self, query: str, *, universe: str | None = None, limit: int = 25,
    ) -> list[asyncpg.Record]:
        """Trigram-similarity search over title names, best match first.

        Uses the ``watch_titles_trgm_idx`` GIN index; optionally scoped to one universe.
        """
        sql = """
            SELECT *, similarity(title, $1) AS score
            FROM watch_titles
            WHERE ($3::text IS NULL OR universe = $3) AND title % $1
            ORDER BY score DESC
            LIMIT $2;
        """
        return await self.fetch(sql, query, limit, universe)

    async def list_providers(self, title_ids: Sequence[int], region: str) -> list[asyncpg.Record]:
        """Fetches provider rows for many titles in one query (avoids per-title N+1)."""
        if not title_ids:
            return []
        return await self.fetch(
            "SELECT * FROM watch_providers WHERE title_id = ANY($1::bigint[]) AND region = $2;",
            list(title_ids), region,
        )

    # -- Ingest (write) -----------------------------------------------------

    async def upsert_universe(self, data: dict[str, Any]) -> None:
        """Inserts or updates a universe by slug. Safe to re-run.

        ``data`` must provide ``slug``, ``name``, ``short_name`` and may provide
        ``tagline``, ``accent``, ``logo_url``, ``backdrop_url``, ``sort_order``,
        ``published``. ``synced_at`` is always stamped to the current UTC time.
        """
        query = """
            INSERT INTO watch_universes (
                slug, name, short_name, tagline, accent, logo_url, backdrop_url,
                sort_order, published, synced_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, (now() AT TIME ZONE 'utc'))
            ON CONFLICT (slug) DO UPDATE SET
                name = EXCLUDED.name,
                short_name = EXCLUDED.short_name,
                tagline = EXCLUDED.tagline,
                accent = EXCLUDED.accent,
                logo_url = EXCLUDED.logo_url,
                backdrop_url = EXCLUDED.backdrop_url,
                sort_order = EXCLUDED.sort_order,
                published = EXCLUDED.published,
                synced_at = EXCLUDED.synced_at;
        """
        await self.execute(
            query,
            data['slug'], data['name'], data['short_name'], data.get('tagline'),
            data.get('accent', 15082537), data.get('logo_url'), data.get('backdrop_url'),
            data.get('sort_order', 0), data.get('published', False),
        )

    async def upsert_path(self, universe: str, data: dict[str, Any]) -> int:
        """Inserts or updates a viewing path within a universe. Returns the path's id.

        ``data`` must provide ``slug``, ``name`` and may provide ``description``,
        ``is_default``, ``sort_order``.
        """
        query = """
            INSERT INTO watch_paths (universe, slug, name, description, is_default, sort_order)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (universe, slug) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                is_default = EXCLUDED.is_default,
                sort_order = EXCLUDED.sort_order
            RETURNING id;
        """
        return await self.fetchval(
            query, universe, data['slug'], data['name'], data.get('description'),
            data.get('is_default', False), data.get('sort_order', 0),
        )

    async def upsert_title(self, universe: str, data: dict[str, Any]) -> int:
        """Inserts or updates a title within a universe. Returns the title's id.

        ``data`` must provide ``tmdb_type``, ``tmdb_id``, ``slug``, ``title`` and may
        provide ``kind``, ``runtime``, ``episodes``, ``release_date``, ``poster_path``,
        ``backdrop_path``, ``overview``, ``tmdb_rating``, ``tmdb_votes``, ``era``,
        ``era_order``, ``story_order``, ``release_order``, ``milestone``, ``instruction``,
        ``context``, ``spoiler``, ``sub_universe``. ``credits_of`` is resolved separately
        via :meth:`set_credits_of`, since the referenced title may not exist yet on first
        insert.
        """
        query = """
            INSERT INTO watch_titles (
                universe, tmdb_type, tmdb_id, slug, title, kind, runtime, episodes,
                release_date, poster_path, backdrop_path, overview, tmdb_rating, tmdb_votes,
                era, era_order, story_order, release_order, milestone, instruction, context,
                spoiler, sub_universe
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17,
                $18, $19, $20, $21, $22, $23
            )
            ON CONFLICT (universe, tmdb_type, tmdb_id) DO UPDATE SET
                slug = EXCLUDED.slug,
                title = EXCLUDED.title,
                kind = EXCLUDED.kind,
                runtime = EXCLUDED.runtime,
                episodes = EXCLUDED.episodes,
                release_date = EXCLUDED.release_date,
                poster_path = EXCLUDED.poster_path,
                backdrop_path = EXCLUDED.backdrop_path,
                overview = EXCLUDED.overview,
                tmdb_rating = EXCLUDED.tmdb_rating,
                tmdb_votes = EXCLUDED.tmdb_votes,
                era = EXCLUDED.era,
                era_order = EXCLUDED.era_order,
                story_order = EXCLUDED.story_order,
                release_order = EXCLUDED.release_order,
                milestone = EXCLUDED.milestone,
                instruction = EXCLUDED.instruction,
                context = EXCLUDED.context,
                spoiler = EXCLUDED.spoiler,
                sub_universe = EXCLUDED.sub_universe
            RETURNING id;
        """
        return await self.fetchval(
            query,
            universe, data['tmdb_type'], data['tmdb_id'], data['slug'], data['title'],
            data.get('kind', 'movie'), data.get('runtime', 0), data.get('episodes'),
            data.get('release_date'), data.get('poster_path'), data.get('backdrop_path'),
            data.get('overview'), data.get('tmdb_rating'), data.get('tmdb_votes'),
            data.get('era'), data.get('era_order', 0), data.get('story_order', 0),
            data.get('release_order', 0), data.get('milestone', False),
            data.get('instruction'), data.get('context'), data.get('spoiler'),
            data.get('sub_universe'),
        )

    async def set_importance(self, title_id: int, path_id: int, importance: str) -> None:
        """Upserts a title's importance tag for a viewing path (composite-PK upsert)."""
        if importance not in _IMPORTANCE_VALUES:
            raise ValueError(f"Invalid importance value: {importance!r}")
        query = """
            INSERT INTO watch_title_importance (title_id, path_id, importance)
            VALUES ($1, $2, $3)
            ON CONFLICT (title_id, path_id) DO UPDATE SET importance = EXCLUDED.importance;
        """
        await self.execute(query, title_id, path_id, importance)

    async def prune_importance(self, title_id: int, keep_path_ids: Sequence[int]) -> int:
        """Deletes a title's importance rows for paths not in ``keep_path_ids``. Returns the count deleted.

        Unlike :meth:`prune_titles`, an empty ``keep_path_ids`` is *not* a "refuse to wipe"
        signal here -- it correctly deletes **all** of the title's importance rows, since a
        seed with no ``importance`` entries for this title means it belongs to no path.
        """
        rows = await self.fetch(
            "DELETE FROM watch_title_importance WHERE title_id = $1 AND path_id != ALL($2::bigint[]) RETURNING path_id;",
            title_id, list(keep_path_ids),
        )
        return len(rows)

    async def replace_providers(self, title_id: int, region: str, rows: Sequence[dict[str, Any]]) -> None:
        """Replaces every provider row for a title+region with ``rows``, atomically.

        Deletes the existing rows and inserts the new set inside one transaction, so a
        failed re-sync never leaves a title with a partial or empty provider list.
        """
        async with self.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM watch_providers WHERE title_id = $1 AND region = $2;", title_id, region)
            for row in rows:
                await conn.execute(
                    """
                    INSERT INTO watch_providers (title_id, region, provider_id, provider_name, logo_path, offer)
                    VALUES ($1, $2, $3, $4, $5, $6);
                    """,
                    title_id, region, row['provider_id'], row['provider_name'],
                    row.get('logo_path'), row.get('offer', 'flatrate'),
                )

    async def prune_titles(self, universe: str, keep_ids: Sequence[int]) -> int:
        """Deletes titles of ``universe`` whose id is not in ``keep_ids``. Returns the count deleted.

        An empty ``keep_ids`` performs no delete and returns 0 — a seed that failed to
        parse must never wipe the catalogue. Deletion cascades to ``watch_progress``
        (``ON DELETE CASCADE``), so a pruned title also destroys every user's watch
        progress for it.
        """
        if not keep_ids:
            return 0
        rows = await self.fetch(
            "DELETE FROM watch_titles WHERE universe = $1 AND id != ALL($2::bigint[]) RETURNING id;",
            universe, list(keep_ids),
        )
        return len(rows)

    async def set_credits_of(self, title_id: int, credits_of_id: int | None) -> None:
        """Sets or clears a title's ``credits_of`` reference (resolved in a second ingest pass)."""
        await self.execute("UPDATE watch_titles SET credits_of = $2 WHERE id = $1;", title_id, credits_of_id)

    # -- Progress / prefs -----------------------------------------------------

    async def get_progress(self, user_id: int, *, universe: str | None = None) -> list[asyncpg.Record]:
        """Fetches a user's watch progress, optionally scoped to one universe."""
        if universe is None:
            return await self.fetch("SELECT * FROM watch_progress WHERE user_id = $1;", user_id)
        query = """
            SELECT wp.*
            FROM watch_progress wp
            JOIN watch_titles wt ON wt.id = wp.title_id
            WHERE wp.user_id = $1 AND wt.universe = $2;
        """
        return await self.fetch(query, user_id, universe)

    async def set_progress(
        self, user_id: int, title_id: int, status: str, updated_at: datetime.datetime,
    ) -> None:
        """Upserts a title's watch status for a user, last-write-wins on ``updated_at``.

        ``updated_at`` must be a naive UTC :class:`~datetime.datetime` -- ``watch_progress
        .updated_at`` is a naive ``TIMESTAMP`` column, and asyncpg's timestamp encoder raises
        ``TypeError`` on a timezone-aware value.
        """
        if status not in _PROGRESS_STATUSES:
            raise ValueError(f"Invalid watch progress status: {status!r}")
        query = """
            INSERT INTO watch_progress (user_id, title_id, status, updated_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, title_id) DO UPDATE
            SET status = EXCLUDED.status, updated_at = EXCLUDED.updated_at
            WHERE watch_progress.updated_at < EXCLUDED.updated_at;
        """
        await self.execute(query, user_id, title_id, status, updated_at)

    async def set_progress_bulk(
        self, user_id: int, entries: Sequence[tuple[int, str, datetime.datetime]],
    ) -> None:
        """Upserts many titles' watch status for a user in one batch, last-write-wins."""
        if not entries:
            return
        # Validate all statuses upfront before any query is issued.
        for _, status, _ in entries:
            if status not in _PROGRESS_STATUSES:
                raise ValueError(f"Invalid watch progress status: {status!r}")
        query = """
            INSERT INTO watch_progress (user_id, title_id, status, updated_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, title_id) DO UPDATE
            SET status = EXCLUDED.status, updated_at = EXCLUDED.updated_at
            WHERE watch_progress.updated_at < EXCLUDED.updated_at;
        """
        args = [(user_id, title_id, status, updated_at) for title_id, status, updated_at in entries]
        async with self.acquire() as conn:
            await conn.executemany(query, args)

    async def clear_progress(self, user_id: int, *, universe: str | None = None) -> int:
        """Deletes a user's watch progress, optionally scoped to one universe. Returns rows deleted."""
        if universe is None:
            rows = await self.fetch(
                "DELETE FROM watch_progress WHERE user_id = $1 RETURNING title_id;", user_id)
            return len(rows)
        query = """
            DELETE FROM watch_progress
            WHERE user_id = $1 AND title_id IN (SELECT id FROM watch_titles WHERE universe = $2)
            RETURNING title_id;
        """
        rows = await self.fetch(query, user_id, universe)
        return len(rows)

    async def delete_title_progress(self, user_id: int, title_id: int) -> None:
        """Removes a single title's progress row for a user."""
        await self.delete_where('watch_progress', ('user_id', 'title_id'), (user_id, title_id))

    async def get_prefs(self, user_id: int) -> asyncpg.Record | None:
        """Fetches a user's watchlist preferences."""
        return await self.fetchrow("SELECT * FROM watch_prefs WHERE user_id = $1;", user_id)

    async def upsert_prefs(self, user_id: int, values: dict[str, Any]) -> asyncpg.Record | None:
        """Inserts or updates a user's watchlist preferences. Returns the resulting row.

        ``values`` keys must be a subset of the whitelisted preference columns (``region``,
        ``path_slug``, ``sort_mode``, ``show_spoiler``, ``with_credits``, ``hide_sub``,
        ``weekly_hours``, ``target_date``); anything else raises :class:`ValueError`. The
        SET/VALUES clauses are built the same parameterized way as :meth:`update_returning`
        — a value is never f-strung directly into the query.
        """
        unknown = set(values) - set(_PREFS_COLUMNS)
        if unknown:
            raise ValueError(f"Unknown watch_prefs column(s): {', '.join(sorted(unknown))}")
        if not values:
            return await self.get_prefs(user_id)

        columns = list(values.keys())
        insert_columns = ", ".join(f'"{col}"' for col in columns)
        insert_placeholders = ", ".join(f"${i}" for i in range(2, len(columns) + 2))
        update_clause = ", ".join(f'"{col}" = EXCLUDED."{col}"' for col in columns)
        query = f"""
            INSERT INTO watch_prefs (user_id, {insert_columns}, updated_at)
            VALUES ($1, {insert_placeholders}, (now() AT TIME ZONE 'utc'))
            ON CONFLICT (user_id) DO UPDATE
            SET {update_clause}, updated_at = (now() AT TIME ZONE 'utc')
            RETURNING *;
        """
        return await self.fetchrow(query, user_id, *values.values())
