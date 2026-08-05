"""Tests for subject derivation and the subscription fan-out (``app/services/releases/subjects.py``).

Everything here is pure, so rows are hand-built dicts shaped like the ``comic_releases`` /
``watch_titles`` records the repository returns, and subscriptions are dicts shaped like
``release_subscriptions`` rows. No database, no bot.

The ``match`` section has one test per numbered rule in that function's docstring -- those
rules are the anti-spam contract (once per item, no backlog, honour the mute), so each gets
named coverage rather than being folded into one big scenario.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from app.services.releases import Releasable, Subject, comic_releasable, match, normalise, title_releasable

NOW = datetime.datetime(2026, 8, 4, 12, 0)
YESTERDAY = NOW - datetime.timedelta(days=1)
LAST_MONTH = NOW - datetime.timedelta(days=30)


# -- normalise ------------------------------------------------------------

@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('Pedro Pascal', 'pedro pascal'),
        ('  Pedro Pascal  ', 'pedro pascal'),
        ('pedro  pascal', 'pedro pascal'),
        ('PEDRO\tPASCAL', 'pedro pascal'),
        ('Pedro\nPascal', 'pedro pascal'),
        ('', ''),
        ('   ', ''),
        ('X-Men', 'x-men'),
        ('STRASSE', 'strasse'),
    ],
)
def test_normalise(value: str, expected: str) -> None:
    assert normalise(value) == expected


def test_normalise_is_idempotent() -> None:
    """Values are normalised on write *and* on match, so a second pass must not change them."""
    once = normalise('  The  Amazing Spider-Man ')
    assert normalise(once) == once


def test_subject_of_normalises_its_value() -> None:
    assert Subject.of('title', 'person', ' Pedro  PASCAL ') == Subject('title', 'person', 'pedro pascal')


# -- comic_releasable -----------------------------------------------------

def comic_row(**kwargs: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        'brand': 'MARVEL',
        'slug': 'amazing-spider-man-12',
        'title': 'Amazing Spider-Man #12',
        'series_slug': 'amazing-spider-man',
        'release_date': datetime.date(2026, 8, 5),
        'first_seen': YESTERDAY,
        'cover_url': 'https://example.invalid/cover.jpg',
        'url': 'https://example.invalid/comic',
    }
    row.update(kwargs)
    return row


def test_comic_releasable_maps_the_shared_shape() -> None:
    item = comic_releasable(comic_row())
    assert (item.media, item.key, item.name) == ('comic', 'MARVEL/amazing-spider-man-12', 'Amazing Spider-Man #12')
    assert (item.release_date, item.first_seen) == (datetime.date(2026, 8, 5), YESTERDAY)
    assert (item.cover_url, item.url) == ('https://example.invalid/cover.jpg', 'https://example.invalid/comic')


def test_comic_releasable_derives_every_subject_type() -> None:
    item = comic_releasable(
        comic_row(),
        creators=[{'name': 'Zeb Wells', 'role': 'Writer'}, {'name': 'John Romita Jr.', 'role': 'Artist'}],
        characters=[{'name': 'Spider-Man', 'kind': 'Main'}],
    )
    assert item.subjects == frozenset({
        Subject('comic', 'brand', 'marvel'),
        Subject('comic', 'series', 'amazing-spider-man'),
        Subject('comic', 'creator', 'zeb wells'),
        Subject('comic', 'creator', 'john romita jr.'),
        Subject('comic', 'character', 'spider-man'),
    })


def test_comic_releasable_skips_a_null_series() -> None:
    """A collected edition has no issue marker and so no series slug -- it must not become one."""
    subjects = comic_releasable(comic_row(series_slug=None)).subjects
    assert all(subject.type != 'series' for subject in subjects)


def test_comic_releasable_dedupes_a_repeated_creator() -> None:
    """The same person credited twice is one subject, so they are not fanned out to twice."""
    item = comic_releasable(comic_row(), creators=[{'name': 'Zeb Wells'}, {'name': 'zeb  wells'}])
    assert sum(subject.type == 'creator' for subject in item.subjects) == 1


def test_comic_releasable_accepts_bare_name_strings() -> None:
    item = comic_releasable(comic_row(), creators=['Zeb Wells'], characters=['Venom'])
    assert Subject('comic', 'creator', 'zeb wells') in item.subjects
    assert Subject('comic', 'character', 'venom') in item.subjects


# -- title_releasable -----------------------------------------------------

def title_row(**kwargs: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        'universe': 'mcu',
        'slug': 'avengers-doomsday',
        'title': 'Avengers: Doomsday',
        'sub_universe': 'multiverse-saga',
        'release_date': datetime.date(2026, 12, 18),
        'poster_path': '/poster.jpg',
    }
    row.update(kwargs)
    return row


def test_title_releasable_maps_the_shared_shape() -> None:
    item = title_releasable(title_row())
    assert (item.media, item.key, item.name) == ('title', 'mcu/avengers-doomsday', 'Avengers: Doomsday')
    assert item.cover_url == '/poster.jpg'
    assert (item.first_seen, item.url) == (None, None), 'watch_titles carries neither column'


def test_title_releasable_derives_every_subject_type() -> None:
    credits = [
        {'slug': 'pedro-pascal', 'name': 'Pedro Pascal', 'role': 'cast', 'character_name': 'Reed Richards'},
        {'slug': 'anthony-russo', 'name': 'Anthony Russo', 'role': 'director', 'character_name': None},
    ]
    assert title_releasable(title_row(), credits).subjects == frozenset({
        Subject('title', 'universe', 'mcu'),
        Subject('title', 'franchise', 'multiverse-saga'),
        Subject('title', 'person', 'pedro-pascal'),
        Subject('title', 'person', 'anthony-russo'),
        Subject('title', 'character', 'reed richards'),
    })


def test_title_releasable_subscribes_people_by_slug_not_name() -> None:
    """The slug is the stable key ``/people/:slug`` and the autocomplete already use."""
    subjects = title_releasable(title_row(), [{'slug': 'pedro-pascal', 'name': 'Pedro Pascal', 'role': 'cast'}]).subjects
    assert Subject('title', 'person', 'pedro-pascal') in subjects
    assert Subject('title', 'person', 'pedro pascal') not in subjects


@pytest.mark.parametrize('sub_universe', [None, '', '   '])
def test_title_releasable_skips_an_unset_franchise(sub_universe: str | None) -> None:
    subjects = title_releasable(title_row(sub_universe=sub_universe)).subjects
    assert all(subject.type != 'franchise' for subject in subjects)


def test_title_releasable_ignores_character_names_on_crew_credits() -> None:
    credits = [{'slug': 'kevin-feige', 'role': 'creator', 'character_name': 'Producer'}]
    subjects = title_releasable(title_row(), credits).subjects
    assert all(subject.type != 'character' for subject in subjects)


def test_title_releasable_skips_an_unnamed_cast_credit() -> None:
    credits = [{'slug': 'extra', 'role': 'cast', 'character_name': None}]
    subjects = title_releasable(title_row(), credits).subjects
    assert all(subject.type != 'character' for subject in subjects)


# -- match ----------------------------------------------------------------

def subscription(**kwargs: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        'user_id': 1,
        'media': 'comic',
        'subject_type': 'series',
        'subject_value': 'amazing-spider-man',
        'streams': 'releases',
        'delivery': 'dm',
        'created_at': LAST_MONTH,
    }
    row.update(kwargs)
    return row


def releasable(**kwargs: Any) -> Releasable:
    return comic_releasable(comic_row(**kwargs))


def test_match_rule_1_matches_on_media_type_and_value() -> None:
    item = releasable()
    assert match([item], [subscription()]) == {1: [item]}


def test_match_rule_1_compares_values_after_normalising() -> None:
    """A name typed with odd spacing or casing is the same subscription as the stored one."""
    item = comic_releasable(comic_row(), creators=[{'name': 'Zeb Wells'}])
    assert match([item], [subscription(subject_type='creator', subject_value=' ZEB   Wells ')]) == {1: [item]}


def test_match_rule_1_ignores_a_subject_of_another_medium() -> None:
    """The subject *value* can collide across media; the media key is what keeps them apart."""
    assert match([releasable()], [subscription(media='title', subject_type='universe')]) == {}


def test_match_rule_2_delivery_off_never_matches() -> None:
    assert match([releasable()], [subscription(delivery='off')]) == {}


def test_match_rule_3_a_news_only_subscription_skips_a_release() -> None:
    assert match([releasable()], [subscription(streams='news')]) == {}


def test_match_rule_3_both_matches_either_stream() -> None:
    item = releasable()
    assert match([item], [subscription(streams='both')]) == {1: [item]}
    assert match([item], [subscription(streams='both')], stream='news') == {1: [item]}


def test_match_rule_3_the_news_stream_skips_a_releases_only_subscription() -> None:
    assert match([releasable()], [subscription()], stream='news') == {}


def test_match_rule_4_an_item_seen_before_the_subscription_is_backlog() -> None:
    """Following something today must not dump the last month of releases into a DM."""
    assert match([releasable(first_seen=LAST_MONTH)], [subscription(created_at=YESTERDAY)]) == {}


def test_match_rule_4_an_item_seen_after_the_subscription_matches() -> None:
    item = releasable(first_seen=NOW)
    assert match([item], [subscription(created_at=YESTERDAY)]) == {1: [item]}


def test_match_rule_4_falls_back_to_the_release_date() -> None:
    """``watch_titles`` has no ``first_seen``, so the cutoff uses the release date instead."""
    old = title_releasable(title_row(release_date=datetime.date(2020, 1, 1)))
    subject = subscription(media='title', subject_type='universe', subject_value='mcu', created_at=YESTERDAY)
    assert match([old], [subject]) == {}


def test_match_rule_4_keeps_an_item_with_neither_timestamp() -> None:
    """Nothing about an undated, never-seen item proves it is old, so it is not dropped."""
    item = releasable(first_seen=None, release_date=None)
    assert match([item], [subscription(created_at=NOW)]) == {1: [item]}


def test_match_rule_4_compares_an_aware_timestamp_correctly() -> None:
    """Percy stores naive UTC, but a caller handing over an aware value must not blow up."""
    aware = NOW.replace(tzinfo=datetime.UTC)
    item = releasable(first_seen=aware - datetime.timedelta(days=10))
    assert match([item], [subscription(created_at=NOW)]) == {}


def test_match_rule_5_one_item_reaches_a_user_once() -> None:
    """Three matching subscriptions are one line in one digest, not three DMs."""
    item = comic_releasable(comic_row(), creators=[{'name': 'Zeb Wells'}], characters=[{'name': 'Spider-Man'}])
    subscriptions = [
        subscription(subject_type='series', subject_value='amazing-spider-man'),
        subscription(subject_type='creator', subject_value='Zeb Wells'),
        subscription(subject_type='character', subject_value='Spider-Man'),
    ]
    assert match([item], subscriptions) == {1: [item]}


def test_match_rule_6_sorts_by_date_then_name() -> None:
    early = releasable(slug='a', title='Zed', release_date=datetime.date(2026, 8, 1))
    late_a = releasable(slug='b', title='Alpha', release_date=datetime.date(2026, 8, 9))
    late_z = releasable(slug='c', title='Omega', release_date=datetime.date(2026, 8, 9))
    undated = releasable(slug='d', title='Someday', release_date=None)

    matched = match([undated, late_z, late_a, early], [subscription()])

    assert matched[1] == [early, late_a, late_z, undated]


def test_match_separates_users() -> None:
    item = releasable()
    matched = match([item], [subscription(user_id=1), subscription(user_id=2, delivery='off')])
    assert matched == {1: [item]}


def test_match_returns_nothing_for_an_empty_batch() -> None:
    assert match([], [subscription()]) == {}
    assert match([releasable()], []) == {}
