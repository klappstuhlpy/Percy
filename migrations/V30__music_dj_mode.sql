-- Configurable DJ access mode for the music player panel and commands.
--
-- 0 = everyone (default, current behaviour: anyone in the voice channel can interact)
-- 1 = dj_only (only DJ role holders or manage_guild permission can control playback)
-- 2 = hybrid  (everyone can use basic controls; destructive actions require DJ)

ALTER TABLE guild_config
    ADD COLUMN IF NOT EXISTS music_dj_mode SMALLINT NOT NULL DEFAULT 0;
