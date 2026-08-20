"""Tests for the comic catalogue payload shaping and the internal-API handlers built on it.

Same two-layer split as ``test_watchlist_api.py``: :mod:`app.services.comics.payload` is pure
row-to-JSON shaping and is tested directly, while the handlers in
:mod:`app.internal_api.routers.comics` are plain async functions taking the bot first, so they
are called with a fake repository rather than through an HTTP client.

The JSON-safety assertions are load-bearing (``price`` is ``NUMERIC`` -> :class:`decimal.Decimal`
from asyncpg), and so is the issue-number sort -- ``issue_number`` is TEXT and mixes plain
digits with tokens like ``'1.MU'`` / ``'3AU'``.
"""

from __future__ import annotations

import datetime
import decimal
from typing import Any

import pytest
from fastapi import HTTPException

from app.internal_api.routers import comics as api
from app.services.comics.payload import (
    browse_payload,
    issue_sort_key,
    names_payload,
    release_payload,
    series_context,
    series_detail_payload,
    series_summary_payload,
)


def _release(**overrides: Any) -> dict[str, Any]:
    """A ``comic_releases`` row as asyncpg would hand it back."""
    row: dict[str, Any] = {
        'id': 1,
        'brand': 'MARVEL',
        'external_id': '1',
        'slug': 'amazing-spider-man-1',
        'title': 'Amazing Spider-Man #1',
        'series_slug': 'amazing-spider-man',
        'series_name': 'Amazing Spider-Man',
        'issue_number': '1',
        'release_date': datetime.date(2026, 8, 5),
        'cover_url': 'https://example.invalid/c.jpg',
        'description': 'Peter swings back into action.',
        'price': decimal.Decimal('4.99'),
        'page_count': 32,
        'format': 'Comic',
        'publisher': 'Marvel Comics',
        'url': 'https://example.invalid/u',
        'first_seen': datetime.datetime(2026, 8, 1, 12, 0, 0),
        'updated_at': datetime.datetime(2026, 8, 1, 12, 0, 0),
    }
    row.update(overrides)
    return row


# -- Payload shaping ----------------------------------------------------------


def test_issue_sort_key_is_numeric_on_the_leading_digits() -> None:
    values = ['12', '3AU', '1.MU', 'Annual', None, '150']
    ordered = sorted(values, key=issue_sort_key)
    assert ordered == ['1.MU', '3AU', '12', '150', None, 'Annual']


def test_issue_sort_key_ties_break_lexically() -> None:
    assert issue_sort_key('3A') < issue_sort_key('3B')
    assert issue_sort_key('') == issue_sort_key(None)


def test_release_payload_converts_decimal_price_and_dates() -> None:
    payload = release_payload(_release())
    assert payload['price'] == 4.99
    assert isinstance(payload['price'], float)
    assert payload['release_date'] == '2026-08-05'
    assert payload['first_seen'] == '2026-08-01T12:00:00'
    assert payload['credits'] == []
    assert payload['characters'] == []
    assert 'external_id' not in payload  # identity column, not part of the public shape


def test_release_payload_tolerates_a_null_price_and_date() -> None:
    payload = release_payload(_release(price=None, release_date=None))
    assert payload['price'] is None
    assert payload['release_date'] is None


def test_release_payload_attaches_credits_and_characters() -> None:
    payload = release_payload(
        _release(),
        credits=[{'name': 'Jane Doe', 'role': 'Writer'}],
        characters=[{'name': 'Spider-Man', 'kind': 'Main'}],
    )
    assert payload['credits'] == [{'name': 'Jane Doe', 'role': 'Writer'}]
    assert payload['characters'] == [{'name': 'Spider-Man', 'kind': 'Main'}]


def test_browse_payload_pagination_maths() -> None:
    payload = browse_payload([_release()], total=5, page=1, per_page=2, filters={'brand': 'MARVEL'})
    assert (payload['total'], payload['page'], payload['per_page']) == (5, 1, 2)
    assert payload['total_pages'] == 3  # ceil(5 / 2)
    assert payload['filters'] == {'brand': 'MARVEL'}


def test_browse_payload_last_partial_page() -> None:
    # page 3 of 5 items at 2/page has exactly one item, but total_pages is unaffected by
    # how many rows the caller actually handed in for that page.
    payload = browse_payload([_release(id=5)], total=5, page=3, per_page=2, filters={})
    assert len(payload['items']) == 1
    assert payload['total_pages'] == 3


def test_browse_payload_empty_result_still_reports_one_page() -> None:
    payload = browse_payload([], total=0, page=1, per_page=24, filters={})
    assert payload['total_pages'] == 1
    assert payload['items'] == []


def test_series_summary_payload_shapes_a_list_series_row() -> None:
    payload = series_summary_payload({
        'series_slug': 'amazing-spider-man', 'series_name': 'Amazing Spider-Man', 'brand': 'MARVEL',
        'issue_count': 3, 'latest': datetime.date(2026, 8, 5),
    })
    assert payload == {
        'series_slug': 'amazing-spider-man', 'series_name': 'Amazing Spider-Man', 'brand': 'MARVEL',
        'issue_count': 3, 'latest': '2026-08-05',
    }


def test_series_detail_payload_orders_issues_by_issue_number() -> None:
    issues = [
        _release(id=3, slug='asm-12', issue_number='12', release_date=datetime.date(2026, 8, 19)),
        _release(id=1, slug='asm-1', issue_number='1', release_date=datetime.date(2026, 8, 5)),
        _release(id=2, slug='asm-3au', issue_number='3AU', release_date=datetime.date(2026, 8, 12)),
    ]
    payload = series_detail_payload(issues)
    assert [issue['slug'] for issue in payload['issues']] == ['asm-1', 'asm-3au', 'asm-12']
    assert payload['issue_count'] == 3
    assert payload['series_slug'] == 'amazing-spider-man'


def test_series_detail_payload_breaks_ties_on_release_date() -> None:
    # Two issues sharing an issue number (a reprint) sort by release date.
    issues = [
        _release(id=1, slug='asm-1-reprint', issue_number='1', release_date=datetime.date(2026, 9, 1)),
        _release(id=2, slug='asm-1', issue_number='1', release_date=datetime.date(2026, 8, 5)),
    ]
    payload = series_detail_payload(issues)
    assert [issue['slug'] for issue in payload['issues']] == ['asm-1', 'asm-1-reprint']


def test_series_context_finds_neighbours_and_more() -> None:
    issues = [
        _release(id=1, slug='asm-1', issue_number='1'),
        _release(id=2, slug='asm-2', issue_number='2'),
        _release(id=3, slug='asm-3', issue_number='3'),
        _release(id=4, slug='asm-4', issue_number='4'),
    ]
    context = series_context(issues, current_id=2)
    assert context['previous']['slug'] == 'asm-1'
    assert context['next']['slug'] == 'asm-3'
    assert [row['slug'] for row in context['more_from_series']] == ['asm-1', 'asm-3', 'asm-4']


def test_series_context_first_and_last_have_no_neighbour_on_that_side() -> None:
    issues = [_release(id=1, issue_number='1'), _release(id=2, issue_number='2')]
    assert series_context(issues, current_id=1)['previous'] is None
    assert series_context(issues, current_id=2)['next'] is None


def test_names_payload_shapes_list_names_rows() -> None:
    payload = names_payload('creators', [{'name': 'Jane Doe', 'appearances': 3}])
    assert payload == {'kind': 'creators', 'names': [{'name': 'Jane Doe', 'appearances': 3}]}


# -- Fake repository ----------------------------------------------------------


class FakeReleasesRepository:
    """Stands in for :class:`ReleasesRepository`, filtering/sorting the same way the SQL does."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.credits: dict[int, list[tuple[str, str]]] = {}
        self.characters: dict[int, list[tuple[str, str | None]]] = {}

    def _filtered(
        self, *, brand=None, series=None, creator=None, character=None,
        since=None, until=None, search=None,
    ) -> list[dict[str, Any]]:
        rows = self.rows
        if brand is not None:
            rows = [r for r in rows if r['brand'] == brand]
        if series is not None:
            rows = [r for r in rows if r['series_slug'] == series]
        if since is not None:
            rows = [r for r in rows if r['release_date'] and r['release_date'] >= since]
        if until is not None:
            rows = [r for r in rows if r['release_date'] and r['release_date'] <= until]
        if search:
            rows = [r for r in rows if search.lower() in r['title'].lower()]
        if creator is not None:
            rows = [r for r in rows if any(
                name.lower() == creator.lower() for name, _ in self.credits.get(r['id'], []))]
        if character is not None:
            rows = [r for r in rows if any(
                name.lower() == character.lower() for name, _ in self.characters.get(r['id'], []))]
        return rows

    async def list_releases(
        self, *, brand=None, series=None, creator=None, character=None,
        since=None, until=None, search=None, limit=25, offset=0,
    ) -> list[dict[str, Any]]:
        rows = sorted(
            self._filtered(brand=brand, series=series, creator=creator, character=character,
                            since=since, until=until, search=search),
            key=lambda r: (r['release_date'] or datetime.date.min, r['id']), reverse=True,
        )
        return rows[offset:offset + limit]

    async def count_releases(self, **kwargs: Any) -> int:
        return len(self._filtered(**kwargs))

    async def get_release(self, brand: str, slug: str) -> dict[str, Any] | None:
        return next((r for r in self.rows if r['brand'] == brand and r['slug'] == slug), None)

    async def get_credits(self, release_id: int) -> list[dict[str, Any]]:
        return [{'name': n, 'role': role} for n, role in self.credits.get(release_id, [])]

    async def get_characters(self, release_id: int) -> list[dict[str, Any]]:
        return [{'name': n, 'kind': kind} for n, kind in self.characters.get(release_id, [])]

    async def list_series(self, *, brand: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.rows if brand is None else [r for r in self.rows if r['brand'] == brand]
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for r in rows:
            if not r['series_slug']:
                continue
            group = groups.setdefault((r['series_slug'], r['brand']), {
                'series_slug': r['series_slug'], 'series_name': r['series_name'],
                'brand': r['brand'], 'issue_count': 0, 'latest': None,
            })
            group['issue_count'] += 1
            if r['release_date'] and (group['latest'] is None or r['release_date'] > group['latest']):
                group['latest'] = r['release_date']
        return list(groups.values())[:limit]

    async def list_series_releases(self, series_slug: str, *, brand: str | None = None) -> list[dict[str, Any]]:
        return [
            r for r in self.rows
            if r['series_slug'] == series_slug and (brand is None or r['brand'] == brand)
        ]

    async def list_names(
        self, table: str, *, brand: str | None = None, limit: int = 200,
    ) -> list[dict[str, Any]]:
        if table not in ('comic_creators', 'comic_characters'):
            raise ValueError(f'unknown name table {table!r}')
        store = self.credits if table == 'comic_creators' else self.characters
        counts: dict[str, int] = {}
        for release_id, pairs in store.items():
            row = next((r for r in self.rows if r['id'] == release_id), None)
            if row is None or (brand is not None and row['brand'] != brand):
                continue
            for name, _ in pairs:
                counts[name] = counts.get(name, 0) + 1
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [{'name': n, 'appearances': c} for n, c in ordered][:limit]


class FakeBot:
    def __init__(self, repo: FakeReleasesRepository) -> None:
        self.db = type('DB', (), {'releases': repo})()


@pytest.fixture(autouse=True)
def clear_payload_cache() -> None:
    """Drops every cached browse/list payload between tests (see ``test_watchlist_api.py``)."""
    api._browse.invalidate_containing('_browse')
    api._series_list.invalidate_containing('_series_list')
    api._names.invalidate_containing('_names')


@pytest.fixture
def repo() -> FakeReleasesRepository:
    rows = [
        _release(id=1, slug='amazing-spider-man-1', issue_number='1', release_date=datetime.date(2026, 8, 5)),
        _release(id=2, slug='amazing-spider-man-2', issue_number='2', release_date=datetime.date(2026, 8, 12),
                  title='Amazing Spider-Man #2'),
        _release(id=3, slug='amazing-spider-man-3au', issue_number='3AU', release_date=datetime.date(2026, 8, 19),
                  title='Amazing Spider-Man Annual #3AU'),
        _release(id=4, brand='MARVEL', slug='daredevil-7', series_slug='daredevil', series_name='Daredevil',
                  issue_number='7', release_date=datetime.date(2026, 8, 15), title='Daredevil #7'),
        _release(id=5, brand='DC', slug='batman-1', series_slug='batman', series_name='Batman',
                  issue_number='1', release_date=datetime.date(2026, 8, 10), title='Batman #1',
                  publisher='DC Comics'),
    ]
    repository = FakeReleasesRepository(rows)
    repository.credits[1] = [('Jane Doe', 'Writer')]
    repository.credits[2] = [('Jane Doe', 'Writer'), ('John Roe', 'Artist')]
    repository.characters[1] = [('Spider-Man', 'Main')]
    repository.characters[4] = [('Daredevil', 'Main'), ('Kingpin', 'Villain')]
    return repository


@pytest.fixture
def bot(repo: FakeReleasesRepository) -> Any:
    return FakeBot(repo)


# -- Handlers -----------------------------------------------------------------


async def test_list_comics_filters_by_brand(bot: Any) -> None:
    result = await api.list_comics(bot, brand='DC')
    assert [item['slug'] for item in result['items']] == ['batman-1']
    assert result['filters']['brand'] == 'DC'
    assert result['total'] == 1


async def test_list_comics_paginates_including_the_last_partial_page(bot: Any) -> None:
    page1 = await api.list_comics(bot, brand='MARVEL', page=1, per_page=2)
    page2 = await api.list_comics(bot, brand='MARVEL', page=2, per_page=2)
    page3 = await api.list_comics(bot, brand='MARVEL', page=3, per_page=2)
    assert (page1['total'], page1['total_pages']) == (4, 2)
    assert len(page1['items']) == 2
    assert len(page2['items']) == 2
    assert page3['items'] == []  # past the last page
    # newest first: id 3 (Aug 19) before id 4 (Aug 15) before id 2 (Aug 12) before id 1 (Aug 5)
    assert [item['id'] for item in page1['items'] + page2['items']] == [3, 4, 2, 1]


async def test_list_comics_filters_by_creator_and_character(bot: Any) -> None:
    by_creator = await api.list_comics(bot, creator='jane doe')  # case-insensitive
    assert {item['id'] for item in by_creator['items']} == {1, 2}
    by_character = await api.list_comics(bot, character='Kingpin')
    assert [item['id'] for item in by_character['items']] == [4]


async def test_list_comics_filters_by_date_window_and_search(bot: Any) -> None:
    windowed = await api.list_comics(
        bot, since=datetime.date(2026, 8, 10), until=datetime.date(2026, 8, 15))
    assert {item['id'] for item in windowed['items']} == {2, 4, 5}
    searched = await api.list_comics(bot, search='annual')
    assert [item['id'] for item in searched['items']] == [3]


async def test_list_comics_is_cached_between_calls(bot: Any, repo: FakeReleasesRepository) -> None:
    calls: list[str] = []
    original = repo.list_releases

    async def counting(*args: Any, **kwargs: Any) -> Any:
        calls.append('list_releases')
        return await original(*args, **kwargs)

    repo.list_releases = counting  # type: ignore[method-assign]
    await api.list_comics(bot)
    await api.list_comics(bot)
    assert calls == ['list_releases']
    await api.list_comics(bot, brand='DC')  # a different filter set must miss
    assert len(calls) == 2


async def test_get_comic_returns_credits_characters_and_series_context(bot: Any) -> None:
    detail = await api.get_comic(bot, 'MARVEL', 'amazing-spider-man-2')
    assert detail['credits'] == [{'name': 'Jane Doe', 'role': 'Writer'}, {'name': 'John Roe', 'role': 'Artist'}]
    assert detail['series']['previous']['slug'] == 'amazing-spider-man-1'
    assert detail['series']['next']['slug'] == 'amazing-spider-man-3au'


async def test_get_comic_without_a_series_has_an_empty_series_block(bot: Any, repo: FakeReleasesRepository) -> None:
    repo.rows.append(_release(id=6, slug='one-shot', series_slug=None, series_name=None, issue_number=None))
    detail = await api.get_comic(bot, 'MARVEL', 'one-shot')
    assert detail['series'] == {'previous': None, 'next': None, 'more_from_series': []}


async def test_get_comic_404s_an_unknown_slug(bot: Any) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.get_comic(bot, 'MARVEL', 'not-a-real-issue')
    assert exc.value.status_code == 404


async def test_list_series_scopes_to_a_brand(bot: Any) -> None:
    result = await api.list_series(bot, brand='MARVEL')
    assert {row['series_slug'] for row in result['series']} == {'amazing-spider-man', 'daredevil'}
    everything = await api.list_series(bot)
    assert {row['series_slug'] for row in everything['series']} == {'amazing-spider-man', 'daredevil', 'batman'}


async def test_get_series_returns_issues_in_reading_order(bot: Any) -> None:
    series = await api.get_series(bot, 'amazing-spider-man')
    assert [issue['issue_number'] for issue in series['issues']] == ['1', '2', '3AU']
    assert series['issue_count'] == 3


async def test_series_lookups_are_brand_scoped(bot: Any) -> None:
    """A series slug comes from a title, so two publishers can collide on one.

    When they do, neither the series page nor a release's neighbours may mix them: a DC book
    must never show up as the next issue of a Marvel one.
    """
    collision = _release(
        id=99, brand='DC', slug='amazing-spider-man-1-dc', title='Amazing Spider-Man #1',
        series_slug='amazing-spider-man', issue_number='1')
    bot.db.releases.rows.append(collision)

    marvel = await api.get_series(bot, 'amazing-spider-man', brand='MARVEL')
    assert {issue['brand'] for issue in marvel['issues']} == {'MARVEL'}
    assert marvel['issue_count'] == 3

    dc = await api.get_series(bot, 'amazing-spider-man', brand='DC')
    assert dc['issue_count'] == 1

    unscoped = await api.get_series(bot, 'amazing-spider-man')
    assert unscoped['issue_count'] == 4

    detail = await api.get_comic(bot, 'MARVEL', 'amazing-spider-man-1')
    assert [row['brand'] for row in detail['series']['more_from_series']] == ['MARVEL', 'MARVEL']


async def test_get_series_404s_a_series_with_no_issues(bot: Any) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.get_series(bot, 'nonexistent')
    assert exc.value.status_code == 404


async def test_list_names_creators_and_characters(bot: Any) -> None:
    creators = await api.list_names(bot, kind='creators')
    assert {row['name'] for row in creators['names']} == {'Jane Doe', 'John Roe'}
    characters = await api.list_names(bot, kind='characters', brand='MARVEL')
    assert {row['name'] for row in characters['names']} == {'Spider-Man', 'Daredevil', 'Kingpin'}


async def test_list_names_rejects_an_unknown_kind(bot: Any) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.list_names(bot, kind='people')
    assert exc.value.status_code == 400
