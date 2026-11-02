-- Member restrictions beyond mutes: voice bans, frozen nicknames and per-channel mutes.
-- One row per (member, kind, channel); channel_id is 0 when the kind is not channel-scoped.
-- ``value`` holds the frozen nickname; ``expires_at`` (naive UTC) the optional end.
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS voiceban_role_id BIGINT;

CREATE TABLE IF NOT EXISTS member_restrictions
(
    guild_id   BIGINT      NOT NULL,
    user_id    BIGINT      NOT NULL,
    kind       TEXT        NOT NULL CHECK (kind IN ('voiceban', 'nickfreeze', 'channelmute')),
    channel_id BIGINT      NOT NULL DEFAULT 0,
    value      TEXT,
    expires_at TIMESTAMP,
    PRIMARY KEY (guild_id, user_id, kind, channel_id)
);
CREATE INDEX IF NOT EXISTS member_restrictions_user_idx ON member_restrictions (guild_id, user_id);
