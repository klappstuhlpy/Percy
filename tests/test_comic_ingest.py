"""Tests for the comic archive ingest (``app/services/comics/ingest.py``).

The mapping functions are pure, so they are called directly with hand-built
:class:`GenericComic` objects. :class:`ComicIngest` is exercised against a fake repository
that records what it was asked to write -- the point of those tests is the *contract* (one
upsert plus a credit and character replace per comic, failures counted rather than raised),
not the SQL, which needs a live database.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from app.cogs.comic.models import Brand, GenericComic
from app.services.comics import (
    ComicIngest,
    character_rows,
    creator_rows,
    release_row,
    split_series,
)


def make_comic(**kwargs: Any) -> GenericComic:
    """A minimally-populated comic; every field the row builder reads has a default."""
    defaults: dict[str, Any] = {
        'brand': Brand.MARVEL,
        '_id': 4242,
        'title': 'Amazing Spider-Man #12',
        'description': 'Peter has a bad day.',
        'creators': {'Writer': ['Zeb Wells'], 'Artist': ['John Romita Jr.']},
        'image_url': 'https://example.invalid/cover.jpg',
        'url': 'https://example.invalid/comic',
        'page_count': 32,
        'price': 4.99,
        'date': datetime.datetime(2026, 8, 5, tzinfo=datetime.UTC),
        'characters': [{'name': 'Spider-Man', 'type': 'Main'}],
        'comic_format': 'Comic',
    }
    defaults.update(kwargs)
    return GenericComic(**defaults)


class FakeReleasesRepository:
    """Records writes; ``fail_on`` makes a titled comic blow up mid-ingest."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.releases: list[dict[str, Any]] = []
        self.creators: dict[int, list[tuple[str, str]]] = {}
        self.characters: dict[int, list[tuple[str, str | None]]] = {}
        self.known: dict[tuple[str, str], int] = {}
        self.fail_on = fail_on

    async def upsert_release(self, row: dict[str, Any]) -> tuple[int, bool]:
        if self.fail_on is not None and row['title'] == self.fail_on:
            raise RuntimeError('boom')
        self.releases.append(row)
        key = (row['brand'], row['external_id'])
        if key in self.known:
            return self.known[key], False
        release_id = self.known[key] = len(self.known) + 1
        return release_id, True

    async def replace_creators(self, release_id: int, creators: list[tuple[str, str]]) -> None:
        self.creators[release_id] = list(creators)

    async def replace_characters(self, release_id: int, characters: list[tuple[str, str | None]]) -> None:
        self.characters[release_id] = list(characters)


# -- split_series ---------------------------------------------------------

@pytest.mark.parametrize(
    ('title', 'expected'),
    [
        ('Amazing Spider-Man #12', ('Amazing Spider-Man', '12')),
        ('Batman #150 (Variant)', ('Batman', '150')),
        ('Ultimate Spider-Man #1.MU', ('Ultimate Spider-Man', '1.MU')),
        ('X-Men #3AU', ('X-Men', '3AU')),
        ('Batman (2016) #50', ('Batman', '50')),
        ('Saga Vol. 11 TP', ('Saga Vol. 11 TP', None)),
        ('Absolute Superman', ('Absolute Superman', None)),
        ('  Daredevil #7  ', ('Daredevil', '7')),
    ],
)
def test_split_series(title: str, expected: tuple[str, str | None]) -> None:
    assert split_series(title) == expected


def test_split_series_strips_year_qualifier_from_bare_series() -> None:
    """A year qualifier is dropped so ``Batman (2016)`` groups with ``Batman``."""
    assert split_series('Batman (2016)') == ('Batman', None)


def test_split_series_keeps_meaningful_parenthetical() -> None:
    """``(Variant)`` is not a volume qualifier, so an issueless title keeps it."""
    assert split_series('Batman (Variant)') == ('Batman (Variant)', None)


# -- release_row ----------------------------------------------------------

def test_release_row_maps_every_column() -> None:
    row = release_row(make_comic())
    assert row == {
        'brand': 'MARVEL',
        'external_id': '4242',
        'slug': 'amazing-spider-man-12',
        'title': 'Amazing Spider-Man #12',
        'series_slug': 'amazing-spider-man',
        'series_name': 'Amazing Spider-Man',
        'issue_number': '12',
        'release_date': datetime.date(2026, 8, 5),
        'cover_url': 'https://example.invalid/cover.jpg',
        'description': 'Peter has a bad day.',
        'price': 4.99,
        'page_count': 32,
        'format': 'Comic',
        'publisher': 'Marvel',
        'url': 'https://example.invalid/comic',
    }


def test_release_row_stringifies_external_id() -> None:
    """locg-api hands out ints, the manga scraper an index -- the column is TEXT."""
    assert release_row(make_comic(_id=7))['external_id'] == '7'


def test_release_row_without_date() -> None:
    assert release_row(make_comic(date=None))['release_date'] is None


def test_release_row_falls_back_to_external_id_for_unsluggable_title() -> None:
    """A title with no ASCII alphanumerics must still get a non-empty stored slug."""
    row = release_row(make_comic(title='???', _id=99))
    assert row['slug'] == '99'
    assert row['series_slug'] is None


def test_release_row_transliterates_non_ascii_title() -> None:
    assert release_row(make_comic(title='Pokémon #4'))['slug'] == 'pokemon-4'


# -- creator / character rows --------------------------------------------

def test_creator_rows_flattens_and_dedupes() -> None:
    comic = make_comic(creators={'Writer': ['Zeb Wells', 'Zeb Wells'], 'Artist': ['JRJR']})
    assert creator_rows(comic) == [('Zeb Wells', 'Writer'), ('JRJR', 'Artist')]


def test_creator_rows_skips_blank_names_and_roles() -> None:
    comic = make_comic(creators={'Writer': ['', '  ', 'Real Name'], '': ['Nobody']})
    assert creator_rows(comic) == [('Real Name', 'Writer')]


def test_creator_rows_without_credits() -> None:
    assert creator_rows(make_comic(creators=None)) == []


def test_character_rows_keeps_first_kind_per_name() -> None:
    """PK is ``(release_id, name)``, so a name listed twice must not produce two rows."""
    comic = make_comic(characters=[
        {'name': 'Spider-Man', 'type': 'Main'},
        {'name': 'Spider-Man', 'type': 'Supporting'},
        {'name': 'Venom', 'type': 'Villain'},
    ])
    assert character_rows(comic) == [('Spider-Man', 'Main'), ('Venom', 'Villain')]


def test_character_rows_allows_missing_type() -> None:
    assert character_rows(make_comic(characters=[{'name': 'Kingpin'}])) == [('Kingpin', None)]


def test_character_rows_skips_nameless_entries() -> None:
    assert character_rows(make_comic(characters=[{'type': 'Main'}, {'name': '  '}])) == []


# -- ComicIngest ----------------------------------------------------------

async def test_ingest_writes_release_creators_and_characters() -> None:
    repository = FakeReleasesRepository()
    report = await ComicIngest(repository).ingest('MARVEL', [make_comic()])

    assert (report.seen, report.created, report.updated, report.failed) == (1, 1, 0, 0)
    assert repository.creators[1] == [('Zeb Wells', 'Writer'), ('John Romita Jr.', 'Artist')]
    assert repository.characters[1] == [('Spider-Man', 'Main')]


async def test_ingest_counts_a_second_pass_as_updated() -> None:
    """Re-running a refresh over unchanged issues must be idempotent, not duplicating."""
    repository = FakeReleasesRepository()
    ingest = ComicIngest(repository)

    await ingest.ingest('MARVEL', [make_comic()])
    report = await ingest.ingest('MARVEL', [make_comic()])

    assert (report.created, report.updated) == (0, 1)
    assert len(repository.known) == 1


async def test_ingest_survives_one_bad_comic() -> None:
    """One malformed issue must not cost the rest of the refresh."""
    comics = [make_comic(_id=1), make_comic(_id=2, title='Broken #1'), make_comic(_id=3)]
    repository = FakeReleasesRepository(fail_on='Broken #1')

    report = await ComicIngest(repository).ingest('MARVEL', comics)

    assert (report.seen, report.created, report.failed) == (3, 2, 1)
    assert len(repository.releases) == 2


async def test_ingest_report_is_readable() -> None:
    report = await ComicIngest(FakeReleasesRepository()).ingest('DC', [make_comic()])
    assert str(report) == 'DC: 1 seen, 1 new, 0 updated, 0 failed'
