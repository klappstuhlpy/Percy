"""Queue editing on the player and playlist ownership, without Discord or Lavalink."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

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
