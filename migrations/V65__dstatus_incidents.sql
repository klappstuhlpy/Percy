-- Revises: V64
-- Reason: track one posted message per (guild, incident) so concurrent Discord incidents all keep updating

CREATE TABLE IF NOT EXISTS discord_incident_posts
(
    guild_id    BIGINT NOT NULL REFERENCES discord_incidents (guild_id) ON DELETE CASCADE,
    incident_id TEXT   NOT NULL,
    message_id  BIGINT NOT NULL,
    status      TEXT,
    PRIMARY KEY (guild_id, incident_id)
);

-- Backfill from the old one-row-per-guild tracking.
INSERT INTO discord_incident_posts (guild_id, incident_id, message_id, status)
SELECT guild_id, id, message_id, status
FROM discord_incidents
WHERE id IS NOT NULL AND message_id IS NOT NULL
ON CONFLICT DO NOTHING;
