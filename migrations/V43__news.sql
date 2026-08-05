-- Revises: V42
-- Creation Date: 2026-08-04 21:24:27.756679+00:00 UTC
-- Reason: news

-- The second stream. Releases tell a subscriber when a thing *ships*; news tells them what is
-- being said about it in between, and the whole point of the design is that both travel the
-- same pipe: a story is tagged with the same ``(media, subject_type, subject_value)`` keys a
-- subscription is expressed in (V42__subscriptions.sql), so ``news_subjects`` and
-- ``release_subscriptions`` meet on one indexed lookup and there is no second notification
-- system to keep honest.

-- One row per feed we poll. A source being a *row* rather than a constant is the whole
-- operational story: a publisher whose terms forbid reuse, a feed that starts spewing junk, or
-- one that simply dies is switched off with an UPDATE rather than a deploy (the plan's §9).
--
-- ``last_fetched``/``last_status`` are the visibility half: a feed that has been erroring for a
-- week is a row anyone can see, not silence. ``last_status`` is TEXT, not INTEGER, because the
-- interesting failures have no HTTP status at all -- a DNS failure, a timeout or an open
-- circuit breaker are exactly the states worth recording, and '404' and 'timeout' belong in
-- one column.
--
-- ``media`` says which catalogue's subjects a source's stories are tagged against; 'both' is
-- for the general entertainment-news outlets that cover comics and film in one feed.
CREATE TABLE IF NOT EXISTS news_sources
(
    slug         TEXT PRIMARY KEY,
    name         TEXT      NOT NULL,
    homepage     TEXT,
    feed_url     TEXT      NOT NULL,
    media        TEXT      NOT NULL DEFAULT 'both' CHECK (media IN ('comic', 'title', 'both')),
    enabled      BOOLEAN   NOT NULL DEFAULT TRUE,
    last_fetched TIMESTAMP,
    last_status  TEXT,
    created_at   TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

-- One row per story. What is stored is deliberately the whole of what the plan's §9 allows:
-- headline, a short excerpt, the image the feed itself offers for syndication, the author and
-- a link back to the original. Never the article body -- ``app/services/news/feeds.py`` caps
-- the excerpt on the way in, and nothing downstream re-fetches the page.
--
-- Two identity axes, in the order §5.1 dedupes by:
--   * ``url`` is the strong one and is UNIQUE outright -- the same story syndicated by two
--     sources under two guids is one story, and the upsert is keyed on it.
--   * ``(source_slug, external_id)`` is the fallback for a feed that rewrites its links
--     (tracking parameters, a CDN move) while keeping its guid stable. NULLs are unconstrained
--     in Postgres, which is what we want for feeds that publish no guid at all.
CREATE TABLE IF NOT EXISTS news_items
(
    id           SERIAL PRIMARY KEY,
    source_slug  TEXT      NOT NULL REFERENCES news_sources (slug) ON DELETE CASCADE,
    external_id  TEXT,
    url          TEXT      NOT NULL UNIQUE,
    headline     TEXT      NOT NULL,
    excerpt      TEXT,
    image_url    TEXT,
    author       TEXT,
    published_at TIMESTAMP,
    fetched_at   TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    UNIQUE (source_slug, external_id)
);

CREATE INDEX IF NOT EXISTS news_items_published_idx ON news_items (published_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS news_items_source_idx ON news_items (source_slug, published_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS news_items_headline_trgm_idx ON news_items USING GIN (headline gin_trgm_ops);

-- The join between a story and the things people follow, in the *same* key shape as
-- ``release_subscriptions``: ``subject_value`` is normalised (casefold + whitespace collapse)
-- by ``app/services/releases/subjects.py``'s ``normalise`` on both sides, and is a slug for
-- 'series'/'universe'/'person' and a name otherwise. Never slugified here -- a tag that does
-- not compare equal to the subscription it should match is a tag that notifies nobody.
--
-- ``rule`` and ``matched_term`` are the per-rule provenance §4.5 requires, and they exist for
-- one operational reason: a tagging rule that fires wrongly notifies the wrong people (§8), so
-- it must be possible to find every tag a rule produced and revoke *those* --
--     DELETE FROM news_subjects WHERE rule = 'alias' AND matched_term = 'storm';
-- -- without deleting the stories, which are fine. ``rule`` is the rule class ('exact',
-- 'alias'); ``matched_term`` is the surface form that actually fired, because that is what a
-- bad-tag report names. Neither is indexed: a revoke sweep is a rare admin operation and a
-- two-value column is a useless index on every insert.
CREATE TABLE IF NOT EXISTS news_subjects
(
    news_id       INTEGER   NOT NULL REFERENCES news_items (id) ON DELETE CASCADE,
    media         TEXT      NOT NULL CHECK (media IN ('comic', 'title')),
    subject_type  TEXT      NOT NULL,
    subject_value TEXT      NOT NULL,
    rule          TEXT      NOT NULL,
    matched_term  TEXT,
    tagged_at     TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    PRIMARY KEY (news_id, media, subject_type, subject_value)
);

-- The fan-out, and the reason news needs no delivery machinery of its own: this is the same
-- index shape as ``release_subscriptions_subject_idx``, so "which of this batch of stories
-- does anyone follow" is the query "which of this batch of releases does anyone follow" with a
-- different table name.
CREATE INDEX IF NOT EXISTS news_subjects_subject_idx
    ON news_subjects (media, subject_type, subject_value);
