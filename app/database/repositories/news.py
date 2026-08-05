"""Data access for the news stream: ``news_sources``, ``news_items`` and ``news_subjects``
(see ``migrations/V43__news.sql``).

Separate from :class:`~app.database.repositories.releases.ReleasesRepository`, which owns the
comic catalogue and the subscription tables, but deliberately the *same shape* where it counts:
:meth:`NewsRepository.news_for_subjects` mirrors ``subscribers_for_subjects`` -- one unnest
against one composite index, whatever the batch size -- because news and releases fan out
through the same subject keys and must not grow two different fan-out costs.

None of these tables sit behind a ``@cache.cache()`` getter, so nothing here fires cache
signals. The internal API's own 300 s TTL is what bounds staleness on the read side.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import asyncpg

from app.database.repositories.base import BaseRepository
from app.services.releases import normalise

if TYPE_CHECKING:
    import datetime
    from collections.abc import Sequence

__all__ = ('NewsRepository',)

#: Columns :meth:`NewsRepository.upsert_item` writes, in statement order.
_ITEM_COLUMNS = (
    'source_slug', 'external_id', 'url', 'headline', 'excerpt', 'image_url', 'author',
    'published_at',
)

#: Everything a re-fetch of a known story is allowed to refresh. ``url`` is excluded because it
#: is the conflict target, ``source_slug`` because a story does not change publisher.
_ITEM_UPDATE_COLUMNS = ('external_id', 'headline', 'excerpt', 'image_url', 'author', 'published_at')

#: Hard ceiling on one page, so a bad ``limit`` cannot ask for the whole archive.
MAX_PAGE_SIZE = 100


class NewsRepository(BaseRepository):
    """Data access for polled news: the sources, the stories, and their subject tags."""

    # -- Sources -----------------------------------------------------------

    async def list_sources(self, *, media: str | None = None, enabled_only: bool = True) -> list[asyncpg.Record]:
        """Fetches configured sources, oldest-polled first.

        Ordering by ``last_fetched`` with nulls first is what makes a polling loop fair: a
        source added today or one that has been failing does not sit behind the feeds that
        were already refreshed. ``media`` matches ``'both'`` too -- a general entertainment
        feed is a comic source *and* a film source.
        """
        return await self.fetch(
            """
            SELECT * FROM news_sources
            WHERE (NOT $1::bool OR enabled)
              AND ($2::text IS NULL OR media = $2 OR media = 'both')
            ORDER BY last_fetched ASC NULLS FIRST, slug;
            """,
            enabled_only, media)

    async def get_source(self, slug: str) -> asyncpg.Record | None:
        """Fetches one source by slug."""
        return await self.fetchrow("SELECT * FROM news_sources WHERE slug = $1;", slug)

    async def upsert_source(
        self,
        slug: str,
        name: str,
        feed_url: str,
        *,
        homepage: str | None = None,
        media: str = 'both',
        enabled: bool = True,
    ) -> asyncpg.Record:
        """Registers or updates a source, returning the stored row.

        ``last_fetched``/``last_status`` are deliberately untouched: re-registering a source
        (a seed re-run, a renamed feed) is configuration, and losing the record of when it last
        answered would hide a feed that has been dead for a week.
        """
        return await self.fetchrow(
            """
            INSERT INTO news_sources (slug, name, homepage, feed_url, media, enabled)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (slug) DO UPDATE
                SET name = EXCLUDED.name, homepage = EXCLUDED.homepage,
                    feed_url = EXCLUDED.feed_url, media = EXCLUDED.media, enabled = EXCLUDED.enabled
            RETURNING *;
            """,
            slug, name, homepage, feed_url, media, enabled)

    async def set_source_enabled(self, slug: str, enabled: bool) -> asyncpg.Record | None:
        """Switches a source on or off, returning the row (``None`` if there is no such slug).

        The one-row kill switch §9 asks for: a publisher who objects, or a feed that starts
        producing garbage, is disabled without a deploy and without losing its history.
        """
        return await self.fetchrow(
            "UPDATE news_sources SET enabled = $2 WHERE slug = $1 RETURNING *;", slug, enabled)

    async def mark_fetched(self, slug: str, status: str) -> None:
        """Records the outcome of one poll.

        Called on **every** attempt, success or failure -- that is the whole point of the
        column pair. ``status`` is the string from
        :class:`~app.clients.news.FeedResponse` (``'200'``, ``'404'``, ``'timeout'``,
        ``'breaker-open'``, ...), so a dead feed is a value someone can see rather than an
        absence of log lines.
        """
        await self.execute(
            """
            UPDATE news_sources
            SET last_fetched = (now() AT TIME ZONE 'utc'), last_status = $2
            WHERE slug = $1;
            """,
            slug, status)

    # -- Items (write) -----------------------------------------------------

    async def upsert_item(self, row: dict[str, Any]) -> tuple[int, bool]:
        """Inserts or refreshes one story, returning ``(id, created)``.

        Identity is ``url`` -- §5.1's first dedupe axis, and the strong one: the same story
        syndicated under two guids is one story. ``fetched_at`` stays at its insert value so
        "new since" questions survive a re-poll.

        The second axis, ``(source_slug, external_id)``, is a separate unique constraint, so a
        feed that rewrote a story's link collides on *that* index instead. Rather than insert a
        duplicate under the new URL, the conflict is resolved by updating the row that guid
        already owns -- which is the one case where a story's ``url`` legitimately changes.
        """
        try:
            return await self._upsert_item(row)
        except asyncpg.UniqueViolationError:
            return await self._update_by_external_id(row)

    async def _upsert_item(self, row: dict[str, Any]) -> tuple[int, bool]:
        placeholders = ', '.join(f'${i}' for i in range(1, len(_ITEM_COLUMNS) + 1))
        assignments = ', '.join(f'{column} = EXCLUDED.{column}' for column in _ITEM_UPDATE_COLUMNS)
        query = f"""
            INSERT INTO news_items ({', '.join(_ITEM_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT (url) DO UPDATE SET {assignments}
            RETURNING id, (xmax = 0) AS created;
        """
        record = await self.fetchrow(query, *(row.get(column) for column in _ITEM_COLUMNS))
        return record['id'], record['created']

    async def _update_by_external_id(self, row: dict[str, Any]) -> tuple[int, bool]:
        """Refreshes the row a ``(source_slug, external_id)`` pair already owns."""
        assignments = ', '.join(f'{column} = ${i}' for i, column in enumerate(('url', *_ITEM_UPDATE_COLUMNS), start=3))
        record = await self.fetchrow(
            f"""
            UPDATE news_items SET {assignments}
            WHERE source_slug = $1 AND external_id = $2
            RETURNING id;
            """,
            row.get('source_slug'), row.get('external_id'),
            *(row.get(column) for column in ('url', *_ITEM_UPDATE_COLUMNS)))
        if record is None:
            raise ValueError(f'news item {row.get("url")!r} conflicts but has no owning row')
        return record['id'], False

    async def replace_subjects(self, news_id: int, tags: Sequence[tuple[str, str, str, str, str | None]]) -> None:
        """Replaces a story's subject tags with ``(media, type, value, rule, matched_term)``.

        Delete-then-insert, like ``replace_creators``: a re-tag that *drops* a subject (a rule
        was revoked, a term was removed from the catalogue) has to actually drop it, which an
        upsert alone would not do. Values are normalised here as well as in the tagger -- the
        same belt-and-braces the subscription writes use, since a tag that does not compare
        equal to the subscription it should match reaches nobody.
        """
        async with self.acquire() as connection, connection.transaction():
            await connection.execute("DELETE FROM news_subjects WHERE news_id = $1;", news_id)
            if tags:
                await connection.executemany(
                    """
                    INSERT INTO news_subjects (news_id, media, subject_type, subject_value, rule, matched_term)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT DO NOTHING;
                    """,
                    [(news_id, media, subject_type, normalise(value), rule, matched)
                     for media, subject_type, value, rule, matched in tags],
                )

    # -- Items (read) ------------------------------------------------------

    async def list_news(
        self,
        *,
        media: str | None = None,
        subject: tuple[str, str] | None = None,
        source: str | None = None,
        since: datetime.datetime | None = None,
        search: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> list[asyncpg.Record]:
        """Fetches stories newest-first, filtered by any combination of the arguments.

        ``subject`` is a ``(subject_type, subject_value)`` pair; combine it with ``media`` to
        scope a tag to one catalogue. ``search`` is a trigram-friendly substring match on the
        headline. Undated stories sort last rather than being hidden.
        """
        where, args = self._filters(media=media, subject=subject, source=source, since=since, search=search)
        args.extend((min(max(limit, 1), MAX_PAGE_SIZE), max(offset, 0)))
        return await self.fetch(
            f"""
            SELECT * FROM news_items
            {where}
            ORDER BY published_at DESC NULLS LAST, id DESC
            LIMIT ${len(args) - 1} OFFSET ${len(args)};
            """,
            *args)

    async def count_news(
        self,
        *,
        media: str | None = None,
        subject: tuple[str, str] | None = None,
        source: str | None = None,
        since: datetime.datetime | None = None,
        search: str | None = None,
    ) -> int:
        """Counts the stories :meth:`list_news` would page through."""
        where, args = self._filters(media=media, subject=subject, source=source, since=since, search=search)
        return await self.fetchval(f"SELECT COUNT(*) FROM news_items {where};", *args) or 0

    @staticmethod
    def _filters(
        *,
        media: str | None,
        subject: tuple[str, str] | None,
        source: str | None,
        since: datetime.datetime | None,
        search: str | None,
    ) -> tuple[str, list[Any]]:
        """Builds the shared ``WHERE`` clause for the list/count pair.

        Both must filter identically or a page count disagrees with its page, so the clause is
        built once here rather than written twice.
        """
        clauses: list[str] = []
        args: list[Any] = []

        def placeholder(value: Any) -> str:
            args.append(value)
            return f'${len(args)}'

        if source is not None:
            clauses.append(f'source_slug = {placeholder(source)}')
        if since is not None:
            clauses.append(f'published_at >= {placeholder(since)}')
        if search:
            clauses.append(f'headline ILIKE {placeholder(f"%{search}%")}')

        # The tag filters are one EXISTS over ``news_subjects`` rather than a join, so a story
        # tagged with the subject twice cannot come back twice and break the page count.
        tag_clauses: list[str] = []
        if subject is not None:
            tag_clauses.append(f's.subject_type = {placeholder(subject[0])}')
            tag_clauses.append(f's.subject_value = {placeholder(normalise(subject[1]))}')
        if media is not None:
            tag_clauses.append(f's.media = {placeholder(media)}')
        if tag_clauses:
            clauses.append(
                'EXISTS (SELECT 1 FROM news_subjects s WHERE s.news_id = news_items.id '
                f'AND {" AND ".join(tag_clauses)})')

        return (f'WHERE {" AND ".join(clauses)}' if clauses else ''), args

    async def get_item(self, news_id: int) -> asyncpg.Record | None:
        """Fetches one story by id."""
        return await self.fetchrow("SELECT * FROM news_items WHERE id = $1;", news_id)

    async def subjects_for_items(self, news_ids: Sequence[int]) -> list[asyncpg.Record]:
        """Fetches the tags of many stories in one query, for a batch to group by ``news_id``.

        The batched twin of :meth:`get_subjects`, and it exists for the same reason
        ``credits_for_releases`` does: both the browse payload and the dispatch run hold a page
        of stories and need every story's subjects, and asking per story is an N+1 that grows
        with the page size. Empty input never touches the pool.
        """
        if not news_ids:
            return []
        return await self.fetch(
            """
            SELECT news_id, media, subject_type, subject_value, rule, matched_term
            FROM news_subjects WHERE news_id = ANY($1::int[])
            ORDER BY news_id, media, subject_type, subject_value;
            """,
            list(news_ids))

    async def get_subjects(self, news_id: int) -> list[asyncpg.Record]:
        """Fetches one story's tags, with their provenance."""
        return await self.fetch(
            """
            SELECT media, subject_type, subject_value, rule, matched_term
            FROM news_subjects WHERE news_id = $1
            ORDER BY media, subject_type, subject_value;
            """,
            news_id)

    # -- Dispatch (read) ---------------------------------------------------

    async def news_for_subjects(
        self,
        subjects: Sequence[tuple[str, str, str]],
        *,
        since: datetime.datetime | None = None,
        limit: int = 200,
    ) -> list[asyncpg.Record]:
        """The fan-out read: stories tagged with any of ``(media, subject_type, value)``.

        **One** query for a whole dispatch run, whatever the batch size -- the triples are
        unnested into a row set and matched against ``news_subjects_subject_idx``, exactly as
        ``ReleasesRepository.subscribers_for_subjects`` does on the release side. Each row
        carries the ``media``/``subject_type``/``subject_value`` that matched, so the caller can
        route a story to the right subscription without a second lookup; a story tagged with
        three of a user's subjects therefore appears three times and is deduped by the pure
        matcher, which is where that policy already lives.

        Empty input returns ``[]`` without touching the pool.
        """
        if not subjects:
            return []
        return await self.fetch(
            """
            SELECT i.*, s.media, s.subject_type, s.subject_value
            FROM news_subjects s
            JOIN news_items i ON i.id = s.news_id
            WHERE (s.media, s.subject_type, s.subject_value)
                  IN (SELECT * FROM unnest($1::text[], $2::text[], $3::text[]))
              AND ($4::timestamp IS NULL OR i.published_at IS NULL OR i.published_at >= $4)
            ORDER BY i.published_at DESC NULLS LAST, i.id DESC
            LIMIT $5;
            """,
            [item[0] for item in subjects],
            [item[1] for item in subjects],
            [normalise(item[2]) for item in subjects],
            since, limit)

    async def recent_news(self, since: datetime.datetime, *, limit: int = 500) -> list[asyncpg.Record]:
        """Fetches stories *fetched* since ``since``, newest first.

        ``fetched_at`` rather than ``published_at``: a dispatcher asks "what did we learn about
        recently", and a feed that back-fills a week-old story is new to us today. The same
        distinction ``recent_comic_releases`` draws with ``first_seen``.
        """
        return await self.fetch(
            "SELECT * FROM news_items WHERE fetched_at >= $1 ORDER BY fetched_at DESC LIMIT $2;",
            since, limit)
