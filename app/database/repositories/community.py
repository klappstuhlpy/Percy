from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, cast

from asyncpg import Record

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import datetime
    from collections.abc import Iterable

    import asyncpg

__all__ = (
    'GiveawaysRepository',
    'HighlightsRepository',
    'PollsRepository',
    'StarboardRepository',
    'TagsRepository',
)


# -- Polls ----------------------------------------------------------------


class PollsRepository(BaseRepository):
    """Data access for the ``polls`` table.

    ``poll_entry`` is a Postgres composite type stored in the ``entries`` array
    column of ``polls`` rather than a standalone table, so it is handled here too.
    The methods return raw records/scalars; building :class:`Poll` objects is left
    to the ``Polls`` cog, which owns the ``cog`` reference each record needs.
    """

    _SORT_CLAUSES: ClassVar[dict[str, str]] = {
        'id': 'id',
        'new': "metadata #>> ARRAY['kwargs', 'published'] DESC",
        'old': "metadata #>> ARRAY['kwargs', 'published'] ASC",
        'most votes': "metadata #>> ARRAY['kwargs', 'votes'] DESC",
        'least votes': "metadata #>> ARRAY['kwargs', 'votes'] ASC",
    }

    async def create(
            self,
            poll_id: int,
            channel_id: int,
            message_id: int,
            guild_id: int,
            published: datetime.datetime,
            expires: datetime.datetime,
            metadata: dict[str, Any],
    ) -> int:
        """Inserts a new poll and returns its generated ``id``."""
        query = """
            INSERT INTO polls (id, channel_id, message_id, guild_id, published, expires, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            RETURNING id;
        """
        return await self.fetchval(
            query, poll_id, channel_id, message_id, guild_id, published, expires, metadata)

    async def get(self, poll_id: int, guild_id: int) -> asyncpg.Record | None:
        """Fetches a single poll scoped to a guild."""
        query = "SELECT * FROM polls WHERE id = $1 AND guild_id = $2 LIMIT 1;"
        return await self.fetchrow(query, poll_id, guild_id)

    async def get_by_id(self, poll_id: int) -> asyncpg.Record | None:
        """Fetches a single poll by its ID, regardless of guild."""
        query = "SELECT * FROM polls WHERE id = $1 LIMIT 1;"
        return await self.fetchrow(query, poll_id)

    async def get_for_guild(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches every poll belonging to a guild."""
        query = "SELECT * FROM polls WHERE guild_id = $1;"
        return await self.fetch(query, guild_id)

    async def get_all_ids(self) -> list[asyncpg.Record]:
        """Fetches the IDs of every poll (used to generate a unique new ID)."""
        return await self.fetch("SELECT id FROM polls;")

    async def search_for_guild(
            self, guild_id: int, *, sort: str | None = None, active: bool = False
    ) -> list[asyncpg.Record]:
        """Fetches a guild's polls, optionally filtered to running polls and sorted.

        ``sort`` is matched against a whitelist of allowed ``ORDER BY`` fragments,
        falling back to sorting by ``id`` for unknown values.
        """
        sort_clause = self._SORT_CLAUSES.get(sort or 'id', 'id')
        running = "AND metadata #>> ARRAY['kwargs', 'running'] = true" if active else ''
        query = f"SELECT * FROM polls WHERE guild_id = $1 {running} ORDER BY {sort_clause};"
        return await self.fetch(query, guild_id)

    async def update(
            self,
            poll_id: int,
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> Record | None:
        """Updates a poll row and returns the full updated record."""
        return await self.update_returning("polls", ("id",), (poll_id,), values, connection=connection)

    async def delete(self, poll_id: int) -> None:
        """Deletes a poll row."""
        await self.delete_where("polls", ("id",), (poll_id,))


# -- Giveaways ------------------------------------------------------------


class GiveawaysRepository(BaseRepository):
    """Data access for the ``giveaways`` table.

    Each row stores a giveaway's location (guild/channel/message), its author,
    the set of entrant IDs, and a JSONB ``metadata`` blob holding the prize,
    schedule and winner count. Methods return raw records/scalars; the
    ``Giveaways`` cog wraps them in ``Giveaway`` records.
    """

    async def get_giveaway(self, giveaway_id: int) -> asyncpg.Record | None:
        """Fetches a giveaway by ID."""
        return await self.fetchrow("SELECT * FROM giveaways WHERE id = $1 LIMIT 1;", giveaway_id)

    async def get_guild_giveaway(self, guild_id: int, giveaway_id: int) -> asyncpg.Record | None:
        """Fetches a giveaway by ID, scoped to a guild."""
        return await self.fetchrow(
            "SELECT * FROM giveaways WHERE guild_id = $1 AND id = $2 LIMIT 1;", guild_id, giveaway_id)

    async def get_guild_giveaways(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches every giveaway in a guild."""
        return await self.fetch("SELECT * FROM giveaways WHERE guild_id = $1;", guild_id)

    async def create_giveaway(
            self, channel_id: int, message_id: int, guild_id: int, author_id: int, metadata: dict[str, Any]
    ) -> int:
        """Inserts a new giveaway and returns its ID."""
        query = """
            INSERT INTO giveaways (channel_id, message_id, guild_id, author_id, metadata)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            RETURNING id;
        """
        return await self.fetchval(query, channel_id, message_id, guild_id, author_id, metadata)

    async def set_entries(self, giveaway_id: int, entries: Iterable[int]) -> None:
        """Replaces the entrant set of a giveaway."""
        await self.execute("UPDATE giveaways SET entries = $1 WHERE id = $2;", entries, giveaway_id)

    async def delete_giveaway(self, giveaway_id: int) -> None:
        """Deletes a giveaway."""
        await self.delete_where("giveaways", ("id",), (giveaway_id,))


# -- Tags ------------------------------------------------------------------


def _is_id(name_or_id: str | int) -> bool:
    """Returns whether ``name_or_id`` should be treated as a tag ID rather than a name."""
    return isinstance(name_or_id, int) or (isinstance(name_or_id, str) and name_or_id.isdigit())


class TagsRepository(BaseRepository):
    """Data access for the ``tags`` and ``tag_lookup`` tables.

    Tags are the per-guild user-authored snippets; ``tag_lookup`` stores both the
    canonical entry for every tag and any aliases that redirect to it. The methods
    here return raw records/scalars - wrapping them in the ``Tag`` / ``AliasTag``
    domain records (which need a ``bot`` reference) is left to the ``Tags`` cog.

    A handful of statistics helpers additionally read the shared ``commands`` table
    to report how often the ``tag`` command has been invoked.
    """

    # -- mutation (BaseRecord update hook) --------------------------------

    async def update_tag(
            self,
            tag_id: int,
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> asyncpg.Record:
        """Updates a tag row and returns the full updated record."""
        return cast(
            'asyncpg.Record',
            await self.update_returning("tags", ("id",), (tag_id,), values, connection=connection),
        )

    async def create_tag(
            self,
            name: str,
            content: str,
            owner_id: int,
            location_id: int,
            *,
            connection: asyncpg.Connection | None = None,
    ) -> None:
        """Inserts a tag and its canonical ``tag_lookup`` entry in a single statement.

        The caller is responsible for the surrounding transaction and for mapping
        ``asyncpg`` integrity errors onto user-facing messages.
        """
        query = """
            WITH tag_insert AS (
                INSERT INTO tags (name, content, owner_id, location_id)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id)
            INSERT
            INTO tag_lookup (name, owner_id, location_id, parent_id)
            VALUES ($1, $3, $4, (SELECT id FROM tag_insert));
        """
        await (connection or self.db).execute(query, name, content, owner_id, location_id)

    async def create_alias(self, new_alias: str, original: str, location_id: int, owner_id: int) -> str:
        """Creates an alias that redirects to an existing tag, returning the command status."""
        query = """
            INSERT INTO tag_lookup (name, owner_id, location_id, parent_id)
            SELECT $1, $4, tag_lookup.location_id, tag_lookup.parent_id
            FROM tag_lookup
            WHERE tag_lookup.location_id = $3
              AND LOWER(tag_lookup.name) = $2;
        """
        return await self.execute(query, new_alias, original.lower(), location_id, owner_id)

    async def delete_tag(self, tag_id: int) -> None:
        """Deletes a tag and every alias that points to it."""
        await self.execute("DELETE FROM tags WHERE id=$1;", tag_id)
        await self.execute("DELETE FROM tag_lookup WHERE parent_id=$1;", tag_id)

    async def delete_alias(self, alias_id: int) -> None:
        """Deletes a single alias row."""
        await self.delete_where("tag_lookup", ("id",), (alias_id,))

    async def transfer_aliases(
            self, tag_id: int, owner_id: int, *, connection: asyncpg.Connection | None = None
    ) -> None:
        """Reassigns ownership of every alias of a tag."""
        await (connection or self.db).execute(
            "UPDATE tag_lookup SET owner_id=$1 WHERE parent_id=$2;", owner_id, tag_id)

    async def transfer_alias(
            self, alias_id: int, owner_id: int, *, connection: asyncpg.Connection | None = None
    ) -> None:
        """Reassigns ownership of a single alias."""
        await (connection or self.db).execute(
            "UPDATE tag_lookup SET owner_id=$1 WHERE id=$2;", owner_id, alias_id)

    # -- lookups ----------------------------------------------------------

    async def get_tag_record(
            self, name_or_id: str | int, *, owner_id: int | None = None, location_id: int | None = None
    ) -> asyncpg.Record | None:
        """Fetches a parent tag directly from the ``tags`` table by name or ID."""
        form: dict[str, Any] = {}
        if location_id:
            form['location_id'] = location_id
        if owner_id:
            form['owner_id'] = owner_id

        if _is_id(name_or_id):
            form['tags.id'] = name_or_id
        else:
            assert isinstance(name_or_id, str)
            form['LOWER(tags.name)'] = name_or_id.lower()

        where = ' AND '.join(f'{k}=${i}' for i, k in enumerate(form, 1))
        query = f"SELECT * FROM tags WHERE {where} LIMIT 1;"
        return await self.fetchrow(query, *form.values())

    async def get_parent_record_via_alias(self, name_or_id: str | int) -> asyncpg.Record | None:
        """Fetches a parent tag by resolving a name/ID against the ``tag_lookup`` table."""
        if _is_id(name_or_id):
            clause, value = 'tags.id', name_or_id
        else:
            assert isinstance(name_or_id, str)
            clause, value = 'LOWER(tags.name)', name_or_id.lower()

        query = f"""
            SELECT tags.*
            FROM tags
                     INNER JOIN tag_lookup t on t.parent_id = tags.id
            WHERE {clause}=$1
            LIMIT 1;
        """
        return await self.fetchrow(query, value)

    async def get_alias_records(
            self,
            parent_id: int,
            parent_name: str,
            *,
            owner_id: int | None = None,
            location_id: int | None = None,
    ) -> list[asyncpg.Record]:
        """Fetches every alias of a tag, excluding the canonical entry."""
        form: dict[str, Any] = {}
        if location_id:
            form['location_id'] = location_id
        if owner_id:
            form['owner_id'] = owner_id
        form['parent_id'] = parent_id

        where = ' AND '.join(f'{k}=${i}' for i, k in enumerate(form, 1))
        query = f"SELECT * FROM tag_lookup WHERE name != ${len(form) + 1} AND {where}"
        return await self.fetch(query, *form.values(), parent_name)

    async def get_alias_record(
            self, name_or_id: str | int, *, owner_id: int | None = None, location_id: int | None = None
    ) -> asyncpg.Record | None:
        """Fetches a single alias row by name or ID, narrowed to the given guild/owner."""
        form: dict[str, Any] = {}
        if location_id:
            form['location_id'] = location_id
        if owner_id:
            form['owner_id'] = owner_id

        if _is_id(name_or_id):
            form['id'] = name_or_id
        else:
            assert isinstance(name_or_id, str)
            form['LOWER(name)'] = name_or_id.lower()

        where = ' AND '.join(f'{k}=${i}' for i, k in enumerate(form, 1))
        query = f"SELECT * FROM tag_lookup WHERE {where} LIMIT 1;"
        return await self.fetchrow(query, *form.values())

    async def get_similar_aliases(self, location_id: int, name: str) -> list[asyncpg.Record]:
        """Fetches up to 25 aliases whose names are similar to ``name``."""
        query = """
            SELECT tag_lookup.*
            FROM tag_lookup
                     INNER JOIN tags t on t.id = tag_lookup.parent_id
            WHERE tag_lookup.location_id = $1
              AND tag_lookup.name % $2
            ORDER BY similarity(tag_lookup.name, $2) DESC
            LIMIT 25;
        """
        return await self.fetch(query, location_id, name)

    async def get_tag_rank(self, tag_id: int) -> int:
        """Returns the use-rank of a tag within its guild."""
        query = """
            SELECT (SELECT COUNT(*)
                    FROM tags second
                    WHERE (second.uses, second.id) >= (first.uses, first.id)
                      AND second.location_id = first.location_id) AS rank
            FROM tags first
            WHERE first.id = $1
        """
        return await self.fetchval(query, tag_id)

    # -- autocomplete sources --------------------------------------------

    async def get_guild_tags(self, location_id: int) -> list[asyncpg.Record]:
        """Fetches every parent tag in a guild, ordered by uses."""
        return await self.fetch("SELECT * FROM tags WHERE location_id=$1 ORDER BY uses;", location_id)

    async def get_owned_tags(self, location_id: int, owner_id: int) -> list[asyncpg.Record]:
        """Fetches every parent tag owned by a member in a guild, ordered by uses."""
        return await self.fetch(
            "SELECT * FROM tags WHERE location_id=$1 AND owner_id=$2 ORDER BY uses;", location_id, owner_id)

    async def get_guild_aliases(self, location_id: int) -> list[asyncpg.Record]:
        """Fetches every alias (and canonical entry) in a guild, ordered by parent uses."""
        query = """
            SELECT tag_lookup.*
            FROM tag_lookup
                     INNER JOIN tags ON tags.id = tag_lookup.parent_id
            WHERE tag_lookup.location_id = $1
            ORDER BY uses DESC;
        """
        return await self.fetch(query, location_id)

    async def filter_tags(
            self, location_id: int, *, query: str | None = None, owner_id: int | None = None, sort: str = 'name'
    ) -> list[asyncpg.Record]:
        """Fetches ``(name, id)`` rows from ``tag_lookup`` for the list/search commands."""
        order = {
            'id': 'id',
            'newest': 'created_at DESC',
            'oldest': 'created_at ASC',
            'name': 'name',
        }.get(sort, 'name')

        values: list[Any] = [location_id]
        where = ['location_id=$1']

        if query:
            values.append(query)
            where.append(f'name % ${len(values)}')
            if sort == 'name':
                order = f'similarity(name, ${len(values)}) DESC'

        if owner_id:
            values.append(owner_id)
            where.append(f'owner_id=${len(values)}')

        sql = f"SELECT name, id FROM tag_lookup WHERE {' AND '.join(where)} ORDER BY {order};"
        return await self.fetch(sql, *values)

    async def export_tags(self, location_id: int, *, owner_id: int | None = None) -> list[asyncpg.Record]:
        """Fetches ``(name, content)`` rows for the CSV export command."""
        form: dict[str, Any] = {'location_id': location_id}
        if owner_id is not None:
            form['owner_id'] = owner_id

        where = ' AND '.join(f'{k}=${i}' for i, k in enumerate(form, 1))
        query = f"SELECT name, content FROM tags WHERE {where};"
        return await self.fetch(query, *form.values())

    # -- ownership / bulk operations -------------------------------------

    async def count_owned_tags(self, location_id: int, owner_id: int) -> int:
        """Counts the tags a member owns in a guild."""
        return await self.fetchval(
            "SELECT COUNT(*) FROM tags WHERE location_id=$1 AND owner_id=$2;", location_id, owner_id)

    async def delete_owned_tags(self, location_id: int, owner_id: int) -> None:
        """Deletes every tag a member owns in a guild."""
        await self.execute("DELETE FROM tags WHERE location_id=$1 AND owner_id=$2;", location_id, owner_id)

    # -- statistics -------------------------------------------------------

    async def count_tags(self, location_id: int) -> int:
        """Counts all tags in a guild."""
        return await self.fetchval("SELECT COUNT(*) as total_tags FROM tags WHERE location_id=$1;", location_id)

    async def count_tag_command_uses(self, location_id: int) -> int:
        """Counts how many times the ``tag`` command has been invoked in a guild."""
        return await self.fetchval(
            "SELECT COUNT(*) FROM commands WHERE guild_id=$1 AND command='tag';", location_id)

    async def count_member_tag_command_uses(self, location_id: int, author_id: int) -> int:
        """Counts how many times a member has invoked the ``tag`` command in a guild."""
        query = "SELECT COUNT(*) FROM commands WHERE guild_id=$1 AND command='tag' AND author_id=$2;"
        return await self.fetchval(query, location_id, author_id)

    async def get_most_used_tags(self, location_id: int, *, limit: int = 3) -> list[asyncpg.Record]:
        """Fetches the ``(name, uses)`` of the most-used tags in a guild."""
        query = """
            SELECT
                id,
                name,
                uses,
                created_at,
                owner_id
            FROM tags
            WHERE location_id=$1
            ORDER BY uses DESC
            LIMIT $2;
        """
        return await self.fetch(query, location_id, limit)

    async def get_top_tag_users(self, location_id: int, *, limit: int = 3) -> list[asyncpg.Record]:
        """Fetches the members who have invoked the ``tag`` command most in a guild."""
        query = """
            SELECT
                COUNT(*) AS "uses",
                author_id
            FROM commands
            WHERE guild_id=$1 AND command='tag'
            GROUP BY author_id
            ORDER BY COUNT(*) DESC
            LIMIT $2;
        """
        return await self.fetch(query, location_id, limit)

    async def get_top_tag_creators(self, location_id: int, *, limit: int = 3) -> list[asyncpg.Record]:
        """Fetches the members who own the most tags in a guild."""
        query = """
            SELECT
               COUNT(*) AS "count",
               owner_id
            FROM tags
            WHERE location_id=$1
            GROUP BY owner_id
            ORDER BY COUNT(*) DESC
            LIMIT $2;
        """
        return await self.fetch(query, location_id, limit)

    async def get_member_tag_summary(self, location_id: int, owner_id: int) -> asyncpg.Record | None:
        """Fetches the ``(count, total_uses)`` summary of a member's tags in a guild."""
        query = """
            SELECT COUNT(*) OVER ()  AS "count",
                   SUM(uses) OVER () AS "total_uses"
            FROM tags
            WHERE location_id = $1
              AND owner_id = $2
            ORDER BY uses DESC
            LIMIT 1;
        """
        return await self.fetchrow(query, location_id, owner_id)

    async def get_member_top_tags(self, location_id: int, owner_id: int, *, limit: int = 3) -> list[asyncpg.Record]:
        """Fetches the ``(name, uses)`` of a member's most-used tags in a guild."""
        query = """
            SELECT name,
                   uses
            FROM tags
            WHERE location_id = $1
              AND owner_id = $2
            ORDER BY uses DESC
            LIMIT $3;
        """
        return await self.fetch(query, location_id, owner_id, limit)


# -- Highlights ------------------------------------------------------------


class HighlightsRepository(BaseRepository):
    """Data access for the ``highlights`` table.

    Each row is a user's highlight configuration within a guild: the trigger
    ``lookup`` set and the ``blocked`` entity set. Methods return raw records;
    the ``Highlights`` cog wraps them in ``HighlightConfig`` records.
    """

    async def update_config(
            self,
            config_id: int,
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> asyncpg.Record:
        """Updates a highlight row and returns the full updated record."""
        return cast(
            'asyncpg.Record',
            await self.update_returning("highlights", ("id",), (config_id,), values, connection=connection),
        )

    async def get_guild_configs(self, location_id: int) -> list[asyncpg.Record]:
        """Fetches every highlight configuration in a guild."""
        return await self.fetch("SELECT * FROM highlights WHERE location_id = $1;", location_id)

    async def get_config(self, location_id: int, user_id: int) -> asyncpg.Record | None:
        """Fetches a user's highlight configuration in a guild, if it exists."""
        return await self.fetchrow(
            "SELECT * FROM highlights WHERE location_id = $1 AND user_id = $2;", location_id, user_id)

    async def create_config(self, user_id: int, location_id: int) -> asyncpg.Record:
        """Inserts a blank highlight configuration for a user in a guild and returns it."""
        return await self.fetchrow(
            "INSERT INTO highlights (user_id, location_id) VALUES ($1, $2) RETURNING *;", user_id, location_id)

    async def delete_config(self, config_id: int) -> None:
        """Deletes a highlight configuration."""
        await self.delete_where("highlights", ("id",), (config_id,))

    async def get_import_locations(self, user_id: int, exclude_location_id: int) -> list[asyncpg.Record]:
        """Fetches the guild IDs where a user has highlights, excluding the current guild."""
        query = """
            SELECT location_id
            FROM highlights
            WHERE user_id = $1
            AND location_id != $2
            AND lookup IS NOT NULL;
        """
        return await self.fetch(query, user_id, exclude_location_id)


# -- Starboard -------------------------------------------------------------


class StarboardRepository(BaseRepository):
    """Data access for the ``starboard_config`` and ``starboard_entries`` tables.

    ``starboard_config`` holds one row of per-guild settings (channel, threshold, star
    emoji, self-star toggle, ignore list); ``starboard_entries`` tracks each original
    message that has been mirrored to the starboard, keyed by the *original* message id.
    Methods return raw records/scalars; the ``Starboard`` cog wraps the config row in a
    :class:`~app.cogs.starboard.models.StarboardConfig`.
    """

    # -- config -----------------------------------------------------------

    async def get_config(self, guild_id: int) -> asyncpg.Record | None:
        """Fetches a guild's starboard config row, or ``None`` if never configured."""
        return await self.fetchrow("SELECT * FROM starboard_config WHERE guild_id = $1;", guild_id)

    async def upsert_config(self, guild_id: int, **columns: object) -> asyncpg.Record:
        """Inserts or updates the given config columns for a guild, returning the row.

        Only the columns passed are written; everything else falls back to its default
        (on insert) or keeps its current value (on update).
        """
        keys = list(columns)
        insert_cols = ', '.join(['guild_id', *keys])
        placeholders = ', '.join(f'${i}' for i in range(1, len(keys) + 2))
        updates = ', '.join(f'{key} = EXCLUDED.{key}' for key in keys) or 'guild_id = EXCLUDED.guild_id'
        query = f"""
            INSERT INTO starboard_config ({insert_cols})
            VALUES ({placeholders})
            ON CONFLICT (guild_id) DO UPDATE SET {updates}
            RETURNING *;
        """
        return await self.fetchrow(query, guild_id, *columns.values())

    # -- entries ----------------------------------------------------------

    async def get_entry(self, message_id: int) -> asyncpg.Record | None:
        """Fetches the starboard entry for an original message id."""
        return await self.fetchrow("SELECT * FROM starboard_entries WHERE message_id = $1;", message_id)

    async def get_entry_by_starboard_message(self, starboard_message_id: int) -> asyncpg.Record | None:
        """Fetches the entry whose mirrored post has the given starboard message id."""
        return await self.fetchrow(
            "SELECT * FROM starboard_entries WHERE starboard_message_id = $1;", starboard_message_id)

    async def create_entry(
        self,
        message_id: int,
        guild_id: int,
        channel_id: int,
        author_id: int,
        starboard_message_id: int,
        star_count: int,
    ) -> None:
        """Records a newly mirrored message."""
        query = """
            INSERT INTO starboard_entries
                (message_id, guild_id, channel_id, author_id, starboard_message_id, star_count)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (message_id) DO UPDATE
                SET starboard_message_id = EXCLUDED.starboard_message_id,
                    star_count = EXCLUDED.star_count;
        """
        await self.execute(query, message_id, guild_id, channel_id, author_id, starboard_message_id, star_count)

    async def update_star_count(self, message_id: int, star_count: int) -> None:
        """Updates the cached star count for an entry."""
        await self.execute(
            "UPDATE starboard_entries SET star_count = $2 WHERE message_id = $1;", message_id, star_count)

    async def delete_entry(self, message_id: int) -> None:
        """Removes a starboard entry by original message id."""
        await self.delete_where("starboard_entries", ("message_id",), (message_id,))

    async def delete_entries(self, message_ids: Iterable[int]) -> None:
        """Removes several starboard entries by original message id."""
        await self.execute(
            "DELETE FROM starboard_entries WHERE message_id = ANY($1::bigint[]);", list(message_ids))
