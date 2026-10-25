"""User music library routes against an in-memory fake of the playlists/music_plays repositories."""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.internal_api.routers import users as api


class FakePlaylists:
    def __init__(self) -> None:
        self.lists: list[dict] = []
        self.tracks: list[dict] = []

    async def get_user_playlists(self, uid) -> Any:
        return [p for p in self.lists if p["user_id"] == uid]

    async def get_liked_songs(self, uid) -> Any:
        return next((p for p in self.lists if p["user_id"] == uid and p["name"] == "Liked Songs"), None)

    async def get_playlist_by_id(self, pid) -> Any:
        return next((p for p in self.lists if p["id"] == pid), None)

    async def create_playlist(self, uid, name, created) -> Any:
        self.lists.append({"id": len(self.lists) + 1, "user_id": uid, "name": name})
        return len(self.lists)

    async def get_playlist_tracks(self, pid) -> Any:
        return [t for t in self.tracks if t["playlist_id"] == pid]

    async def add_track(self, pid, name, url) -> Any:
        self.tracks.append({"playlist_id": pid, "name": name, "url": url})

    async def remove_track_by_url(self, pid, url) -> Any:
        self.tracks[:] = [t for t in self.tracks if not (t["playlist_id"] == pid and t["url"] == url)]


def _bot() -> MagicMock:
    bot = MagicMock()
    bot.db.playlists = FakePlaylists()
    bot.db.music_plays.user_top_tracks = AsyncMock(return_value=[])
    bot.db.music_plays.user_recent = AsyncMock(return_value=[])
    bot.get_cog = MagicMock(return_value=None)
    return bot


async def test_library_shape_empty() -> None:
    assert await api.get_user_music(1, _bot()) == {"top_tracks": [], "recent": [], "liked": [], "playlists": []}


async def test_like_creates_liked_playlist() -> None:
    bot = _bot()
    assert await api.like_track(1, api.LikeBody(name="a", url="u"), bot) == {"liked": True}
    out = await api.get_user_music(1, bot)
    assert out["liked"] == [{"name": "a", "url": "u"}]
    assert out["playlists"] == []


async def test_like_duplicate_noop() -> None:
    bot = _bot()
    for _ in range(2):
        await api.like_track(1, api.LikeBody(name="a", url="u"), bot)
    assert len(bot.db.playlists.tracks) == 1


async def test_unlike() -> None:
    bot = _bot()
    await api.like_track(1, api.LikeBody(name="a", url="u"), bot)
    assert await api.unlike_track(1, bot, url="u") == {"liked": False}
    assert (await api.get_user_music(1, bot))["liked"] == []


async def test_playlist_other_owner_404() -> None:
    bot = _bot()
    bot.db.playlists.lists.append({"id": 7, "user_id": 2, "name": "mine"})
    with pytest.raises(HTTPException) as e:
        await api.get_user_playlist(1, 7, bot)
    assert e.value.status_code == 404
    assert (await api.get_user_playlist(2, 7, bot))["name"] == "mine"


async def test_recent_uses_history_shape() -> None:
    bot = _bot()
    bot.db.music_plays.user_recent = AsyncMock(return_value=[{
        "title": "t", "author": "a", "uri": "u", "artwork": None, "length_ms": 1, "user_id": 1,
        "played_at": datetime.datetime(2026, 1, 1),
    }])
    item = (await api.get_user_music(1, bot))["recent"][0]
    assert item["user_id"] == "1" and item["played_at"].startswith("2026-01-01")
