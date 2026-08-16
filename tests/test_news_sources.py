"""Tests for the built-in news source list (``app/services/news/sources.py``).

Pure: no repository, no network. What is worth proving is the shape ``seed_sources`` and the
``news_sources`` schema both depend on -- a duplicate slug would silently drop a source at
insert time, a duplicate feed URL would poll and store the same publisher twice under two
identities, and a ``media`` value outside the schema's ``CHECK`` (``migrations/V43__news.sql``)
would fail the insert at runtime rather than at review time.
"""

from __future__ import annotations

from app.services.news import DEFAULT_SOURCES

VALID_MEDIA = {'comic', 'title', 'both'}


def test_slugs_are_unique() -> None:
    slugs = [source.slug for source in DEFAULT_SOURCES]
    assert len(slugs) == len(set(slugs))


def test_feed_urls_are_unique() -> None:
    urls = [source.feed_url for source in DEFAULT_SOURCES]
    assert len(urls) == len(set(urls))


def test_media_values_are_valid() -> None:
    for source in DEFAULT_SOURCES:
        assert source.media in VALID_MEDIA, f'{source.slug!r} has an invalid media {source.media!r}'


def test_every_source_has_a_slug_name_homepage_and_feed_url() -> None:
    """A blank field would seed a row the browse/attribution surfaces can't render."""
    for source in DEFAULT_SOURCES:
        assert source.slug.strip()
        assert source.name.strip()
        assert source.homepage.startswith('http')
        assert source.feed_url.startswith('http')


def test_roughly_a_dozen_sources_balanced_across_media() -> None:
    assert 10 <= len(DEFAULT_SOURCES) <= 14
    counts = {media: sum(1 for s in DEFAULT_SOURCES if s.media == media) for media in VALID_MEDIA}
    assert all(count >= 2 for count in counts.values()), counts
