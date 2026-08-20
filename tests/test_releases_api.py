"""Tests for the internal-API handlers in :mod:`app.internal_api.routers.releases`.

Like ``tests/test_watchlist_api.py``: the handlers are plain async functions taking the bot
first, so they are called with a fake repository rather than through an HTTP client. The
shaping they delegate to is already covered pure in ``tests/test_releases_overview.py`` -- what
is worth proving here is the part that lives in the handler and nowhere else: *which row's*
credits each card is built from.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.internal_api.routers import releases as api
from app.services.releases import Subject

SERIES_ID, SEASON_ID, FILM_ID = 147, 385, 900


def title_row(row_id: int, slug: str, **kwargs: Any) -> dict[str, Any]:
    """One ``titles_releasing_between`` row -- exactly the columns that query selects."""
    row: dict[str, Any] = {
        'id': row_id, 'universe': 'mcu', 'slug': slug, 'title': slug,
        'release_date': datetime.date(2026, 8, 4), 'poster_path': None, 'sub_universe': None,
        'tmdb_type': 'tv', 'tmdb_id': 1, 'season_number': None, 'parent_id': None,
    }
    row.update(kwargs)
    return row


def fake_bot() -> MagicMock:
    """A bot whose comic half is empty and whose ``list_credits`` answers only for the ids it
    was actually asked for, exactly as ``= ANY($1::bigint[])`` does."""
    credits = [
        {'title_id': SERIES_ID, 'role': 'cast', 'slug': 'clark-gregg', 'character_name': 'Phil Coulson'},
        {'title_id': FILM_ID, 'role': 'cast', 'slug': 'tom-holland', 'character_name': 'Peter Parker'},
    ]
    bot = MagicMock(name='Bot')
    bot.db.releases.recent_comic_releases = AsyncMock(return_value=[])
    bot.db.releases.credits_for_releases = AsyncMock(return_value=[])
    bot.db.releases.characters_for_releases = AsyncMock(return_value=[])
    bot.db.watchlist.list_universes = AsyncMock(return_value=[{'slug': 'mcu', 'name': 'Marvel Cinematic Universe'}])
    bot.db.watchlist.titles_releasing_between = AsyncMock(return_value=[
        title_row(SEASON_ID, 'shield-s1', season_number=1, parent_id=SERIES_ID),
        title_row(FILM_ID, 'no-way-home', tmdb_type='movie'),
    ])
    bot.db.watchlist.list_credits = AsyncMock(
        side_effect=lambda ids: [row for row in credits if row['title_id'] in set(ids)])
    return bot


async def entries(bot: MagicMock) -> dict[str, Any]:
    """The cached catalogue half, keyed by card key. Fresh bot per call, so fresh cache key."""
    *_, titles = await api._overview_entries(bot, 7)
    return {entry.item.key: entry for entry in titles}


async def test_a_season_card_offers_its_series_person_subjects() -> None:
    """A season holds no credits of its own (TMDB's ``credits`` is series-level) and is the only
    row of a multi-season show inside the window -- so a card built from the season's own id
    carries no ``person``/``character`` subject and can never land in ``followed``.
    """
    season = (await entries(fake_bot()))['mcu/shield-s1'].item

    assert Subject.of('title', 'person', 'clark-gregg') in season.subjects
    assert Subject.of('title', 'character', 'Phil Coulson') in season.subjects


async def test_a_film_card_keeps_its_own_credits() -> None:
    """The regression risk: a film and a single-season show have no ``parent_id`` at all."""
    film = (await entries(fake_bot()))['mcu/no-way-home'].item

    assert Subject.of('title', 'person', 'tom-holland') in film.subjects
    assert Subject.of('title', 'person', 'clark-gregg') not in film.subjects


async def test_the_window_takes_one_credits_query() -> None:
    bot = fake_bot()

    await entries(bot)

    bot.db.watchlist.list_credits.assert_awaited_once()
    assert bot.db.watchlist.list_credits.await_args.args[0] == [SERIES_ID, FILM_ID]
