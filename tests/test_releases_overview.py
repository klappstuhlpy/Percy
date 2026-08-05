"""Tests for the overview shaping (``app/services/releases/overview.py``).

Pure, like ``tests/test_releases_subjects.py``: rows are hand-built dicts shaped like the
``comic_releases`` / ``watch_titles`` records the repositories return, subscriptions are dicts
shaped like ``release_subscriptions`` rows. No database, no FastAPI, no bot.

Two things this module does *not* do are worth naming, because a test that assumed otherwise
would be testing a fiction:

* it does **no** windowing. ``build_overview`` reports the ``start``/``end`` it is handed and
  passes every entry through; the bounds are enforced by the two queries that fetched them
  (``recent_comic_releases``: ``first_seen >= start``, no upper bound at all;
  ``titles_releasing_between``: ``BETWEEN``, inclusive at both ends). That asymmetry is the
  point -- comics are windowed on when *we* learned about a book, titles on when they are out.
* it does not decide who follows what. ``followed`` is
  :func:`~app.services.releases.match` -- the same matcher the DM dispatch uses -- with one
  documented change: ``created_at`` is blanked, so the backlog cutoff does not apply to a page.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from app.services.releases import (
    BRAND_LABELS,
    Entry,
    build_overview,
    comic_entry,
    group_rows,
    title_entry,
)

TODAY = datetime.date(2026, 8, 4)
START = TODAY - datetime.timedelta(days=7)
END = TODAY + datetime.timedelta(days=7)
SEEN = datetime.datetime(2026, 8, 3, 9, 30)


def comic_row(**kwargs: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        'id': 1,
        'brand': 'marvel',
        'slug': 'amazing-spider-man-12',
        'title': 'Amazing Spider-Man #12',
        'series_slug': 'amazing-spider-man',
        'series_name': 'The Amazing Spider-Man',
        'issue_number': '12',
        'release_date': datetime.date(2026, 8, 5),
        'first_seen': SEEN,
        'cover_url': 'https://example.invalid/cover.jpg',
        'url': 'https://example.invalid/comic',
    }
    row.update(kwargs)
    return row


def title_row(**kwargs: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        'id': 10,
        'universe': 'mcu',
        'slug': 'avengers-doomsday',
        'title': 'Avengers: Doomsday',
        'sub_universe': 'Multiverse Saga',
        'release_date': datetime.date(2026, 8, 6),
        'poster_path': '/poster.jpg',
    }
    row.update(kwargs)
    return row


UNIVERSES = {'mcu': 'Marvel Cinematic Universe'}


def subscription(**kwargs: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        'user_id': 1,
        'media': 'comic',
        'subject_type': 'series',
        'subject_value': 'amazing-spider-man',
        'streams': 'releases',
        'delivery': 'dm',
        'created_at': None,
    }
    row.update(kwargs)
    return row


# -- group_rows ---------------------------------------------------------------


def test_group_rows_buckets_by_the_owning_id() -> None:
    rows = [
        {'release_id': 1, 'name': 'Zeb Wells'},
        {'release_id': 2, 'name': 'Al Ewing'},
        {'release_id': 1, 'name': 'John Romita Jr.'},
    ]
    grouped = group_rows(rows, 'release_id')
    assert [row['name'] for row in grouped[1]] == ['Zeb Wells', 'John Romita Jr.']
    assert [row['name'] for row in grouped[2]] == ['Al Ewing']
    assert 3 not in grouped


def test_group_rows_of_nothing_is_empty() -> None:
    assert group_rows([], 'release_id') == {}


# -- comic_entry --------------------------------------------------------------


def test_comic_card_carries_the_whole_shape() -> None:
    card = comic_entry(comic_row()).card
    assert card == {
        'media': 'comic',
        'key': 'marvel/amazing-spider-man-12',
        'name': 'Amazing Spider-Man #12',
        'subtitle': 'The Amazing Spider-Man #12',
        'release_date': '2026-08-05',
        'cover_url': 'https://example.invalid/cover.jpg',
        'url': 'https://example.invalid/comic',
        'subjects': [
            {'subject_type': 'brand', 'subject_value': 'marvel', 'label': 'Marvel'},
            {'subject_type': 'series', 'subject_value': 'amazing-spider-man',
             'label': 'The Amazing Spider-Man'},
        ],
    }


def test_the_comic_key_is_the_release_key_deliveries_are_claimed_on() -> None:
    """``{brand}/{slug}`` -- the same string ``release_deliveries`` stores, so the page, the DM
    and the delivery claim all name one item identically."""
    entry = comic_entry(comic_row())
    assert entry.card['key'] == entry.item.key == 'marvel/amazing-spider-man-12'


@pytest.mark.parametrize(('brand', 'label'), [('marvel', 'Marvel'), ('dc', 'DC'), ('manga', 'Manga')])
def test_every_brand_gets_its_curated_label(brand: str, label: str) -> None:
    subjects = comic_entry(comic_row(brand=brand)).card['subjects']
    assert subjects[0] == {'subject_type': 'brand', 'subject_value': brand, 'label': label}
    assert BRAND_LABELS[brand] == label


def test_an_unknown_brand_falls_back_to_a_titlecased_label() -> None:
    subjects = comic_entry(comic_row(brand='image')).card['subjects']
    assert subjects[0] == {'subject_type': 'brand', 'subject_value': 'image', 'label': 'Image'}


def test_a_brand_subject_value_is_normalised_but_the_key_is_not() -> None:
    """The subject value is compared against stored subscriptions (so it is normalised); the
    ``key`` is an identity string and stays exactly as the row spelled it."""
    entry = comic_entry(comic_row(brand='MARVEL'))
    assert entry.card['subjects'][0]['subject_value'] == 'marvel'
    assert entry.card['key'] == 'MARVEL/amazing-spider-man-12'


def test_a_release_without_a_series_gets_no_series_subject_and_no_stray_hash() -> None:
    card = comic_entry(comic_row(series_slug=None, series_name=None, issue_number=None)).card
    assert card['subtitle'] is None
    assert [subject['subject_type'] for subject in card['subjects']] == ['brand']


def test_a_series_without_an_issue_number_keeps_the_series_subtitle() -> None:
    assert comic_entry(comic_row(issue_number=None)).card['subtitle'] == 'The Amazing Spider-Man'


def test_an_unnamed_series_labels_itself_with_its_slug() -> None:
    card = comic_entry(comic_row(series_name=None)).card
    assert card['subtitle'] == 'amazing-spider-man #12'
    assert card['subjects'][1]['label'] == 'amazing-spider-man'


def test_an_undated_comic_reports_a_null_release_date() -> None:
    assert comic_entry(comic_row(release_date=None)).card['release_date'] is None


def test_comic_creators_and_characters_match_but_are_not_card_chips() -> None:
    """Thirty chips is noise; the fan-out still sees them, which is what ``followed`` uses."""
    entry = comic_entry(comic_row(), creators=[{'name': 'Zeb Wells'}], characters=[{'name': 'Spider-Man'}])
    assert [subject['subject_type'] for subject in entry.card['subjects']] == ['brand', 'series']
    assert {subject.type for subject in entry.item.subjects} == {'brand', 'series', 'creator', 'character'}


# -- title_entry --------------------------------------------------------------


def test_title_card_carries_the_whole_shape() -> None:
    card = title_entry(title_row(), universe_names=UNIVERSES).card
    assert card == {
        'media': 'title',
        'key': 'mcu/avengers-doomsday',
        'name': 'Avengers: Doomsday',
        'subtitle': 'Marvel Cinematic Universe · Multiverse Saga',
        'release_date': '2026-08-06',
        'cover_url': '/poster.jpg',
        'url': None,
        'subjects': [
            {'subject_type': 'universe', 'subject_value': 'mcu', 'label': 'Marvel Cinematic Universe'},
            {'subject_type': 'franchise', 'subject_value': 'multiverse saga', 'label': 'Multiverse Saga'},
        ],
    }


def test_the_title_key_is_the_release_key_deliveries_are_claimed_on() -> None:
    entry = title_entry(title_row())
    assert entry.card['key'] == entry.item.key == 'mcu/avengers-doomsday'


def test_an_unmapped_universe_degrades_to_its_slug_rather_than_to_none() -> None:
    card = title_entry(title_row(), universe_names={}).card
    assert card['subtitle'] == 'mcu · Multiverse Saga'
    assert card['subjects'][0] == {'subject_type': 'universe', 'subject_value': 'mcu', 'label': 'mcu'}


@pytest.mark.parametrize('sub_universe', [None, '', '   '])
def test_a_title_without_a_franchise_gets_no_franchise_subject(sub_universe: str | None) -> None:
    card = title_entry(title_row(sub_universe=sub_universe), universe_names=UNIVERSES).card
    assert card['subtitle'] == 'Marvel Cinematic Universe'
    assert [subject['subject_type'] for subject in card['subjects']] == ['universe']


def test_a_title_cover_stays_the_relative_poster_path() -> None:
    """The renderer owns TMDB's CDN prefix, exactly as in ``app/services/watchlist/payload.py``."""
    assert title_entry(title_row()).card['cover_url'] == '/poster.jpg'


def test_a_title_carries_no_url_because_watch_titles_has_none() -> None:
    assert title_entry(title_row()).card['url'] is None


def test_title_cast_matches_but_is_not_a_card_chip() -> None:
    credits = [{'slug': 'pedro-pascal', 'name': 'Pedro Pascal', 'role': 'cast', 'character_name': 'Reed Richards'}]
    entry = title_entry(title_row(), credits, universe_names=UNIVERSES)
    assert [subject['subject_type'] for subject in entry.card['subjects']] == ['universe', 'franchise']
    assert {subject.type for subject in entry.item.subjects} == {'universe', 'franchise', 'person', 'character'}


# -- The comic/title asymmetry ------------------------------------------------


def test_a_comic_is_dated_by_first_seen_and_a_title_only_by_its_release_date() -> None:
    """Why the window is asymmetric: ``comic_releases`` records when we *learned* about a book
    (months before it ships), ``watch_titles`` has no such column at all."""
    assert comic_entry(comic_row()).item.first_seen == SEEN
    assert title_entry(title_row()).item.first_seen is None
    assert title_entry(title_row()).item.release_date == datetime.date(2026, 8, 6)


# -- build_overview -----------------------------------------------------------


def test_the_window_is_reported_as_iso_dates() -> None:
    payload = build_overview([], [], start=START, end=END)
    assert payload['window'] == {'start': '2026-07-28', 'end': '2026-08-11'}


def test_the_payload_keeps_the_two_media_apart() -> None:
    payload = build_overview([comic_entry(comic_row())], [title_entry(title_row())], start=START, end=END)
    assert [card['media'] for card in payload['comics']] == ['comic']
    assert [card['media'] for card in payload['titles']] == ['title']
    assert set(payload) == {'window', 'comics', 'titles', 'followed', 'progress'}


def test_entries_outside_the_window_are_still_returned() -> None:
    """``build_overview`` does no filtering -- the bounds are the queries' job (inclusive at both
    ends of ``titles_releasing_between``, and open-ended forward for comics). Handing it an
    out-of-range entry passes it straight through, so a window mismatch is a caller's bug."""
    payload = build_overview(
        [comic_entry(comic_row(release_date=datetime.date(2030, 1, 1)))],
        [title_entry(title_row(release_date=datetime.date(1990, 1, 1)))],
        start=START, end=END,
    )
    assert [card['release_date'] for card in payload['comics']] == ['2030-01-01']
    assert [card['release_date'] for card in payload['titles']] == ['1990-01-01']


@pytest.mark.parametrize('release_date', [START, END])
def test_a_card_on_either_boundary_day_is_kept(release_date: datetime.date) -> None:
    payload = build_overview([], [title_entry(title_row(release_date=release_date))], start=START, end=END)
    assert [card['release_date'] for card in payload['titles']] == [release_date.isoformat()]


def test_followed_is_empty_without_subscriptions() -> None:
    """A signed-out page renders the same shape as a signed-in one -- ``[]``, never ``None``."""
    payload = build_overview([comic_entry(comic_row())], [title_entry(title_row())], start=START, end=END)
    assert payload['followed'] == []


def test_followed_is_the_subset_a_users_subscriptions_match() -> None:
    comics = [comic_entry(comic_row()), comic_entry(comic_row(slug='x-men-1', title='X-Men #1',
                                                              series_slug='x-men', series_name='X-Men'))]
    titles = [title_entry(title_row(), universe_names=UNIVERSES)]
    payload = build_overview(comics, titles, start=START, end=END, subscriptions=[subscription()])

    assert [card['key'] for card in payload['followed']] == ['marvel/amazing-spider-man-12']
    assert all(card in payload['comics'] + payload['titles'] for card in payload['followed'])


def test_followed_matches_on_a_subject_the_card_does_not_show() -> None:
    """Creators and cast are matchable subjects even though they are not card chips."""
    comics = [comic_entry(comic_row(), creators=[{'name': 'Zeb Wells'}])]
    subscriptions = [subscription(subject_type='creator', subject_value=' ZEB   Wells ')]
    payload = build_overview(comics, [], start=START, end=END, subscriptions=subscriptions)
    assert [card['key'] for card in payload['followed']] == ['marvel/amazing-spider-man-12']


def test_followed_spans_both_media_in_catalogue_order() -> None:
    comics = [comic_entry(comic_row())]
    titles = [title_entry(title_row(), universe_names=UNIVERSES)]
    subscriptions = [
        subscription(),
        subscription(media='title', subject_type='universe', subject_value='mcu'),
    ]
    payload = build_overview(comics, titles, start=START, end=END, subscriptions=subscriptions)
    assert [card['key'] for card in payload['followed']] == ['marvel/amazing-spider-man-12', 'mcu/avengers-doomsday']


def test_a_muted_subscription_does_not_light_up_the_page() -> None:
    """``delivery = 'off'`` is the mute a blocked DM sets, and it means the same on both surfaces."""
    payload = build_overview([comic_entry(comic_row())], [], start=START, end=END,
                             subscriptions=[subscription(delivery='off')])
    assert payload['followed'] == []


def test_a_news_only_subscription_does_not_light_up_a_release() -> None:
    payload = build_overview([comic_entry(comic_row())], [], start=START, end=END,
                             subscriptions=[subscription(streams='news')])
    assert payload['followed'] == []


def test_the_backlog_cutoff_is_deliberately_switched_off_for_the_page() -> None:
    """Following something yesterday must not blank this week's page -- the cutoff exists to stop
    a *DM* backlog, and the matcher's ``created_at`` rule is neutralised here on purpose."""
    just_subscribed = subscription(created_at=datetime.datetime(2026, 8, 4, 12, 0))
    payload = build_overview([comic_entry(comic_row())], [], start=START, end=END,
                             subscriptions=[just_subscribed])
    assert [card['key'] for card in payload['followed']] == ['marvel/amazing-spider-man-12']


def test_one_card_matched_by_three_subscriptions_appears_once() -> None:
    entry = comic_entry(comic_row(), creators=[{'name': 'Zeb Wells'}], characters=[{'name': 'Spider-Man'}])
    subscriptions = [
        subscription(),
        subscription(subject_type='creator', subject_value='Zeb Wells'),
        subscription(subject_type='character', subject_value='Spider-Man'),
    ]
    payload = build_overview([entry], [], start=START, end=END, subscriptions=subscriptions)
    assert len(payload['followed']) == 1


def test_another_users_subscriptions_are_matched_too_because_the_caller_scopes_them() -> None:
    """The handler passes one user's rows; the matcher itself is per-``user_id`` and this module
    flattens whatever it is given, so scoping is the caller's contract."""
    payload = build_overview([comic_entry(comic_row())], [], start=START, end=END,
                             subscriptions=[subscription(user_id=999)])
    assert [card['key'] for card in payload['followed']] == ['marvel/amazing-spider-man-12']


def test_an_entry_is_its_releasable_plus_its_card() -> None:
    entry = comic_entry(comic_row())
    assert isinstance(entry, Entry)
    assert (entry.item.media, entry.item.name) == ('comic', entry.card['name'])
