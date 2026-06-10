-- Persistent storage for AniList OAuth tokens, replacing the in-memory cache.

CREATE TABLE IF NOT EXISTS anilist_users (
    user_id    BIGINT PRIMARY KEY,
    access_token TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
