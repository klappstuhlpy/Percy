"""Tests for the help-menu category partitioning (:func:`app.core.help.partition_categories`)."""

from __future__ import annotations

import itertools

from app.core.help import MAX_HELP_SELECTS, MAX_SELECT_OPTIONS, partition_categories


def test_empty_has_no_selects() -> None:
    assert partition_categories(0, with_index=True) == []


def test_fits_in_one_select() -> None:
    assert partition_categories(10, with_index=True) == [(0, 10)]


def test_first_select_reserves_room_for_index_option() -> None:
    # 24 categories + the Start Page option exactly fill one select.
    assert partition_categories(24, with_index=True) == [(0, 24)]
    # Without the index option, a full 25 fit in one select.
    assert partition_categories(25, with_index=False) == [(0, 25)]


def test_overflow_spills_into_a_second_select() -> None:
    # 29 categories: 24 in the first select (index takes a slot), 5 in the second.
    assert partition_categories(29, with_index=True) == [(0, 24), (24, 29)]


def test_boundary_at_25_with_index() -> None:
    assert partition_categories(25, with_index=True) == [(0, 24), (24, 25)]


def test_slices_are_contiguous_and_cover_everything_within_capacity() -> None:
    ranges = partition_categories(60, with_index=True)
    # Contiguous, non-overlapping.
    for (_, prev_end), (next_start, _) in itertools.pairwise(ranges):
        assert prev_end == next_start
    assert ranges[0][0] == 0
    # Within the select cap, everything is covered.
    assert ranges[-1][1] == 60


def test_caps_at_max_selects_and_drops_overflow() -> None:
    ranges = partition_categories(500, with_index=True)
    assert len(ranges) == MAX_HELP_SELECTS
    capacity = (MAX_SELECT_OPTIONS - 1) + (MAX_HELP_SELECTS - 1) * MAX_SELECT_OPTIONS
    assert ranges[-1][1] == capacity
