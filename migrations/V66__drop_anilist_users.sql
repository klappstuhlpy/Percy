-- Revises: V65
-- Reason: AniList account linking was removed; the stored OAuth tokens have no reader and should not be kept

DROP TABLE IF EXISTS anilist_users;
