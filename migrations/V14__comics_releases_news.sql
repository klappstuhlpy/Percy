-- Comic catalogue, release subscriptions/progress, news stream

-- == V40__comic_catalogue ==
-- The comic archive. Until now the comic cog held every issue in a memory-only cache
-- (refreshed every 6 h) and persisted nothing but the per-guild feed *schedule*
-- (V5__comic_config), so a restart lost the catalogue and the website had nothing to read.
-- These tables are the archive: the cache stays (the Discord embeds render from it), the
-- database becomes what survives.
--
-- ``first_seen`` is what makes "new this week" honest across a restart -- it is set once, on
-- the first upsert of a release, and never touched again.
CREATE TABLE IF NOT EXISTS comic_releases
(
    id           SERIAL PRIMARY KEY,
    brand        TEXT           NOT NULL,
    external_id  TEXT           NOT NULL,
    slug         TEXT           NOT NULL,
    title        TEXT           NOT NULL,
    series_slug  TEXT,
    series_name  TEXT,
    issue_number TEXT,
    release_date DATE,
    cover_url    TEXT,
    description  TEXT,
    price        NUMERIC(10, 2),
    page_count   INTEGER,
    format       TEXT,
    publisher    TEXT,
    url          TEXT,
    first_seen   TIMESTAMP      NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    updated_at   TIMESTAMP      NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    UNIQUE (brand, external_id),
    UNIQUE (brand, slug)
);

CREATE INDEX IF NOT EXISTS comic_releases_brand_date_idx ON comic_releases (brand, release_date DESC);
CREATE INDEX IF NOT EXISTS comic_releases_series_idx ON comic_releases (series_slug);
CREATE INDEX IF NOT EXISTS comic_releases_first_seen_idx ON comic_releases (first_seen DESC);
CREATE INDEX IF NOT EXISTS comic_releases_title_trgm_idx ON comic_releases USING GIN (title gin_trgm_ops);

-- Credits, in the ``{role: [names]}`` shape locg-api gives us, flattened one row per pair.
-- A creator is a subject a user can subscribe to (see the releases plan), hence the
-- lower(name) index: the fan-out lookup is by name, case-insensitively.
CREATE TABLE IF NOT EXISTS comic_creators
(
    release_id INTEGER NOT NULL REFERENCES comic_releases (id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    role       TEXT    NOT NULL,
    PRIMARY KEY (release_id, name, role)
);

CREATE INDEX IF NOT EXISTS comic_creators_name_idx ON comic_creators (lower(name));

-- ``kind`` is locg-api's character ``type`` (Main, Supporting, Villain, ...) and is display
-- data only -- subscriptions match on the name.
CREATE TABLE IF NOT EXISTS comic_characters
(
    release_id INTEGER NOT NULL REFERENCES comic_releases (id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    kind       TEXT,
    PRIMARY KEY (release_id, name)
);

CREATE INDEX IF NOT EXISTS comic_characters_name_idx ON comic_characters (lower(name));

-- == V42__subscriptions ==
-- Personal release subscriptions. Until now the only "comics subscriber" in Percy was a
-- *guild channel* feed (V5__comic_config); this is the per-user standing interest, and it is
-- one table for both media because the subject is a plain string key, not a foreign key --
-- no polymorphic references, and film/TV and comics fan out through the same query.
--
-- ``subject_value`` is stored NORMALISED (casefolded, trimmed, inner whitespace collapsed) by
-- ``app/services/releases/subjects.py``'s ``normalise``, which is also what the matcher runs
-- over a release's derived subjects. That single rule is why "Pedro Pascal" and
-- "pedro  pascal" are one subscription rather than two. ``subject_label`` keeps the display
-- form, so a list command can show the name the user typed.
--
-- ``subject_type`` is deliberately NOT constrained by a CHECK: a cross-column constraint
-- ("this type is only legal for that media") ages badly, and the closed sets belong with the
-- code that derives them. They are, per media:
--     comic -> brand | series | creator | character
--     title -> universe | franchise | person | character
-- ``series`` is a series slug, ``person`` a ``watch_people.slug``; the rest are names.
--
-- ``streams`` is the volume dial. News (V43) is far higher-frequency than releases, so a user
-- who wants only the release ping must be able to say so at subscribe time rather than by
-- unsubscribing in annoyance. ``delivery = 'off'`` mutes a subscription without losing it --
-- it is what a blocked-DM failure sets, per the plan's D8.
--
-- ``guild_id`` records where the subscription was created (nullable: a DM-created one has no
-- guild). It is context for a later per-guild view, never a scope on delivery.
CREATE TABLE IF NOT EXISTS release_subscriptions
(
    user_id       BIGINT    NOT NULL,
    media         TEXT      NOT NULL CHECK (media IN ('comic', 'title')),
    subject_type  TEXT      NOT NULL,
    subject_value TEXT      NOT NULL,
    subject_label TEXT,
    streams       TEXT      NOT NULL DEFAULT 'releases' CHECK (streams IN ('releases', 'news', 'both')),
    delivery      TEXT      NOT NULL DEFAULT 'dm' CHECK (delivery IN ('dm', 'off')),
    guild_id      BIGINT,
    created_at    TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    PRIMARY KEY (user_id, media, subject_type, subject_value)
);

-- The fan-out: given a batch of releases' subject keys, who subscribed to any of them. The
-- primary key leads with ``user_id`` and cannot serve that lookup, hence a second index.
CREATE INDEX IF NOT EXISTS release_subscriptions_subject_idx
    ON release_subscriptions (media, subject_type, subject_value);

-- The anti-spam guarantee: a user hears about an item **once**, no matter how many of their
-- subscriptions matched it. ``release_key`` is namespaced by the ``media`` column rather than
-- inside the string -- comics use ``{brand}/{slug}``, titles use ``{universe}/{slug}`` -- so a
-- comic issue and a film can never collide on one key.
CREATE TABLE IF NOT EXISTS release_deliveries
(
    user_id      BIGINT    NOT NULL,
    media        TEXT      NOT NULL,
    release_key  TEXT      NOT NULL,
    delivered_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    PRIMARY KEY (user_id, media, release_key)
);

-- Housekeeping: the idempotency table only has to remember far enough back that a re-dispatch
-- cannot double-notify, so old rows are pruned by age.
CREATE INDEX IF NOT EXISTS release_deliveries_delivered_at_idx ON release_deliveries (delivered_at);

-- Read/watched/skipped state across both media. It lands now so the subscription tables ship
-- as one migration, but nothing *uses* it until the progress phase (R8); V39's
-- ``watch_progress`` keeps serving film/TV in the meantime and is deliberately not dropped --
-- merging them is a later migration once this shape is proven, not a hunch acted on now.
CREATE TABLE IF NOT EXISTS release_progress
(
    user_id     BIGINT    NOT NULL,
    media       TEXT      NOT NULL,
    release_key TEXT      NOT NULL,
    status      TEXT      NOT NULL CHECK (status IN ('read', 'watched', 'skipped')),
    updated_at  TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    PRIMARY KEY (user_id, media, release_key)
);

-- == V43__news ==
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

-- == V67__anime_subscriptions ==
ALTER TABLE release_subscriptions DROP CONSTRAINT IF EXISTS release_subscriptions_media_check;
ALTER TABLE release_subscriptions
    ADD CONSTRAINT release_subscriptions_media_check CHECK (media IN ('comic', 'title', 'anime'));
