"""Queue editing on the player and playlist ownership, without Discord or Lavalink."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from app.cogs.music.cog import Music
from app.cogs.music.player import Player


def _player(items: list[str]) -> SimpleNamespace:
    return SimpleNamespace(queue=SimpleNamespace(_items=list(items)))


def test_move_and_remove_queued() -> None:
    p = _player(["a", "b", "c", "d"])
    assert Player.move_queued(p, 3, 0) == "d"  # type: ignore[arg-type]
    assert p.queue._items == ["d", "a", "b", "c"]
    assert Player.remove_queued(p, 1) == "a"  # type: ignore[arg-type]
    assert p.queue._items == ["d", "b", "c"]
    for bad in ((5, 0), (0, 5), (-1, 0)):
        with pytest.raises(IndexError):
            Player.move_queued(p, *bad)  # type: ignore[arg-type]
    with pytest.raises(IndexError):
        Player.remove_queued(p, 3)  # type: ignore[arg-type]


async def test_playlist_by_id_is_owner_only() -> None:
    cog = MagicMock()
    cog.bot.db.playlists.get_playlist_by_id = AsyncMock(
        return_value={"id": 7, "user_id": 2, "name": "theirs", "created": None, "thumbnail": None}
    )
    cog._get_playlist_tracks = AsyncMock(return_value=[])
    stranger, owner = SimpleNamespace(user=SimpleNamespace(id=1)), SimpleNamespace(user=SimpleNamespace(id=2))
    assert await Music.get_playlist(cog, stranger, 7) is None  # type: ignore[arg-type]
    found = await Music.get_playlist(cog, owner, 7)  # type: ignore[arg-type]
    assert found is not None and found.name == "theirs"


async def test_playlist_import_is_one_transaction() -> None:
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock

    from app.database.repositories.users import PlaylistsRepository

    events: list[str] = []
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=9)
    conn.executemany = AsyncMock()

    @asynccontextmanager
    async def txn() -> AsyncIterator[None]:
        events.append('begin')
        yield
        events.append('commit')

    conn.transaction = txn

    @asynccontextmanager
    async def acquire(**_: object) -> AsyncIterator[MagicMock]:
        yield conn

    repo = PlaylistsRepository(MagicMock(acquire=acquire))
    import datetime

    pid = await repo.create_playlist_with_tracks(1, 'n', datetime.datetime(2026, 1, 1), [('t', 'u', 'a')])
    assert pid == 9 and events == ['begin', 'commit']
    assert conn.executemany.await_args.args[1] == [(9, 't', 'u', 'a')]


@pytest.mark.parametrize(('requested', 'applied'), [(-5, 0), (0, 0), (50, 50), (100, 100), (105, 100), (999, 100)])
async def test_set_volume_clamps_to_percent_range(monkeypatch: pytest.MonkeyPatch, requested: int, applied: int) -> None:
    import wavelink

    from app.cogs.music.player import Player

    sent: list[int] = []

    async def fake_set_volume(_self: object, value: int = 100, /) -> None:
        sent.append(value)

    monkeypatch.setattr(wavelink.Player, 'set_volume', fake_set_volume)
    monkeypatch.setattr(Player, 'announce', lambda _self: None)

    await Player.set_volume(Player.__new__(Player), requested)
    assert sent == [applied]
