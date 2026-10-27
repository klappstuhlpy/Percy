-- Case expiry / pardon, plus per-guild warn expiry and escalation thresholds.
ALTER TABLE mod_cases ADD COLUMN IF NOT EXISTS expires_at  TIMESTAMP;
ALTER TABLE mod_cases ADD COLUMN IF NOT EXISTS pardoned_at TIMESTAMP;
ALTER TABLE mod_cases ADD COLUMN IF NOT EXISTS pardoned_by BIGINT;

CREATE TABLE IF NOT EXISTS warn_policy
(
    guild_id    BIGINT  PRIMARY KEY,
    expire_days INTEGER CHECK (expire_days IS NULL OR expire_days > 0)
);

CREATE TABLE IF NOT EXISTS warn_thresholds
(
    guild_id         BIGINT  NOT NULL,
    warn_count       INTEGER NOT NULL CHECK (warn_count > 0),
    action           TEXT    NOT NULL,
    duration_seconds INTEGER,
    PRIMARY KEY (guild_id, warn_count)
);
