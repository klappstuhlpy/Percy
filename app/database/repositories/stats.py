from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Any, Literal

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    from collections.abc import Sequence

    import asyncpg

__all__ = (
    'EmojiStatsRepository',
    'GameStatsRepository',
    'StatsRepository',
)

log = logging.getLogger(__name__)


# -- Stats (commands, presence, items, avatars, activity) -------------------


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

    # -- activity heatmap -------------------------------------------------

    async def get_member_daily_activity(
        self, guild_id: int, user_id: int, *, days: int = 365
    ) -> list[asyncpg.Record]:
        """Returns per-day command counts for a member, used for activity heatmaps."""
        query = """
            SELECT used::date AS day, COUNT(*) AS count
            FROM commands
            WHERE guild_id = $1 AND author_id = $2
              AND used > (CURRENT_TIMESTAMP - $3::interval)
            GROUP BY day
            ORDER BY day;
        """
        return await self.fetch(query, guild_id, user_id, datetime.timedelta(days=days))


# -- Emoji Stats -----------------------------------------------------------


class EmojiStatsRepository(BaseRepository):
    """Data access for the ``emoji_stats`` table.

    Tracks how often each custom emoji is used per guild. Methods return raw
    records/scalars; the ``Emoji`` cog owns the batching and presentation.
    """

    async def bulk_insert(self, rows: list[dict[str, Any]]) -> None:
        """Upserts a batch of ``(guild, emoji, added)`` usage counts."""
        query = """
            INSERT INTO emoji_stats (guild_id, emoji_id, total)
            SELECT x.guild, x.emoji, x.added
            FROM jsonb_to_recordset($1::jsonb)
                     AS x(
                          guild BIGINT,
                          emoji BIGINT,
                          added INT
                    )
            ON CONFLICT (guild_id, emoji_id) DO UPDATE
                SET total = emoji_stats.total + excluded.total;
        """
        await self.execute(query, rows)

    async def get_random_emoji_id(self, *, connection: asyncpg.Connection | None = None) -> int | None:
        """Returns a random emoji ID from the table, or ``None`` if it is empty."""
        query = """
            SELECT emoji_id
            FROM emoji_stats
            OFFSET FLOOR(RANDOM() * (SELECT COUNT(*) FROM emoji_stats))
            LIMIT 1;
        """
        return await (connection or self.db).fetchval(query)

    async def get_emoji_record(
            self, emoji_id: int, *, connection: asyncpg.Connection | None = None
    ) -> asyncpg.Record | None:
        """Fetches a single ``emoji_stats`` row for an emoji."""
        return await (connection or self.db).fetchrow(
            "SELECT * FROM emoji_stats WHERE emoji_id=$1 LIMIT 1;", emoji_id)

    async def get_guild_summary(self, guild_id: int) -> asyncpg.Record | None:
        """Returns the total uses (``Count``) and distinct emoji (``Emoji``) for a guild."""
        query = """
            SELECT
               COALESCE(SUM(total), 0) AS "Count",
               COUNT(*) AS "Emoji"
            FROM emoji_stats
            WHERE guild_id=$1
            GROUP BY guild_id;
        """
        return await self.fetchrow(query, guild_id)

    async def get_top_guild_emojis(self, guild_id: int, *, limit: int = 10) -> list[asyncpg.Record]:
        """Returns a guild's most-used emoji as ``(emoji_id, total)`` rows."""
        query = """
            SELECT emoji_id, total
            FROM emoji_stats
            WHERE guild_id=$1
            ORDER BY total DESC
            LIMIT $2;
        """
        return await self.fetch(query, guild_id, limit)

    async def get_emoji_guild_breakdown(self, emoji_id: int) -> list[asyncpg.Record]:
        """Returns per-guild usage counts for a single emoji as ``(guild_id, count)`` rows."""
        query = """
            SELECT guild_id, SUM(total) AS "count"
            FROM emoji_stats
            WHERE emoji_id=$1
            GROUP BY guild_id;
        """
        return await self.fetch(query, emoji_id)

    async def get_guild_emoji_stats(
            self, guild_id: int, emoji_ids: Sequence[int]
    ) -> list[asyncpg.Record]:
        """Returns usage for a specific set of a guild's emoji, ordered by total."""
        query = """
            SELECT emoji_id, total
            FROM emoji_stats
            WHERE guild_id=$1 AND emoji_id = ANY($2::bigint[])
            ORDER BY total DESC;
        """
        return await self.fetch(query, guild_id, emoji_ids)

    async def get_global_top_emojis(self, *, limit: int = 10) -> list[asyncpg.Record]:
        """Returns the most-used emoji across all guilds as ``(emoji_id, count)`` rows."""
        query = """
            SELECT emoji_id, SUM(total) AS "count"
            FROM emoji_stats
            GROUP BY emoji_id
            ORDER BY "count" DESC
            LIMIT $1;
        """
        return await self.fetch(query, limit)


# -- Game Stats ------------------------------------------------------------

LeaderboardMetric = Literal['won', 'profit', 'played', 'best_streak', 'winrate']

_METRIC_COLUMN: dict[str, str] = {
    'won': 'won',
    'profit': 'profit',
    'played': 'played',
    'best_streak': 'best_streak',
}


class GameStatsRepository(BaseRepository):
    """Data access for per-member game outcome tracking (``game_stats``).

    A single row aggregates one member's lifetime record for one game. Writes go
    through :meth:`record_result`, which performs an idempotent upsert that also
    maintains profit, biggest-win and win/loss streak bookkeeping. Reads back the
    member-facing summaries and the server-wide leaderboards consumed by the
    ``/stats games`` command group. Methods return raw records/scalars; the cog
    owns the Discord-side formatting.
    """

    # -- writes -----------------------------------------------------------

    async def record_result(
        self,
        guild_id: int,
        user_id: int,
        game: str,
        result: Literal['win', 'loss', 'push'],
        *,
        wagered: int = 0,
        profit: int = 0,
    ) -> None:
        """Records the outcome of a single round.

        Telemetry must never break gameplay, so any database error here is logged
        and swallowed rather than propagated to the caller (a game's payout flow).

        Parameters
        ----------
        guild_id, user_id:
            The member the round belongs to.
        game:
            The game key (see :class:`app.cogs.games.models.Game`).
        result:
            ``'win'``, ``'loss'`` or ``'push'`` (draw - affects neither counters nor streak).
        wagered:
            Coins risked on the round (``0`` for free-to-play games like TicTacToe).
        profit:
            Net coins change for the round; positive on a win, negative on a loss.
        """
        won = 1 if result == 'win' else 0
        lost = 1 if result == 'loss' else 0
        tied = 1 if result == 'push' else 0

        query = """
            INSERT INTO game_stats AS gs
                (guild_id, user_id, game, played, won, lost, tied, wagered, profit,
                 biggest_win, current_streak, best_streak, last_played)
            VALUES ($1, $2, $3, 1, $4, $5, $6, $7, $8::BIGINT,
                    GREATEST($8::BIGINT, 0::BIGINT),
                    CASE WHEN $4 = 1 THEN 1 WHEN $5 = 1 THEN -1 ELSE 0 END,
                    CASE WHEN $4 = 1 THEN 1 ELSE 0 END,
                    (now() AT TIME ZONE 'utc'))
            ON CONFLICT (guild_id, user_id, game) DO UPDATE SET
                played      = gs.played + 1,
                won         = gs.won + $4,
                lost        = gs.lost + $5,
                tied        = gs.tied + $6,
                wagered     = gs.wagered + $7,
                profit      = gs.profit + $8::BIGINT,
                biggest_win = GREATEST(gs.biggest_win::BIGINT, $8::BIGINT),
                current_streak = CASE
                    WHEN $4 = 1 THEN (CASE WHEN gs.current_streak >= 0 THEN gs.current_streak + 1 ELSE 1 END)
                    WHEN $5 = 1 THEN (CASE WHEN gs.current_streak <= 0 THEN gs.current_streak - 1 ELSE -1 END)
                    ELSE gs.current_streak END,
                best_streak = GREATEST(
                    gs.best_streak,
                    CASE WHEN $4 = 1 THEN (CASE WHEN gs.current_streak >= 0 THEN gs.current_streak + 1 ELSE 1 END)
                         ELSE 0 END),
                last_played = (now() AT TIME ZONE 'utc');
        """
        try:
            await self.execute(query, guild_id, user_id, game, won, lost, tied, wagered, profit)
        except Exception:
            log.exception("Failed to record game result (guild=%s user=%s game=%s)", guild_id, user_id, game)

    # -- member reads -----------------------------------------------------

    async def get_member_games(self, guild_id: int, user_id: int) -> list[asyncpg.Record]:
        """All per-game rows for a member, most played first."""
        query = """
            SELECT game, played, won, lost, tied, wagered, profit, biggest_win, current_streak, best_streak
            FROM game_stats
            WHERE guild_id = $1 AND user_id = $2
            ORDER BY played DESC;
        """
        return await self.fetch(query, guild_id, user_id)

    async def get_member_totals(self, guild_id: int, user_id: int) -> asyncpg.Record | None:
        """Member totals summed across every game (``None`` if they've never played)."""
        query = """
            SELECT COALESCE(SUM(played), 0)  AS played,
                   COALESCE(SUM(won), 0)     AS won,
                   COALESCE(SUM(lost), 0)    AS lost,
                   COALESCE(SUM(tied), 0)    AS tied,
                   COALESCE(SUM(wagered), 0) AS wagered,
                   COALESCE(SUM(profit), 0)  AS profit,
                   COALESCE(MAX(biggest_win), 0) AS biggest_win
            FROM game_stats
            WHERE guild_id = $1 AND user_id = $2
            HAVING COUNT(*) > 0;
        """
        return await self.fetchrow(query, guild_id, user_id)

    # -- leaderboard reads ------------------------------------------------

    async def get_leaderboard(
        self,
        guild_id: int,
        *,
        game: str | None = None,
        metric: LeaderboardMetric = 'won',
        limit: int = 10,
        min_played: int = 1,
    ) -> list[asyncpg.Record]:
        """Server leaderboard for a metric.

        When ``game`` is given the board is scoped to that game; otherwise each
        member's rows are summed across all games. ``winrate`` ranks by
        wins / played and only includes members with at least ``min_played`` rounds.
        """
        scope = "AND game = $2" if game is not None else ""
        args: list[object] = [guild_id]
        if game is not None:
            args.append(game)
        played_param = f"${len(args) + 1}"
        args.append(min_played)

        order = "winrate DESC, played DESC" if metric == 'winrate' else f"{_METRIC_COLUMN[metric]} DESC"

        query = f"""
            SELECT user_id,
                   SUM(played)  AS played,
                   SUM(won)     AS won,
                   SUM(lost)    AS lost,
                   SUM(tied)    AS tied,
                   SUM(wagered) AS wagered,
                   SUM(profit)  AS profit,
                   MAX(biggest_win) AS biggest_win,
                   MAX(best_streak) AS best_streak,
                   (SUM(won)::float / NULLIF(SUM(played), 0)) AS winrate
            FROM game_stats
            WHERE guild_id = $1 {scope}
            GROUP BY user_id
            HAVING SUM(played) >= {played_param}
            ORDER BY {order}
            LIMIT {limit};
        """
        return await self.fetch(query, *args)

    async def get_guild_overview(self, guild_id: int) -> list[asyncpg.Record]:
        """Per-game totals across the whole guild (rounds played + unique players)."""
        query = """
            SELECT game,
                   SUM(played)        AS played,
                   SUM(won)           AS won,
                   COUNT(*)           AS players,
                   COALESCE(SUM(profit), 0) AS profit
            FROM game_stats
            WHERE guild_id = $1
            GROUP BY game
            ORDER BY played DESC;
        """
        return await self.fetch(query, guild_id)
