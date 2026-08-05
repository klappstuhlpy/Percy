-- Revises: V41
-- Creation Date: 2026-08-04 07:56:28.299379+00:00 UTC
-- Reason: subscriptions

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
