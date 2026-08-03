-- Revises: V38
-- Creation Date: 2026-08-02 00:00:01.000000+00:00 UTC
-- Reason: watchlist_progress

-- Per-user watchlist state: which titles a user has watched or skipped
-- (watch_progress) and their viewing preferences (watch_prefs) -- region,
-- preferred path, sort mode, spoiler/credits visibility, and a weekly-hours
-- budget used to estimate completion time.
CREATE TABLE IF NOT EXISTS watch_progress
(
    user_id    BIGINT    NOT NULL,
    title_id   BIGINT    NOT NULL REFERENCES watch_titles (id) ON DELETE CASCADE,
    status     TEXT      NOT NULL CHECK (status IN ('watched', 'skipped')),
    updated_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    PRIMARY KEY (user_id, title_id)
);

CREATE TABLE IF NOT EXISTS watch_prefs
(
    user_id      BIGINT    PRIMARY KEY,
    region       CHAR(2)   NOT NULL DEFAULT 'US',
    path_slug    TEXT,
    sort_mode    TEXT      NOT NULL DEFAULT 'story' CHECK (sort_mode IN ('story', 'release')),
    show_spoiler BOOLEAN   NOT NULL DEFAULT FALSE,
    with_credits BOOLEAN   NOT NULL DEFAULT TRUE,
    hide_sub     BOOLEAN   NOT NULL DEFAULT FALSE,
    weekly_hours JSONB,
    target_date  DATE,
    updated_at   TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);
