"""Tests for :mod:`app.services.watchlist.people` -- TMDB ``credits`` -> row mapping.

Pure function, hand-built payloads, no client and no pool: the whole role-normalisation and
cast-cap policy is decided here, so it is asserted here rather than through the ingest.
"""

from __future__ import annotations

from typing import Any

from app.services.watchlist.people import CAST_LIMIT, credit_rows


def _cast(person_id: int, name: str, *, order: int, character: str = 'Someone') -> dict[str, Any]:
    return {'id': person_id, 'name': name, 'order': order, 'character': character, 'profile_path': '/p.jpg'}


def _crew(person_id: int, name: str, job: str) -> dict[str, Any]:
    return {'id': person_id, 'name': name, 'job': job, 'profile_path': None}


def test_cast_is_mapped_with_billing_and_character() -> None:
    rows = credit_rows({'cast': [_cast(1, 'Robert Downey Jr.', order=0, character='Tony Stark')]})
    assert rows == [{
        'tmdb_person_id': 1,
        'name': 'Robert Downey Jr.',
        'slug': 'robert-downey-jr',
        'profile_path': '/p.jpg',
        'role': 'cast',
        'character_name': 'Tony Stark',
        'billing_order': 0,
    }]


def test_cast_is_sorted_by_billing_and_capped() -> None:
    # Deliberately handed in reverse billing order, plus one entry over the cap.
    cast = [_cast(i, f'Actor {i}', order=CAST_LIMIT - i) for i in range(CAST_LIMIT + 1)]
    rows = credit_rows({'cast': cast})
    assert len(rows) == CAST_LIMIT
    assert [row['billing_order'] for row in rows] == list(range(CAST_LIMIT))
    # The dropped one is the worst-billed, not an arbitrary tail of the input list.
    assert 0 not in {row['tmdb_person_id'] for row in rows}


def test_cast_without_order_sorts_last_and_stores_no_billing() -> None:
    rows = credit_rows({'cast': [{'id': 9, 'name': 'Uncredited'}, _cast(1, 'Billed', order=3)]})
    assert [row['tmdb_person_id'] for row in rows] == [1, 9]
    assert rows[1]['billing_order'] is None


def test_only_known_crew_jobs_become_credits() -> None:
    payload = {'crew': [
        _crew(10, 'Jon Favreau', 'Director'),
        _crew(11, 'Mark Fergus', 'Screenplay'),
        _crew(12, 'Some Gaffer', 'Gaffer'),
    ]}
    assert [(row['tmdb_person_id'], row['role']) for row in credit_rows(payload)] == [
        (10, 'director'), (11, 'writer'),
    ]


def test_one_person_can_hold_two_roles_but_not_the_same_one_twice() -> None:
    payload = {'crew': [
        _crew(10, 'Taika Waititi', 'Director'),
        _crew(10, 'Taika Waititi', 'Writer'),
        _crew(10, 'Taika Waititi', 'Screenplay'),  # same normalised role -> dropped
    ]}
    assert [row['role'] for row in credit_rows(payload)] == ['director', 'writer']


def test_created_by_becomes_the_creator_role() -> None:
    rows = credit_rows({}, created_by=[{'id': 20, 'name': 'Jac Schaeffer', 'profile_path': None}])
    assert rows == [{
        'tmdb_person_id': 20, 'name': 'Jac Schaeffer', 'slug': 'jac-schaeffer',
        'profile_path': None, 'role': 'creator', 'character_name': None, 'billing_order': None,
    }]


def test_unusable_entries_are_dropped_rather_than_stored_blank() -> None:
    payload = {'cast': [
        {'id': None, 'name': 'No id', 'order': 0},
        {'id': 5, 'name': '   ', 'order': 1},
        _cast(6, 'Fine', order=2),
    ]}
    assert [row['tmdb_person_id'] for row in credit_rows(payload)] == [6]


def test_a_nameless_slug_falls_back_to_the_person_id() -> None:
    # slugify() returns "" for a name with no ASCII alphanumerics; watch_people.slug is NOT NULL
    # and uniquely indexed, so the id is the fallback rather than an empty string.
    rows = credit_rows({'cast': [{'id': 77, 'name': '???', 'order': 0}]})
    assert rows[0]['slug'] == '77'


def test_missing_payload_is_not_an_error() -> None:
    assert credit_rows(None) == []
    assert credit_rows({}) == []
