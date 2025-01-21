-- Revises: V12
-- Creation Date: 2024-03-03 12:15:07.587204+00:00 UTC
-- Reason: item_history_migration

CREATE TABLE IF NOT EXISTS item_history
(
    id         BIGSERIAL PRIMARY KEY                                       NOT NULL,
    uuid       BIGINT                                                      NOT NULL,
    item_type  TEXT                                                        NOT NULL,
    item_value TEXT                                                        NOT NULL,
    changed_at TIMESTAMP WITH TIME ZONE DEFAULT (now() AT TIME ZONE 'UTC') NOT NULL
);

ALTER TABLE item_history
    ADD CONSTRAINT item_history_item_type_check CHECK (
        item_type IN (
                      'avatar', 'nickname', 'name'
            )
        );


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
