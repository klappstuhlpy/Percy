-- Custom playlist covers uploaded in the music app. Percy stores only the cover's URL (its
-- playlist.thumbnail); the image itself lives here, already cropped and re-encoded by the
-- browser, and is served only to the playlist's owner.
CREATE TABLE IF NOT EXISTS music_covers (
    id           TEXT    PRIMARY KEY,        -- random hex; part of the URL, so a new upload is a new URL
    user_id      TEXT    NOT NULL,           -- Discord id of the owner (the only viewer)
    playlist_id  TEXT    NOT NULL,
    content_type TEXT    NOT NULL,
    data         BLOB    NOT NULL,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS music_covers_playlist_idx ON music_covers (user_id, playlist_id);
