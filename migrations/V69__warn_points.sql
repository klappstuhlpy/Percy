-- Point-weighted warnings: a warn carries points (every older warn is worth 1), and a guild
-- can define named reason presets worth N points. Thresholds compare the active point total.
ALTER TABLE mod_cases ADD COLUMN IF NOT EXISTS points INTEGER NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS warn_reasons
(
    guild_id    BIGINT  NOT NULL,
    name        TEXT    NOT NULL,
    points      INTEGER NOT NULL CHECK (points BETWEEN 1 AND 100),
    description TEXT,
    PRIMARY KEY (guild_id, name)
);
