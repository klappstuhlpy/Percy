-- Revises: V24
-- Creation Date: 2026-06-12 00:00:00.000000+00:00 UTC
-- Reason: per-game win/loss statistics with server-wide leaderboards

-- Aggregated outcome counters per (guild, member, game). One row tracks the lifetime
-- record for a single game so leaderboards are a cheap indexed ORDER BY rather than a
-- scan over an append-only event log.
CREATE TABLE IF NOT EXISTS game_stats
(
    guild_id       BIGINT    NOT NULL,
    user_id        BIGINT    NOT NULL,
    game           TEXT      NOT NULL,  -- poker | blackjack | slots | roulette | tower | tictactoe | minesweeper | hangman
    played         INTEGER   NOT NULL DEFAULT 0,
    won            INTEGER   NOT NULL DEFAULT 0,
    lost           INTEGER   NOT NULL DEFAULT 0,
    tied           INTEGER   NOT NULL DEFAULT 0,  -- pushes / draws (count toward neither win nor loss)
    wagered        BIGINT    NOT NULL DEFAULT 0,  -- total coins risked across all rounds
    profit         BIGINT    NOT NULL DEFAULT 0,  -- net coins won/lost (can be negative)
    biggest_win    BIGINT    NOT NULL DEFAULT 0,  -- largest single-round net gain
    current_streak INTEGER   NOT NULL DEFAULT 0,  -- > 0 active win streak, < 0 active loss streak
    best_streak    INTEGER   NOT NULL DEFAULT 0,  -- longest win streak ever reached
    last_played    TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    PRIMARY KEY (guild_id, user_id, game)
);

-- Leaderboard read paths: "top winners" and "most profitable" per guild/game.
CREATE INDEX IF NOT EXISTS game_stats_won_idx ON game_stats (guild_id, game, won DESC);
CREATE INDEX IF NOT EXISTS game_stats_profit_idx ON game_stats (guild_id, game, profit DESC);
