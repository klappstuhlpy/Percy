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
