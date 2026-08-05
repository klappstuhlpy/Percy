"""Data access for the comic catalogue: ``comic_releases``, ``comic_creators`` and
``comic_characters`` (see ``migrations/V40__comic_catalogue.sql``).

Separate from :class:`~app.database.repositories.content.ComicsRepository`, which owns
``comic_config`` -- the per-guild feed *schedule*. This one owns the *content*: what was
published, by whom, about whom. None of these tables are behind a ``@cache.cache()`` getter,
so methods here never fire cache-invalidation signals.

Named ``releases`` rather than ``comics`` because film/TV releases join these tables' shape in
a later phase; the subscription and delivery tables (``V42__subscriptions.sql``) live here too,
and those *are* cross-media -- a subscription's subject is a string key, not a foreign key.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

import asyncpg

from app.database.repositories.base import BaseRepository
from app.services.releases import check_status, normalise

if TYPE_CHECKING:
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

    async def list_series(self, *, brand: str | None = None, limit: int = 200) -> list[asyncpg.Record]:
        """Fetches series with issue counts and latest release date, optionally scoped to a brand.

        Grouped by ``(series_slug, brand)``, not by slug alone: a series slug is derived from a
        title, so two publishers *can* land on the same one. Grouping by slug alone would merge
        their issue counts under whichever brand happened to sort first.
        """
        query = """
            SELECT series_slug, brand, MIN(series_name) AS series_name,
                   COUNT(*) AS issue_count, MAX(release_date) AS latest
            FROM comic_releases
            WHERE series_slug IS NOT NULL AND ($1::text IS NULL OR brand = $1)
            GROUP BY series_slug, brand
            ORDER BY latest DESC NULLS LAST
            LIMIT $2;
        """
        return await self.fetch(query, brand, limit)

    async def list_series_releases(self, series_slug: str, *, brand: str | None = None) -> list[asyncpg.Record]:
        """Fetches every release in one series, unordered.

        ``brand`` scopes the lookup, and callers that know it should pass it: a series slug
        comes from a title, so two publishers can collide on one, and a release's neighbours
        must never be another publisher's book. It stays optional because a bare
        ``/comics/series/{slug}`` URL has no brand to scope by.

        The caller (the payload service) sorts these into reading order -- issue number
        ascending on the leading digits, release date as the tiebreak -- since that comparison
        belongs with the rest of the JSON shaping, not the SQL.
        """
        return await self.fetch(
            "SELECT * FROM comic_releases WHERE series_slug = $1 AND ($2::text IS NULL OR brand = $2);",
            series_slug, brand)

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

    # -- Dispatch (read) ---------------------------------------------------

    async def recent_comic_releases(self, since: datetime.datetime, *, limit: int = 500) -> list[asyncpg.Record]:
        """Fetches releases first seen since ``since``, newest first.

        ``first_seen`` rather than ``release_date``: the dispatcher asks "what did we learn about
        recently", which is not the same question as "what comes out recently" -- a book solicited
        for next month is new to us today.
        """
        return await self.fetch(
            "SELECT * FROM comic_releases WHERE first_seen >= $1 ORDER BY first_seen DESC LIMIT $2;",
            since, limit)

    async def credits_for_releases(self, release_ids: Sequence[int]) -> list[asyncpg.Record]:
        """Fetches credits for many releases in one query, each row carrying its ``release_id``.

        The batched form of :meth:`get_credits` -- a dispatch run reads a week of releases at
        once, and one query per release is the N+1 that would make it expensive.
        """
        if not release_ids:
            return []
        return await self.fetch(
            """
            SELECT release_id, name, role FROM comic_creators
            WHERE release_id = ANY($1::int[])
            ORDER BY release_id, role, name;
            """,
            list(release_ids))

    async def characters_for_releases(self, release_ids: Sequence[int]) -> list[asyncpg.Record]:
        """Fetches characters for many releases in one query, each row carrying its ``release_id``."""
        if not release_ids:
            return []
        return await self.fetch(
            """
            SELECT release_id, name, kind FROM comic_characters
            WHERE release_id = ANY($1::int[])
            ORDER BY release_id, name;
            """,
            list(release_ids))

    # -- Subscriptions -----------------------------------------------------

    async def subscribe(
        self,
        user_id: int,
        media: str,
        subject_type: str,
        subject_value: str,
        *,
        label: str | None = None,
        streams: str = 'releases',
        delivery: str = 'dm',
        guild_id: int | None = None,
    ) -> asyncpg.Record:
        """Creates or updates one subscription, returning the stored row.

        Re-subscribing to something already followed is an *edit*, not an error: the conflict
        updates ``streams``, ``delivery`` and the display label but leaves ``created_at``
        alone, because that timestamp is the backlog cutoff (see
        :func:`app.services.releases.match`) and bumping it would silently re-arm a week of
        releases the user has already been told about.
        """
        query = """
            INSERT INTO release_subscriptions
                (user_id, media, subject_type, subject_value, subject_label, streams, delivery, guild_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (user_id, media, subject_type, subject_value) DO UPDATE
                SET subject_label = COALESCE(EXCLUDED.subject_label, release_subscriptions.subject_label),
                    streams = EXCLUDED.streams,
                    delivery = EXCLUDED.delivery,
                    guild_id = COALESCE(EXCLUDED.guild_id, release_subscriptions.guild_id)
            RETURNING *;
        """
        return await self.fetchrow(
            query, user_id, media, subject_type, normalise(subject_value),
            label or subject_value.strip() or None, streams, delivery, guild_id)

    async def unsubscribe(self, user_id: int, media: str, subject_type: str, subject_value: str) -> bool:
        """Removes one subscription, returning whether it existed.

        ``RETURNING`` rather than a status string so the caller can tell "unfollowed" from
        "you were not following that" without a second query.
        """
        deleted = await self.fetchval(
            """
            DELETE FROM release_subscriptions
            WHERE user_id = $1 AND media = $2 AND subject_type = $3 AND subject_value = $4
            RETURNING 1;
            """,
            user_id, media, subject_type, normalise(subject_value))
        return deleted is not None

    async def list_subscriptions(self, user_id: int, *, media: str | None = None) -> list[asyncpg.Record]:
        """Fetches one user's subscriptions, grouped by medium then type for display."""
        return await self.fetch(
            """
            SELECT * FROM release_subscriptions
            WHERE user_id = $1 AND ($2::text IS NULL OR media = $2)
            ORDER BY media, subject_type, subject_label NULLS LAST, subject_value;
            """,
            user_id, media)

    async def count_subscribers(self, media: str, subject_type: str, subject_value: str) -> int:
        """Counts the users following one subject -- the "N people follow this" figure.

        One index-only lookup on ``release_subscriptions_subject_idx``.
        """
        return await self.fetchval(
            """
            SELECT COUNT(*) FROM release_subscriptions
            WHERE media = $1 AND subject_type = $2 AND subject_value = $3;
            """,
            media, subject_type, normalise(subject_value)) or 0

    async def subscribers_for_subjects(self, subjects: Sequence[tuple[str, str, str]]) -> list[asyncpg.Record]:
        """The fan-out: every subscription matching any of ``(media, subject_type, value)``.

        **One** query for a whole refresh, whatever the batch size -- the triples are unnested
        into a row set and matched against the composite index, rather than looped over in
        Python or ORed into a statement that grows with the week's releases. Filtering by
        stream, delivery and the backlog cutoff is the pure matcher's job
        (:func:`app.services.releases.match`); this returns the rows it needs to decide.

        Empty input returns ``[]`` without touching the pool.
        """
        if not subjects:
            return []
        media = [item[0] for item in subjects]
        types = [item[1] for item in subjects]
        values = [normalise(item[2]) for item in subjects]
        return await self.fetch(
            """
            SELECT * FROM release_subscriptions
            WHERE (media, subject_type, subject_value)
                  IN (SELECT * FROM unnest($1::text[], $2::text[], $3::text[]));
            """,
            media, types, values)

    async def set_delivery(self, user_id: int, media: str, subject_type: str, subject_value: str,
                           delivery: str) -> asyncpg.Record | None:
        """Switches one subscription between ``'dm'`` and ``'off'``."""
        return await self._set_subscription_field(user_id, media, subject_type, subject_value, 'delivery', delivery)

    async def set_streams(self, user_id: int, media: str, subject_type: str, subject_value: str,
                          streams: str) -> asyncpg.Record | None:
        """Switches one subscription between ``'releases'``, ``'news'`` and ``'both'``."""
        return await self._set_subscription_field(user_id, media, subject_type, subject_value, 'streams', streams)

    async def _set_subscription_field(self, user_id: int, media: str, subject_type: str, subject_value: str,
                                      column: str, value: str) -> asyncpg.Record | None:
        """Shared body for the two single-column toggles.

        ``column`` is interpolated into the statement, so it is checked against the pair of
        columns this is allowed to touch rather than trusted.
        """
        if column not in ('delivery', 'streams'):
            raise ValueError(f'unknown subscription column {column!r}')
        return await self.fetchrow(
            f"""
            UPDATE release_subscriptions SET {column} = $5
            WHERE user_id = $1 AND media = $2 AND subject_type = $3 AND subject_value = $4
            RETURNING *;
            """,
            user_id, media, subject_type, normalise(subject_value), value)

    async def disable_all_delivery(self, user_id: int) -> int:
        """Mutes every subscription of one user, returning how many were muted.

        What a blocked DM triggers (the plan's D8): Discord will not tell us the user is
        reachable again, so retrying forever is how a bot gets reported. Muting keeps the
        subscriptions intact -- the user can turn delivery back on and lose nothing.
        """
        rows = await self.fetch(
            "UPDATE release_subscriptions SET delivery = 'off' WHERE user_id = $1 AND delivery <> 'off' RETURNING 1;",
            user_id)
        return len(rows)

    # -- Deliveries --------------------------------------------------------

    async def claim_deliveries(self, rows: Sequence[tuple[int, str, str]]) -> set[tuple[int, str, str]]:
        """Claims ``(user_id, media, release_key)`` deliveries, returning only the new ones.

        Insert-first, deliberately: ``SELECT`` the already-delivered rows and then insert them
        and two concurrent dispatches both see "not delivered" and both DM the user. Letting
        the unique index arbitrate and reading back what ``RETURNING`` actually inserted makes
        the claim atomic -- whoever wins the race sends, the loser gets an empty set and sends
        nothing.

        Duplicates within one batch are collapsed before the statement, so a user matching the
        same release twice claims it once.
        """
        unique = list(dict.fromkeys(rows))
        if not unique:
            return set()
        claimed = await self.fetch(
            """
            INSERT INTO release_deliveries (user_id, media, release_key)
            SELECT * FROM unnest($1::bigint[], $2::text[], $3::text[])
            ON CONFLICT DO NOTHING
            RETURNING user_id, media, release_key;
            """,
            [item[0] for item in unique], [item[1] for item in unique], [item[2] for item in unique])
        return {(row['user_id'], row['media'], row['release_key']) for row in claimed}

    async def release_claims(self, rows: Sequence[tuple[int, str, str]]) -> None:
        """Gives claimed deliveries back after a send failed.

        Without this a DM that never arrived would still count as delivered and the user would
        silently lose that release forever -- the claim is optimistic, so it has to be
        reversible.
        """
        if not rows:
            return
        await self.execute(
            """
            DELETE FROM release_deliveries
            WHERE (user_id, media, release_key)
                  IN (SELECT * FROM unnest($1::bigint[], $2::text[], $3::text[]));
            """,
            [item[0] for item in rows], [item[1] for item in rows], [item[2] for item in rows])

    async def prune_deliveries(self, before: datetime.datetime) -> int:
        """Deletes delivery claims older than ``before``, returning the count.

        The table only has to remember far enough back that a re-dispatch cannot double-notify;
        beyond that it is dead weight on every insert. Counted through a CTE rather than by
        parsing asyncpg's status string.
        """
        return await self.fetchval(
            """
            WITH pruned AS (DELETE FROM release_deliveries WHERE delivered_at < $1 RETURNING 1)
            SELECT COUNT(*) FROM pruned;
            """,
            before) or 0

    # -- Progress ----------------------------------------------------------

    async def get_progress(self, user_id: int, *, media: str | None = None) -> list[asyncpg.Record]:
        """Fetches a user's read/watched/skipped rows, optionally scoped to one medium."""
        return await self.fetch(
            """
            SELECT * FROM release_progress
            WHERE user_id = $1 AND ($2::text IS NULL OR media = $2)
            ORDER BY media, release_key;
            """,
            user_id, media)

    async def set_progress(
        self,
        user_id: int,
        media: str,
        release_key: str,
        status: str,
        updated_at: datetime.datetime | None = None,
    ) -> asyncpg.Record | None:
        """Marks one item, returning the stored row -- or ``None`` when a newer write already won.

        Same last-write-wins rule as :meth:`set_progress_bulk`, deliberately: one table with two
        conflict rules would mean a single toggle could clobber what a batch merge just refused
        to. ``updated_at`` defaults to now, which is newer than anything already stored, so the
        ``None`` case only arises for a client that sent a future timestamp.

        ``updated_at`` must be naive UTC -- ``release_progress.updated_at`` is a naive
        ``TIMESTAMP`` and asyncpg's encoder raises ``TypeError`` on an aware value.
        """
        check_status(media, status)
        if updated_at is None:
            updated_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        return await self.fetchrow(
            """
            INSERT INTO release_progress (user_id, media, release_key, status, updated_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id, media, release_key) DO UPDATE
                SET status = EXCLUDED.status, updated_at = EXCLUDED.updated_at
                WHERE release_progress.updated_at < EXCLUDED.updated_at
            RETURNING *;
            """,
            user_id, media, release_key, status, updated_at)

    async def set_progress_bulk(
        self, user_id: int, rows: Sequence[tuple[str, str, str, datetime.datetime]],
    ) -> int:
        """Merges many ``(media, release_key, status, updated_at)`` rows, returning how many stuck.

        **One** statement whatever the batch size: the tuples are unnested into a row set, the
        same shape :meth:`claim_deliveries` uses, rather than an ``executemany`` round trip per
        entry -- a client syncing its whole localStorage sends the lot at once.

        Last-write-wins on the *client's* ``updated_at``, mirroring
        :meth:`~app.database.repositories.watchlist.WatchlistRepository.set_progress_bulk`: a
        stale tab replaying an old toggle must not overwrite a newer change from another device.
        The return is the number of rows actually written, so the caller can tell a full merge
        from one where the server was already ahead.

        Duplicates *within* one batch are collapsed first, newest kept. Postgres refuses to let
        ``ON CONFLICT DO UPDATE`` touch a row twice in one statement, so two toggles of the same
        item in one payload would abort the merge outright rather than settle on the later one.
        """
        newest: dict[tuple[str, str], tuple[str, str, str, datetime.datetime]] = {}
        for media, release_key, status, updated_at in rows:
            check_status(media, status)
            current = newest.get((media, release_key))
            if current is None or current[3] <= updated_at:
                newest[media, release_key] = (media, release_key, status, updated_at)
        if not newest:
            return 0

        entries = list(newest.values())
        written = await self.fetch(
            """
            INSERT INTO release_progress (user_id, media, release_key, status, updated_at)
            SELECT $1, * FROM unnest($2::text[], $3::text[], $4::text[], $5::timestamp[])
            ON CONFLICT (user_id, media, release_key) DO UPDATE
                SET status = EXCLUDED.status, updated_at = EXCLUDED.updated_at
                WHERE release_progress.updated_at < EXCLUDED.updated_at
            RETURNING 1;
            """,
            user_id,
            [entry[0] for entry in entries],
            [entry[1] for entry in entries],
            [entry[2] for entry in entries],
            [entry[3] for entry in entries])
        return len(written)

    async def clear_progress(self, user_id: int, media: str, release_key: str) -> bool:
        """Unmarks one item, returning whether it was marked."""
        deleted = await self.fetchval(
            """
            DELETE FROM release_progress
            WHERE user_id = $1 AND media = $2 AND release_key = $3
            RETURNING 1;
            """,
            user_id, media, release_key)
        return deleted is not None

    async def clear_all_progress(self, user_id: int, *, media: str | None = None) -> int:
        """Deletes a user's progress, optionally for one medium only. Returns rows deleted."""
        rows = await self.fetch(
            """
            DELETE FROM release_progress
            WHERE user_id = $1 AND ($2::text IS NULL OR media = $2)
            RETURNING 1;
            """,
            user_id, media)
        return len(rows)
