"""Tests for read/watched/skipped progress (``app/services/releases/progress.py``).

Pure, like ``tests/test_releases_overview.py``: rows are hand-built dicts shaped like the
``release_progress`` records the repository returns. No database, no FastAPI, no bot.

The one thing that *is* exercised against a stand-in is the merge rule, because last-write-wins
is a rule of the write path rather than of the shaping. The fake repository below reimplements
nothing: it replays the same conflict rule the SQL states (``WHERE release_progress.updated_at
< EXCLUDED.updated_at``) and the same in-batch collapse the real method does before the
statement, so a change to either in ``repositories/releases.py`` has to be mirrored here to
keep these green.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from app.services.releases import (
    PROGRESS_STATUSES,
    build_overview,
    check_status,
    comic_entry,
    progress_payload,
    title_entry,
)
from tests.test_releases_overview import UNIVERSES, comic_row, title_row

EARLY = datetime.datetime(2026, 8, 4, 9, 0)
LATE = datetime.datetime(2026, 8, 5, 9, 0)
START = datetime.date(2026, 7, 28)
END = datetime.date(2026, 8, 11)

COMIC_KEY = 'marvel/amazing-spider-man-12'
TITLE_KEY = 'mcu/avengers-doomsday'


def progress_row(**kwargs: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        'user_id': 1,
        'media': 'comic',
        'release_key': COMIC_KEY,
        'status': 'read',
        'updated_at': EARLY,
    }
    row.update(kwargs)
    return row


class FakeProgressRepo:
    """The merge rule of ``ReleasesRepository.set_progress_bulk``, without a pool.

    Mirrors both halves of the real method: duplicates within one batch collapse to the newest
    (Postgres refuses to let ``ON CONFLICT DO UPDATE`` touch a row twice in one statement), and
    a stored row only moves when the incoming ``updated_at`` is strictly newer.
    """

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}

    def merge(self, rows: list[tuple[str, str, str, datetime.datetime]]) -> int:
        newest: dict[tuple[str, str], tuple[str, str, str, datetime.datetime]] = {}
        for media, key, status, updated_at in rows:
            check_status(media, status)
            current = newest.get((media, key))
            if current is None or current[3] <= updated_at:
                newest[media, key] = (media, key, status, updated_at)

        written = 0
        for media, key, status, updated_at in newest.values():
            stored = self.rows.get((media, key))
            if stored is not None and stored['updated_at'] >= updated_at:
                continue
            self.rows[media, key] = progress_row(
                media=media, release_key=key, status=status, updated_at=updated_at)
            written += 1
        return written

    def all(self) -> list[dict[str, Any]]:
        return sorted(self.rows.values(), key=lambda row: (row['media'], row['release_key']))


# -- The media/status pairing --------------------------------------------------


@pytest.mark.parametrize(('media', 'status'), [
    ('comic', 'read'), ('comic', 'skipped'), ('title', 'watched'), ('title', 'skipped'),
])
def test_every_legal_pairing_passes(media: str, status: str) -> None:
    assert check_status(media, status) == status


@pytest.mark.parametrize(('media', 'status'), [('comic', 'watched'), ('title', 'read')])
def test_the_wrong_verb_for_a_medium_is_rejected_not_translated(media: str, status: str) -> None:
    """A ``watched`` comic is a client that mixed up its media -- and since ``media`` namespaces
    ``release_key``, the row it was about to write names the wrong item. Repairing the status
    would file it against a real item in the other catalogue."""
    with pytest.raises(ValueError, match='not valid for media'):
        check_status(media, status)


def test_skipped_is_the_one_status_both_media_share() -> None:
    shared = set(PROGRESS_STATUSES['comic']) & set(PROGRESS_STATUSES['title'])
    assert shared == {'skipped'}


def test_a_status_the_migration_does_not_allow_is_rejected() -> None:
    with pytest.raises(ValueError, match='not valid for media'):
        check_status('comic', 'reading')


def test_an_unknown_medium_is_rejected_before_the_status_is_even_looked_at() -> None:
    with pytest.raises(ValueError, match='unknown media'):
        check_status('news', 'read')


def test_the_union_of_the_sets_is_exactly_the_migrations_check() -> None:
    """``V42`` constrains the column to the union; the *pairing* lives in the service."""
    union = {status for statuses in PROGRESS_STATUSES.values() for status in statuses}
    assert union == {'read', 'watched', 'skipped'}


# -- progress_payload ----------------------------------------------------------


def test_the_payload_is_nested_by_medium_then_key() -> None:
    payload = progress_payload([progress_row()])
    assert payload == {
        'comic': {COMIC_KEY: {'status': 'read', 'updated_at': '2026-08-04T09:00:00'}},
        'title': {},
    }


def test_both_media_are_present_even_for_a_user_who_has_marked_nothing() -> None:
    """So a renderer can index ``progress[card['media']]`` without a guard, signed out or in."""
    assert progress_payload([]) == {'comic': {}, 'title': {}}


def test_the_two_media_never_collide_on_a_shared_key() -> None:
    """``release_key`` is only unique within a medium, which is why the map is nested rather
    than flattened onto one string."""
    rows = [
        progress_row(media='comic', release_key='marvel/loki', status='read'),
        progress_row(media='title', release_key='marvel/loki', status='watched'),
    ]
    payload = progress_payload(rows)
    assert payload['comic']['marvel/loki']['status'] == 'read'
    assert payload['title']['marvel/loki']['status'] == 'watched'


def test_a_null_timestamp_survives_as_none_rather_than_crashing() -> None:
    assert progress_payload([progress_row(updated_at=None)])['comic'][COMIC_KEY]['updated_at'] is None


def test_the_last_row_for_a_key_wins_the_map() -> None:
    """The repository orders by ``(media, release_key)`` and the primary key makes duplicates
    impossible, so this only pins down that shaping does not silently drop a row."""
    rows = [progress_row(status='read'), progress_row(status='skipped')]
    assert progress_payload(rows)['comic'][COMIC_KEY]['status'] == 'skipped'


# -- The merge rule ------------------------------------------------------------


def test_a_first_write_lands() -> None:
    repo = FakeProgressRepo()
    assert repo.merge([('comic', COMIC_KEY, 'read', EARLY)]) == 1
    assert progress_payload(repo.all())['comic'][COMIC_KEY]['status'] == 'read'


def test_a_newer_write_overwrites_an_older_one() -> None:
    repo = FakeProgressRepo()
    repo.merge([('comic', COMIC_KEY, 'read', EARLY)])
    assert repo.merge([('comic', COMIC_KEY, 'skipped', LATE)]) == 1
    assert progress_payload(repo.all())['comic'][COMIC_KEY]['status'] == 'skipped'


def test_a_stale_tab_cannot_undo_a_newer_change_from_another_device() -> None:
    repo = FakeProgressRepo()
    repo.merge([('comic', COMIC_KEY, 'skipped', LATE)])
    assert repo.merge([('comic', COMIC_KEY, 'read', EARLY)]) == 0
    assert progress_payload(repo.all())['comic'][COMIC_KEY]['status'] == 'skipped'


def test_replaying_the_same_write_changes_nothing() -> None:
    """Strictly-newer, not newer-or-equal: a debounced client re-sending its buffer is a no-op
    rather than a needless row rewrite."""
    repo = FakeProgressRepo()
    repo.merge([('comic', COMIC_KEY, 'read', EARLY)])
    assert repo.merge([('comic', COMIC_KEY, 'read', EARLY)]) == 0


def test_two_toggles_of_one_item_in_one_batch_collapse_to_the_newest() -> None:
    """Postgres aborts a statement whose ``ON CONFLICT DO UPDATE`` would touch a row twice, so
    the collapse happens before the merge rather than being left to the database."""
    repo = FakeProgressRepo()
    written = repo.merge([
        ('comic', COMIC_KEY, 'read', EARLY),
        ('comic', COMIC_KEY, 'skipped', LATE),
    ])
    assert written == 1
    assert progress_payload(repo.all())['comic'][COMIC_KEY]['status'] == 'skipped'


def test_a_batch_spanning_both_media_merges_in_one_go() -> None:
    repo = FakeProgressRepo()
    written = repo.merge([
        ('comic', COMIC_KEY, 'read', EARLY),
        ('title', TITLE_KEY, 'watched', EARLY),
    ])
    assert written == 2
    payload = progress_payload(repo.all())
    assert list(payload['comic']) == [COMIC_KEY]
    assert list(payload['title']) == [TITLE_KEY]


def test_a_bad_pairing_anywhere_in_a_batch_rejects_the_whole_batch() -> None:
    """Validation runs over every entry before anything is written, so a mixed-up client cannot
    half-apply its sync."""
    repo = FakeProgressRepo()
    with pytest.raises(ValueError, match='not valid for media'):
        repo.merge([
            ('comic', COMIC_KEY, 'read', EARLY),
            ('comic', 'marvel/x-men-1', 'watched', EARLY),
        ])
    assert repo.all() == []


def test_an_empty_batch_writes_nothing() -> None:
    assert FakeProgressRepo().merge([]) == 0


# -- Progress on the overview --------------------------------------------------


def test_the_overview_carries_progress_beside_the_cards_not_on_them() -> None:
    """The cards come out of a shared TTL cache; a per-user ``status`` written onto one would be
    served to the next visitor. The join is the renderer's, on ``media`` + ``key``."""
    payload = build_overview([comic_entry(comic_row())], [], start=START, end=END,
                             progress=[progress_row()])
    card = payload['comics'][0]
    assert 'status' not in card
    assert payload['progress']['comic'][card['key']] == {'status': 'read', 'updated_at': '2026-08-04T09:00:00'}


def test_progress_joins_on_the_same_key_the_delivery_claim_uses() -> None:
    entry = comic_entry(comic_row())
    payload = build_overview([entry], [], start=START, end=END, progress=[progress_row()])
    assert entry.item.key == entry.card['key'] == next(iter(payload['progress']['comic']))


def test_a_signed_out_overview_gets_the_same_shape_with_empty_maps() -> None:
    payload = build_overview([comic_entry(comic_row())], [], start=START, end=END)
    assert payload['progress'] == {'comic': {}, 'title': {}}


def test_the_overview_payload_keys_are_exactly_these_five() -> None:
    payload = build_overview([], [], start=START, end=END)
    assert set(payload) == {'window', 'comics', 'titles', 'followed', 'progress'}


def test_progress_covers_a_card_in_either_medium() -> None:
    payload = build_overview(
        [comic_entry(comic_row())],
        [title_entry(title_row(), universe_names=UNIVERSES)],
        start=START, end=END,
        progress=[progress_row(), progress_row(media='title', release_key=TITLE_KEY, status='watched')],
    )
    assert payload['progress']['comic'][COMIC_KEY]['status'] == 'read'
    assert payload['progress']['title'][TITLE_KEY]['status'] == 'watched'


def test_progress_for_a_card_that_is_not_on_the_page_is_still_returned() -> None:
    """Unlike ``followed``, progress is not narrowed to the window: the same map serves a page
    that is also rendering something out of it, and dropping rows would cost a second request."""
    payload = build_overview([comic_entry(comic_row())], [], start=START, end=END,
                             progress=[progress_row(release_key='marvel/some-other-book')])
    assert list(payload['progress']['comic']) == ['marvel/some-other-book']


def test_progress_and_followed_are_independent() -> None:
    """Marking something read is not following it, and following it is not marking it read."""
    payload = build_overview([comic_entry(comic_row())], [], start=START, end=END,
                             progress=[progress_row()])
    assert payload['followed'] == []
    assert payload['progress']['comic'][COMIC_KEY]['status'] == 'read'
