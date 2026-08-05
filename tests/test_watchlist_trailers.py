"""Tests for :mod:`app.services.watchlist.trailers` -- TMDB ``videos`` -> one stored trailer.

Its own module rather than an addition to ``test_watchlist_people.py``: that file is about the
credits payload, and the whole substance here is a *precedence*, one rule per test, plus the
shaping half (:func:`~app.services.watchlist.payload.trailer_payload`) that turns the stored
columns back into URLs. Pure functions, hand-built payloads, no client and no pool.
"""

from __future__ import annotations

from typing import Any

from app.services.watchlist.payload import title_payload, trailer_payload
from app.services.watchlist.trailers import select_trailer, trailer_urls


def _video(**overrides: Any) -> dict[str, Any]:
    """A TMDB ``videos`` result entry: the best possible candidate, before overrides."""
    entry: dict[str, Any] = {
        'id': 'abc',
        'name': 'Official Trailer',
        'key': 'k-best',
        'site': 'YouTube',
        'type': 'Trailer',
        'official': True,
        'iso_639_1': 'en',
        'published_at': '2019-12-16T14:00:15.000Z',
    }
    entry.update(overrides)
    return entry


def _pick(*entries: dict[str, Any]) -> str | None:
    """The ``trailer_key`` :func:`select_trailer` chooses from these entries, or ``None``."""
    chosen = select_trailer({'results': list(entries)})
    return chosen['trailer_key'] if chosen else None


# -- Precedence, one rule per test --------------------------------------------


def test_trailer_beats_teaser() -> None:
    assert _pick(_video(key='teaser', type='Teaser'), _video(key='trailer')) == 'trailer'


def test_official_beats_unofficial() -> None:
    assert _pick(_video(key='repost', official=False), _video(key='studio')) == 'studio'


def test_youtube_beats_another_supported_site() -> None:
    assert _pick(_video(key='v', site='Vimeo'), _video(key='yt')) == 'yt'


def test_english_or_unset_language_beats_another() -> None:
    assert _pick(_video(key='fr', iso_639_1='fr'), _video(key='en')) == 'en'
    assert _pick(_video(key='de', iso_639_1='de'), _video(key='none', iso_639_1=None)) == 'none'


def test_newest_publication_wins_among_equals() -> None:
    assert _pick(
        _video(key='old', published_at='2018-01-01T00:00:00.000Z'),
        _video(key='new', published_at='2019-06-01T00:00:00.000Z'),
    ) == 'new'


def test_language_outranks_recency() -> None:
    # A dubbed teaser released later must not displace the original English trailer.
    assert _pick(
        _video(key='en', published_at='2018-01-01T00:00:00.000Z'),
        _video(key='fr', iso_639_1='fr', published_at='2020-01-01T00:00:00.000Z'),
    ) == 'en'


def test_a_full_tie_keeps_the_payloads_order() -> None:
    assert _pick(_video(key='first'), _video(key='second')) == 'first'


def test_the_precedence_is_applied_in_order() -> None:
    # Each entry is better than the winner on exactly one *lower* rule and worse on a higher
    # one, so only the stated ordering picks 'winner'.
    assert _pick(
        _video(key='newer-teaser', type='Teaser', published_at='2025-01-01T00:00:00.000Z'),
        _video(key='newer-unofficial', official=False, published_at='2024-01-01T00:00:00.000Z'),
        _video(key='newer-vimeo', site='Vimeo', published_at='2023-01-01T00:00:00.000Z'),
        _video(key='winner'),
    ) == 'winner'


# -- Rejections and degradation ------------------------------------------------


def test_an_empty_payload_selects_nothing() -> None:
    assert select_trailer(None) is None
    assert select_trailer({}) is None
    assert select_trailer({'results': []}) is None


def test_non_trailer_types_are_rejected_outright() -> None:
    assert select_trailer({'results': [_video(type='Featurette'), _video(type='Clip')]}) is None


def test_an_unsupported_site_is_rejected_rather_than_stored() -> None:
    # Only non-YouTube *unrenderable* videos: nothing we could link or embed, so no trailer.
    assert select_trailer({'results': [_video(site='Dailymotion'), _video(site='Facebook')]}) is None


def test_a_supported_non_youtube_site_is_still_a_trailer() -> None:
    assert select_trailer({'results': [_video(key='v', site='Vimeo')]}) == {
        'trailer_site': 'Vimeo', 'trailer_key': 'v', 'trailer_name': 'Official Trailer',
    }


def test_malformed_entries_are_skipped_without_raising() -> None:
    assert _pick(
        None,  # type: ignore[arg-type]
        'not a dict',  # type: ignore[arg-type]
        _video(key=''),
        _video(key=None),
        _video(site=None),
        _video(type=None),
        _video(published_at=12345),  # not a string: sorts oldest instead of blowing up
        _video(key='survivor'),
    ) == 'survivor'


def test_a_missing_name_stores_none_rather_than_an_empty_string() -> None:
    assert select_trailer({'results': [_video(name='  ')]})['trailer_name'] is None


# -- URLs and payload shaping --------------------------------------------------


def test_trailer_urls_are_built_per_site_and_carry_no_playback_params() -> None:
    assert trailer_urls('YouTube', 'k') == (
        'https://www.youtube.com/watch?v=k', 'https://www.youtube.com/embed/k')
    assert trailer_urls('Vimeo', '42') == ('https://vimeo.com/42', 'https://player.vimeo.com/video/42')
    assert trailer_urls('Dailymotion', 'k') is None
    assert trailer_urls('YouTube', '') is None


def test_trailer_payload_is_absent_rather_than_empty() -> None:
    assert trailer_payload({}) is None
    assert trailer_payload({'trailer_site': 'YouTube', 'trailer_key': None}) is None
    assert trailer_payload({'trailer_site': 'Dailymotion', 'trailer_key': 'k'}) is None


def test_the_title_payload_carries_the_trailer() -> None:
    row = {
        'id': 1, 'universe': 'mcu', 'tmdb_type': 'movie', 'tmdb_id': 1726, 'slug': 'iron-man',
        'title': 'Iron Man', 'kind': 'movie', 'runtime': 126, 'episodes': None,
        'release_date': None, 'poster_path': None, 'backdrop_path': None, 'overview': None,
        'tmdb_rating': None, 'tmdb_votes': None, 'era': None, 'era_order': 0, 'story_order': 0,
        'release_order': 0, 'milestone': False, 'instruction': None, 'context': None,
        'spoiler': None, 'credits_of': None, 'sub_universe': None,
        'trailer_site': 'YouTube', 'trailer_key': 'k-best', 'trailer_name': 'Official Trailer',
    }
    assert title_payload(row)['trailer'] == {
        'site': 'YouTube',
        'key': 'k-best',
        'name': 'Official Trailer',
        'url': 'https://www.youtube.com/watch?v=k-best',
        'embed_url': 'https://www.youtube.com/embed/k-best',
    }
    # A row from before V44 (or a title with no trailer) shapes to a null key, never a KeyError.
    assert title_payload({k: v for k, v in row.items() if not k.startswith('trailer_')})['trailer'] is None
