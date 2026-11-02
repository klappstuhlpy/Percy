-- Member data: histories, notes, highlights, starboard

-- == V13__item_history_migration ==
CREATE TABLE IF NOT EXISTS item_history
(
    id         BIGSERIAL PRIMARY KEY                                       NOT NULL,
    uuid       BIGINT                                                      NOT NULL,
    item_type  TEXT                                                        NOT NULL,
    item_value TEXT                                                        NOT NULL,
    changed_at TIMESTAMP WITH TIME ZONE DEFAULT (now() AT TIME ZONE 'UTC') NOT NULL
);

DO
$$
    BEGIN
        IF NOT EXISTS (SELECT 1
                       FROM pg_constraint
                       WHERE conname = 'item_history_item_type_check') THEN
            ALTER TABLE item_history
                ADD CONSTRAINT item_history_item_type_check CHECK (
                    item_type IN ('avatar', 'nickname', 'name')
                    );
        END IF;
    END
$$;


CREATE TABLE IF NOT EXISTS avatar_history
(
    id         BIGSERIAL PRIMARY KEY                                       NOT NULL,
    uuid       BIGINT                                                      NOT NULL,
    format     TEXT                                                        NOT NULL, -- mime type
    avatar     BYTEA                                                       NOT NULL, -- image bytes
    changed_at TIMESTAMP WITH TIME ZONE DEFAULT (now() AT TIME ZONE 'UTC') NOT NULL
);


CREATE
    OR REPLACE FUNCTION insert_avatar_history_item(
    p_user_id bigint, p_format text, p_avatar bytea
) RETURNS void AS
$$
BEGIN
    IF NOT EXISTS (WITH last_avatar AS (SELECT avatar
                                        FROM avatar_history
                                        WHERE uuid = p_user_id
                                        ORDER BY changed_at DESC
                                        LIMIT 1)
                   SELECT 1
                   FROM last_avatar
                   WHERE avatar = p_avatar) THEN
        INSERT INTO avatar_history (uuid, format, avatar)
        VALUES (p_user_id, p_format, p_avatar);
    END IF;
END;
$$ LANGUAGE plpgsql;


CREATE
    OR REPLACE FUNCTION limit_avatar_history() RETURNS TRIGGER AS
$$
BEGIN
    DELETE
    FROM avatar_history
    WHERE uuid = NEW.uuid
      AND changed_at < (SELECT changed_at
                        FROM avatar_history
                        WHERE uuid = NEW.uuid
                        ORDER BY changed_at DESC
                        OFFSET 12 LIMIT 1);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO
$$
    BEGIN
        IF NOT EXISTS (SELECT 1
                       FROM pg_trigger
                       WHERE tgname = 'limit_avatar_history') THEN CREATE TRIGGER limit_avatar_history
            AFTER
                INSERT
            ON avatar_history
            FOR EACH ROW
        EXECUTE PROCEDURE limit_avatar_history();
        END IF;
    END;
$$;


CREATE TABLE IF NOT EXISTS presence_history
(
    id            BIGSERIAL PRIMARY KEY                                       NOT NULL,
    uuid          BIGINT                                                      NOT NULL,
    status        TEXT                                                        NOT NULL,
    status_before TEXT                                                        NOT NULL,
    changed_at    TIMESTAMP WITH TIME ZONE DEFAULT (now() AT TIME ZONE 'UTC') NOT NULL
);


CREATE
    OR REPLACE FUNCTION limit_presence_history() RETURNS TRIGGER AS
$$
BEGIN
    DELETE
    FROM presence_history
    WHERE uuid = NEW.uuid
      AND changed_at < (SELECT changed_at
                        FROM presence_history
                        WHERE uuid = NEW.uuid
                        ORDER BY changed_at DESC
                        OFFSET 1 LIMIT 1);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- == V14__user_notes_migration ==
CREATE TABLE IF NOT EXISTS user_notes
(
    id         SERIAL PRIMARY KEY,
    topic      TEXT,
    content    TEXT      NOT NULL,
    owner_id   BIGINT    NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc')
);

-- == V15__highlight_migration ==
CREATE TABLE IF NOT EXISTS highlights
(
    id          SERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    location_id BIGINT NOT NULL,
    blocked     BIGINT[],
    lookup      TEXT[]
);

-- == V16__starboard ==
CREATE TABLE IF NOT EXISTS starboard_config
(
    guild_id            BIGINT  PRIMARY KEY,
    channel_id          BIGINT,
    threshold           INTEGER NOT NULL DEFAULT 3,
    emoji               TEXT    NOT NULL DEFAULT '⭐',
    self_star           BOOLEAN NOT NULL DEFAULT FALSE,
    enabled             BOOLEAN NOT NULL DEFAULT TRUE,
    ignored_channel_ids BIGINT[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS starboard_entries
(
    message_id           BIGINT    PRIMARY KEY,
    guild_id             BIGINT    NOT NULL,
    channel_id           BIGINT    NOT NULL,
    author_id            BIGINT    NOT NULL,
    starboard_message_id BIGINT,
    star_count           INTEGER   NOT NULL DEFAULT 0,
    created_at           TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc')
);

CREATE INDEX IF NOT EXISTS starboard_entries_guild_id_idx ON starboard_entries (guild_id);
CREATE INDEX IF NOT EXISTS starboard_entries_starboard_message_id_idx ON starboard_entries (starboard_message_id);

-- == V28__starboard_gating ==
-- Gating controls for the starboard:
--   max_age_hours: ignore messages older than this many hours (0 = no limit)
--   allow_nsfw:    whether messages from NSFW channels may reach a non-NSFW starboard
ALTER TABLE starboard_config
    ADD COLUMN IF NOT EXISTS max_age_hours INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS allow_nsfw    BOOLEAN NOT NULL DEFAULT FALSE;


-- == V33__hot_path_indexes ==
-- Indexes for queries that the slow-query tracker flagged as full table scans.

-- highlights: the on_message listener runs `WHERE location_id = $1` for every
-- message in a guild (highlight.get_guild_highlights), and the user-data export /
-- import-locations paths filter on user_id. The table previously had only a PK on
-- id, so both were sequential scans on every hit.
CREATE INDEX IF NOT EXISTS highlights_location_id_idx ON highlights (location_id);
CREATE INDEX IF NOT EXISTS highlights_user_id_idx ON highlights (user_id);

-- polls: the reconciliation loop scans `WHERE expires < now()` to recover polls
-- whose end timer was missed. No index existed on expires, so it scanned the whole
-- table each tick.
CREATE INDEX IF NOT EXISTS polls_expires_idx ON polls (expires);
