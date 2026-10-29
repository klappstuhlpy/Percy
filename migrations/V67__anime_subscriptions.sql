-- Revises: V66
-- Reason: anime episodes (read live from AniList) become a third subscribable medium beside comics and film/TV

ALTER TABLE release_subscriptions DROP CONSTRAINT IF EXISTS release_subscriptions_media_check;
ALTER TABLE release_subscriptions
    ADD CONSTRAINT release_subscriptions_media_check CHECK (media IN ('comic', 'title', 'anime'));
