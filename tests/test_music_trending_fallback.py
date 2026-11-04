from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.internal_api.routers import music


def _row(n: int) -> dict:
    return {'title': f't{n}', 'author': 'a', 'uri': f'u{n}', 'artwork': None, 'length_ms': 1, 'listeners': 1, 'plays': 1}


def _bot(week: int, month: int) -> tuple[SimpleNamespace, list[int]]:
    calls: list[int] = []

    async def trending(*, guild_id: int | None = None, days: int = 7) -> list[dict]:
        calls.append(days)
        return [_row(n) for n in range(week if days == 7 else month)]

    return SimpleNamespace(db=SimpleNamespace(music_plays=SimpleNamespace(trending=trending))), calls


@pytest.fixture(autouse=True)
def _empty_cache() -> None:
    music._trending_cache.clear()


async def test_thin_week_widens_to_the_month() -> None:
    bot, calls = _bot(week=1, month=12)
    assert len(await music._trending(bot, 1)) == 12
    assert calls == [7, music._TRENDING_FALLBACK_DAYS]


async def test_full_week_stays_the_week() -> None:
    bot, calls = _bot(week=music._TRENDING_MIN, month=12)
    assert len(await music._trending(bot, 1)) == music._TRENDING_MIN
    assert calls == [7]
