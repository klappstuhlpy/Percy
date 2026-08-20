"""Tests for digest grouping (``app/services/releases/digest.py``).

Pure, so releasables are built directly rather than derived from rows -- the shaping of a row
into a releasable is :mod:`tests.test_releases_subjects`' job. What matters here is the
grouping order, the cap, and the "and N more" tail.
"""

from __future__ import annotations

import datetime

from app.services.releases import Releasable, build_digest

DATE = datetime.date(2026, 8, 5)


def item(name: str, *, media: str = 'comic', date: datetime.date | None = DATE) -> Releasable:
    return Releasable(
        media=media,
        key=f'{media}/{name.lower()}',
        name=name,
        release_date=date,
        first_seen=None,
        cover_url=None,
        url=None,
        subjects=frozenset(),
    )


def test_empty_input_gives_a_digest_with_no_groups() -> None:
    """Callers check ``groups`` and skip the DM -- "nothing happened" is not an error."""
    digest = build_digest(7, [])
    assert (digest.user_id, digest.groups, digest.shown, digest.overflow) == (7, [], 0, 0)


def test_groups_by_media_then_date() -> None:
    items = [
        item('Comic Old', date=DATE - datetime.timedelta(days=1)),
        item('Film', media='title'),
        item('Comic New'),
    ]
    digest = build_digest(1, items)

    assert [(group.media, group.date) for group in digest.groups] == [
        ('comic', DATE),
        ('comic', DATE - datetime.timedelta(days=1)),
        ('title', DATE),
    ]
    assert [entry.name for entry in digest.groups[0].items] == ['Comic New']


def test_dates_run_newest_first_with_undated_items_leading() -> None:
    """An announced-but-undated title is news, not backlog, so it opens its medium."""
    items = [item('Dated'), item('Someday', date=None)]
    digest = build_digest(1, items)
    assert [group.date for group in digest.groups] == [None, DATE]


def test_items_inside_a_group_are_sorted_by_name() -> None:
    digest = build_digest(1, [item('Zed'), item('Alpha')])
    assert [entry.name for entry in digest.groups[0].items] == ['Alpha', 'Zed']


def test_cap_limits_shown_and_counts_the_tail() -> None:
    items = [item(f'Issue {index:02d}') for index in range(20)]
    digest = build_digest(1, items, limit=12)

    assert (digest.shown, digest.overflow) == (12, 8)
    assert sum(len(group.items) for group in digest.groups) == 12
    assert [entry.name for entry in digest.groups[0].items][-1] == 'Issue 11'


def test_a_digest_under_the_cap_has_no_overflow() -> None:
    digest = build_digest(1, [item('Only One')], limit=12)
    assert (digest.shown, digest.overflow) == (1, 0)


def test_the_cap_applies_across_media_not_per_group() -> None:
    """The limit is a DM's length budget, so it is spent on the flat list, not per heading."""
    items = [item('A'), item('B'), item('C', media='title')]
    digest = build_digest(1, items, limit=2)

    assert (digest.shown, digest.overflow) == (2, 1)
    assert [group.media for group in digest.groups] == ['comic']


def test_a_zero_limit_shows_nothing_and_overflows_everything() -> None:
    digest = build_digest(1, [item('A'), item('B')], limit=0)
    assert (digest.groups, digest.shown, digest.overflow) == ([], 0, 2)
