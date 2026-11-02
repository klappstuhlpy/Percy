-- Game stats, bets and trivia

-- == V25__game_stats ==
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

-- == V64__game_bets ==
-- persisting them lets startup refund whatever a restart orphaned

CREATE TABLE IF NOT EXISTS game_bets (
    id         BIGSERIAL PRIMARY KEY,
    game       TEXT   NOT NULL,
    guild_id   BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    user_id    BIGINT NOT NULL,
    amount     BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS game_bets_table_idx ON game_bets (game, channel_id);

-- == V74__trivia_lists ==
-- Guild-uploaded trivia lists (app/cogs/games/cog.py). Custom questions are free-text:
-- ``answers`` are all accepted spellings. ``trivia_scores`` counts custom-list rounds only.
CREATE TABLE IF NOT EXISTS trivia_lists
(
    id         SERIAL PRIMARY KEY,
    guild_id   BIGINT       NOT NULL,
    name       TEXT         NOT NULL,
    author_id  BIGINT       NOT NULL,
    created_at TIMESTAMP    NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    UNIQUE (guild_id, name)
);

CREATE TABLE IF NOT EXISTS trivia_questions
(
    id       SERIAL PRIMARY KEY,
    list_id  INT    NOT NULL REFERENCES trivia_lists (id) ON DELETE CASCADE,
    question TEXT   NOT NULL,
    answers  TEXT[] NOT NULL
);
CREATE INDEX IF NOT EXISTS trivia_questions_list_idx ON trivia_questions (list_id);

CREATE TABLE IF NOT EXISTS trivia_scores
(
    guild_id BIGINT NOT NULL,
    user_id  BIGINT NOT NULL,
    correct  INT    NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
