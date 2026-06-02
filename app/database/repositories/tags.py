from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    from collections.abc import Callable

    import asyncpg

__all__ = ('TagsRepository',)


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
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> asyncpg.Record:
        """Applies a :class:`~app.database.base.BaseRecord`-style update to a tag row."""
        query = f"""
            UPDATE tags
            SET {', '.join(map(key, enumerate(values.keys(), start=2)))}
            WHERE id = $1
            RETURNING *;
        """
        return cast('asyncpg.Record', await (connection or self.db).fetchrow(query, tag_id, *values.values()))

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
        await self.execute("DELETE FROM tag_lookup WHERE id=$1;", alias_id)

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
        """Fetches a single alias row matching the given filters."""
        form: dict[str, Any] = {}
        if location_id:
            form['location_id'] = location_id
        if owner_id:
            form['owner_id'] = owner_id

        where = ' AND '.join(f'{k}=${i}' for i, k in enumerate(form, 1))
        query = f"SELECT * FROM tag_lookup WHERE {where} LIMIT 1;"
        return await self.fetchrow(query, name_or_id)

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

        if not query:
            sql = """
                SELECT name, id
                FROM tag_lookup
                WHERE location_id=$1
            """
            if owner_id:
                sql += " AND owner_id=$2"
                values: tuple[Any, ...] = (location_id, owner_id)
            else:
                values = (location_id,)

            sql += f" ORDER BY {order};"
        else:
            if sort == 'name':
                order = 'similarity(name, $2) DESC'

            sql = f"""
                SELECT name, id
                FROM tag_lookup
                WHERE location_id=$1 AND name % $2
                ORDER BY {order};
            """
            if owner_id:
                sql += " AND owner_id=$3"
                values = (location_id, query, owner_id)
            else:
                values = (location_id, query)

            sql += f" ORDER BY {order};"

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
                name,
                uses
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
