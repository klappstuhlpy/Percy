-- Revises: V4
-- Creation Date: 2023-03-28 13:22:48.807338 UTC
-- Reason: timezones

CREATE TABLE IF NOT EXISTS user_settings (
    id BIGINT PRIMARY KEY,
    timezone TEXT
);

ALTER TABLE reminders ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC';
ALTER TABLE todo ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC';