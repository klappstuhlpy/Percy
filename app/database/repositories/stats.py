from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any, Literal

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import asyncpg

__all__ = ('StatsRepository',)


class StatsRepository(BaseRepository):
    """Data access for the bot's telemetry tables.

    Covers command-usage tracking (``commands``), presence transitions
    (``presence_history``), username/nickname changes (``item_history``) and
    avatar snapshots (``avatar_history``). Methods return raw records/scalars;
    the ``Stats`` cog owns the Discord-side formatting and batching.
    """

    # -- commands ---------------------------------------------------------

    async def insert_commands(self, batch: list[Any]) -> None:
        """Bulk-inserts a batch of command invocations from a JSON payload."""
        query = """
            INSERT INTO commands (guild_id, channel_id, author_id, used, prefix, command, failed, app_command, error)
            SELECT x.guild,
                   x.channel,
                   x.author,
                   x.used,
                   x.prefix,
                   x.command,
                   x.failed,
                   x.app_command,
                   x.error
            FROM jsonb_to_recordset($1::jsonb)
                AS x(
                        guild BIGINT,
                        channel BIGINT,
                        author BIGINT,
                        used TIMESTAMP,
                        prefix TEXT,
                        command TEXT,
                        failed BOOLEAN,
                        app_command BOOLEAN,
                        error TEXT
                );
        """
        await self.execute(query, batch)

    async def get_command_usage(
            self,
            guild_id: int | None = None,
            author_id: int | None = None,
            *,
            days: int | None = None,
            group_by: Literal['author_id', 'command', 'guild_id'] = 'command',
            limit: int = 5,
    ) -> list[asyncpg.Record]:
        """Returns aggregated command-usage counts, grouped and optionally filtered.

        If both ``guild_id`` and ``author_id`` are ``None`` the statistics are global.
        """
        args: tuple[Any, ...] = ()
        query = f"SELECT {group_by}, COUNT(*) as uses FROM commands"

        def _pref() -> str:
            return "WHERE" if not args else "AND"

        if guild_id:
            query += " WHERE guild_id = $1"
            args += (guild_id,)
        if author_id:
            query += f" {_pref()} author_id = ${len(args) + 1}"
            args += (author_id,)
        if days:
            query += f" {_pref()} used > (CURRENT_TIMESTAMP - ${len(args) + 1}::interval)"
            args += (datetime.timedelta(days=days),)

        query += f" GROUP BY {group_by} ORDER BY uses DESC LIMIT {limit};"
        return await self.fetch(query, *args)

    async def get_command_invokation_count(self, command: str) -> int:
        """Returns the number of times a command has been invoked."""
        return await self.fetchval("SELECT COUNT(*) FROM commands WHERE command = $1;", command)

    async def get_command_summary(
            self, guild_id: int, author_id: int | None = None
    ) -> asyncpg.Record:
        """Returns the total command count and earliest use for a guild (optionally a member)."""
        if author_id is None:
            query = "SELECT COUNT(*), MIN(used) FROM commands WHERE guild_id=$1;"
            return await self.fetchrow(query, guild_id)

        query = "SELECT COUNT(*), MIN(used) FROM commands WHERE guild_id=$1 AND author_id=$2;"
        return await self.fetchrow(query, guild_id, author_id)

    async def count_all_commands(self) -> int:
        """Returns the total number of commands ever used."""
        return await self.fetchval("SELECT COUNT(*) FROM commands;")

    async def get_daily_status_counts(self) -> list[asyncpg.Record]:
        """Returns ``(failed, count)`` for commands used in the last 24 hours."""
        query = """
            SELECT failed,
                   COUNT(*)
            FROM commands
            WHERE used > (CURRENT_TIMESTAMP - INTERVAL '1 day')
            GROUP BY failed;
        """
        return await self.fetch(query)

    async def get_recent_command_history(self, limit: int) -> list[asyncpg.Record]:
        """Returns the most recently used commands."""
        query = f"""
            SELECT
                CASE failed
                    WHEN TRUE THEN command || ' [!]'
                    ELSE command
                END AS "command",
                to_char(used, 'Mon DD HH12:MI:SS AM') AS "invoked",
                author_id,
                guild_id
            FROM commands
            ORDER BY used DESC
            LIMIT {limit};
        """
        return await self.fetch(query)

    async def get_command_history_for(
            self, command: str, interval: datetime.timedelta
    ) -> list[asyncpg.Record]:
        """Returns per-guild success/failure counts for a command over an interval."""
        query = """
            SELECT *,
                   t.success + t.failed AS "total"
            FROM (SELECT guild_id,
                         SUM(CASE WHEN failed THEN 0 ELSE 1 END) AS "success",
                         SUM(CASE WHEN failed THEN 1 ELSE 0 END) AS "failed"
                  FROM commands
                  WHERE command = $1
                    AND used > (CURRENT_TIMESTAMP - $2::interval)
                  GROUP BY guild_id) AS t
            ORDER BY "total" DESC
            LIMIT 30;
        """
        return await self.fetch(query, command, interval)

    async def get_command_history_guild(self, guild_id: int) -> list[asyncpg.Record]:
        """Returns the most recent command history for a guild."""
        query = """
            SELECT CASE failed
                       WHEN TRUE THEN command || ' [!]'
                       ELSE command
                       END AS "command",
                   channel_id,
                   author_id,
                   used
            FROM commands
            WHERE guild_id = $1
            ORDER BY used DESC
            LIMIT 15;
        """
        return await self.fetch(query, guild_id)

    async def get_command_history_user(self, user_id: int) -> list[asyncpg.Record]:
        """Returns the most recent command history for a user."""
        query = """
            SELECT CASE failed
                       WHEN TRUE THEN command || ' [!]'
                       ELSE command
                       END AS "command",
                   guild_id,
                   used
            FROM commands
            WHERE author_id = $1
            ORDER BY used DESC
            LIMIT 20;
        """
        return await self.fetch(query, user_id)

    async def get_command_history_by_cog(
            self, command_names: list[str], interval: datetime.timedelta
    ) -> list[asyncpg.Record]:
        """Returns success/failure counts for a set of commands over an interval."""
        query = """
            SELECT *,
                   t.success + t.failed AS "total"
            FROM (SELECT command,
                         SUM(CASE WHEN failed THEN 0 ELSE 1 END) AS "success",
                         SUM(CASE WHEN failed THEN 1 ELSE 0 END) AS "failed"
                  FROM commands
                  WHERE command = any ($1::text[])
                    AND used > (CURRENT_TIMESTAMP - $2::interval)
                  GROUP BY command) AS t
            ORDER BY "total" DESC
            LIMIT 30;
        """
        return await self.fetch(query, command_names, interval)

    async def get_command_history_grouped(
            self, interval: datetime.timedelta
    ) -> list[asyncpg.Record]:
        """Returns per-command success/failure counts over an interval (ungrouped by cog)."""
        query = """
            SELECT *,
                   t.success + t.failed AS "total"
            FROM (SELECT command,
                         SUM(CASE WHEN failed THEN 0 ELSE 1 END) AS "success",
                         SUM(CASE WHEN failed THEN 1 ELSE 0 END) AS "failed"
                  FROM commands
                  WHERE used > (CURRENT_TIMESTAMP - $1::interval)
                  GROUP BY command) AS t;
        """
        return await self.fetch(query, interval)

    # -- presence_history -------------------------------------------------

    async def delete_old_presence_history(self) -> None:
        """Removes presence-history entries older than 30 days."""
        await self.execute(
            """
                DELETE FROM presence_history
                WHERE changed_at < (CURRENT_TIMESTAMP - INTERVAL '30 days');
            """
        )

    async def insert_presence(self, uuid: int, status: str | None, status_before: str | None) -> None:
        """Records a member's status transition."""
        await self.execute(
            "INSERT INTO presence_history (uuid, status, status_before) VALUES ($1, $2, $3);",
            uuid, status, status_before,
        )

    async def get_presence_history(self, user_id: int, *, days: int = 30) -> list[asyncpg.Record]:
        """Returns a user's status transitions within the given number of days."""
        return await self.fetch(
            """
                SELECT status, status_before, changed_at
                FROM presence_history
                WHERE uuid = $1
                  AND (changed_at AT TIME ZONE 'UTC') > (CURRENT_TIMESTAMP - $2::interval)
                ORDER BY changed_at DESC;
            """,
            user_id, datetime.timedelta(days=days),
        )

    # -- item_history -----------------------------------------------------

    async def insert_item_history(self, uuid: int, item_type: str, item_value: str) -> None:
        """Records a username/nickname change."""
        await self.execute(
            "INSERT INTO item_history (uuid, item_type, item_value) VALUES ($1, $2, $3);",
            uuid, item_type, item_value,
        )

    async def get_item_history(
            self, user_id: int, item_type: Literal['name', 'nickname']
    ) -> list[asyncpg.Record]:
        """Returns a user's name or nickname change history."""
        return await self.fetch(
            """
                SELECT item_value, changed_at
                FROM item_history
                WHERE uuid = $1
                  AND item_type = $2
                ORDER BY changed_at DESC;
            """,
            user_id, item_type,
        )

    # -- avatar_history ---------------------------------------------------

    async def insert_avatar(self, user_id: int, name: str, image: bytes) -> None:
        """Stores a new avatar snapshot for a user via the DB helper function."""
        await self.execute("SELECT insert_avatar_history_item($1, $2, $3);", user_id, name, image)

    async def get_avatar_history(self, user_id: int, *, limit: int = 100) -> list[asyncpg.Record]:
        """Returns a user's stored avatar snapshots, oldest first."""
        return await self.fetch(
            """
                SELECT avatar, changed_at
                FROM avatar_history
                WHERE uuid = $1
                ORDER BY changed_at LIMIT $2;
            """,
            user_id, limit,
        )
