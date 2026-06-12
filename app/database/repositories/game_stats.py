from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import asyncpg

__all__ = ('GameStatsRepository',)

log = logging.getLogger(__name__)

#: Leaderboard metrics. ``winrate`` is wins / played and is only meaningful past a
#: minimum number of rounds, so its query takes a ``min_played`` floor.
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
            ``'win'``, ``'loss'`` or ``'push'`` (draw — affects neither counters nor streak).
        wagered:
            Coins risked on the round (``0`` for free-to-play games like TicTacToe).
        profit:
            Net coins change for the round; positive on a win, negative on a loss.
        """
        won = 1 if result == 'win' else 0
        lost = 1 if result == 'loss' else 0
        tied = 1 if result == 'push' else 0

        # current_streak: positive = consecutive wins, negative = consecutive losses.
        # A win extends a (>=0) streak or restarts at +1; a loss extends a (<=0)
        # streak or restarts at -1; a push leaves the streak untouched.
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
