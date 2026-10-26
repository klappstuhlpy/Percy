"""User music library routes against an in-memory fake of the playlists/music_plays repositories."""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.internal_api.routers import users as api

U = "https://example.com/u"


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

    async def get_playlist_by_name(self, uid, name) -> Any:
        return next((p for p in self.lists if p["user_id"] == uid and p["name"].lower() == name.lower()), None)

    async def create_playlist(self, uid, name, created) -> Any:
        self.lists.append({"id": len(self.lists) + 1, "user_id": uid, "name": name, "thumbnail": None})
        return len(self.lists)

    async def rename_playlist(self, pid, name) -> None:
        (await self.get_playlist_by_id(pid))["name"] = name

    async def set_playlist_thumbnail(self, pid, thumbnail) -> None:
        (await self.get_playlist_by_id(pid))["thumbnail"] = thumbnail

    async def delete_playlist(self, pid) -> None:
        self.lists[:] = [p for p in self.lists if p["id"] != pid]
        self.tracks[:] = [t for t in self.tracks if t["playlist_id"] != pid]

    async def get_playlist_tracks(self, pid) -> Any:
        return [t for t in self.tracks if t["playlist_id"] == pid]

    async def add_track(self, pid, name, url, *, author=None, artwork=None) -> Any:
        self.tracks.append({"playlist_id": pid, "name": name, "url": url, "author": author, "artwork": artwork})

    async def remove_track_by_url(self, pid, url) -> Any:
        self.tracks[:] = [t for t in self.tracks if not (t["playlist_id"] == pid and t["url"] == url)]


def _bot() -> MagicMock:
    bot = MagicMock()
    bot.db.playlists = FakePlaylists()
    bot.db.music_plays.user_top_tracks = AsyncMock(return_value=[])
    bot.db.music_plays.user_recent = AsyncMock(return_value=[])
    bot.db.music_plays.artwork_for = AsyncMock(return_value={})
    bot.get_cog = MagicMock(return_value=None)
    return bot


def _track(**kw: Any) -> api.TrackBody:
    return api.TrackBody(**{"name": "a", "url": U, **kw})


async def test_library_shape_empty() -> None:
    assert await api.get_user_music(1, _bot()) == {"top_tracks": [], "recent": [], "liked": [], "playlists": []}


async def test_like_creates_liked_playlist_and_keeps_artist_and_cover() -> None:
    bot = _bot()
    assert await api.like_track(1, _track(author="MGMT", artwork="https://img/a.jpg"), bot) == {"liked": True}
    out = await api.get_user_music(1, bot)
    assert out["liked"] == [{"name": "a", "url": U, "author": "MGMT", "artwork": "https://img/a.jpg"}]
    assert out["playlists"] == []


async def test_old_rows_recover_cover_from_plays_or_youtube() -> None:
    bot = _bot()
    yt = "https://www.youtube.com/watch?v=MmZexg8sxyk"
    await api.like_track(1, _track(), bot)
    await api.like_track(1, _track(url=yt), bot)
    bot.db.music_plays.artwork_for = AsyncMock(return_value={U: "https://img/played.jpg"})
    liked = (await api.get_user_music(1, bot))["liked"]
    assert [t["artwork"] for t in liked] == ["https://img/played.jpg", "https://i.ytimg.com/vi/MmZexg8sxyk/mqdefault.jpg"]


async def test_like_duplicate_noop_and_bad_links_rejected() -> None:
    bot = _bot()
    for _ in range(2):
        await api.like_track(1, _track(), bot)
    assert len(bot.db.playlists.tracks) == 1
    for bad in (_track(url="javascript:alert(1)"), _track(artwork="data:image/png;base64,x")):
        with pytest.raises(HTTPException) as e:
            await api.like_track(1, bad, bot)
        assert e.value.status_code == 400


async def test_unlike() -> None:
    bot = _bot()
    await api.like_track(1, _track(), bot)
    assert await api.unlike_track(1, bot, url=U) == {"liked": False}
    assert (await api.get_user_music(1, bot))["liked"] == []


async def test_playlist_other_owner_404() -> None:
    bot = _bot()
    bot.db.playlists.lists.append({"id": 7, "user_id": 2, "name": "mine", "thumbnail": None})
    with pytest.raises(HTTPException) as e:
        await api.get_user_playlist(1, 7, bot)
    assert e.value.status_code == 404
    assert (await api.get_user_playlist(2, 7, bot))["name"] == "mine"


async def test_playlist_lifecycle() -> None:
    bot = _bot()
    made = await api.create_user_playlist(1, api.PlaylistCreateBody(name="  Road   trip "), bot)
    pid = int(made["id"])
    assert made["name"] == "Road trip"

    assert (await api.add_user_playlist_track(1, pid, _track(artwork="https://img/c.jpg"), bot))["added"] is True
    assert (await api.add_user_playlist_track(1, pid, _track(), bot))["added"] is False
    summary = (await api.get_user_music(1, bot))["playlists"][0]
    assert summary["track_count"] == 1 and summary["cover"] == "https://img/c.jpg" and summary["thumbnail"] is None

    p = await api.patch_user_playlist(1, pid, api.PlaylistPatchBody(name="Drive", thumbnail="https://img/t.png"), bot)
    assert (p["name"], p["thumbnail"], p["cover"]) == ("Drive", "https://img/t.png", "https://img/t.png")
    p = await api.patch_user_playlist(1, pid, api.PlaylistPatchBody(thumbnail=""), bot)
    assert p["thumbnail"] is None and p["cover"] == "https://img/c.jpg"

    await api.remove_user_playlist_track(1, pid, bot, url=U)
    assert (await api.get_user_playlist(1, pid, bot))["tracks"] == []
    assert await api.delete_user_playlist(1, pid, bot) == {"deleted": True}
    assert (await api.get_user_music(1, bot))["playlists"] == []


async def test_playlist_names_are_checked() -> None:
    bot = _bot()
    await api.create_user_playlist(1, api.PlaylistCreateBody(name="Mix"), bot)
    for name, code in (("mix", 409), ("liked songs", 400), ("   ", 400), ("x" * 101, 400)):
        with pytest.raises(HTTPException) as e:
            await api.create_user_playlist(1, api.PlaylistCreateBody(name=name), bot)
        assert e.value.status_code == code, name


async def test_liked_songs_cannot_be_renamed_or_deleted() -> None:
    bot = _bot()
    await api.like_track(1, _track(), bot)
    liked_id = bot.db.playlists.lists[0]["id"]
    with pytest.raises(HTTPException):
        await api.patch_user_playlist(1, liked_id, api.PlaylistPatchBody(name="Other"), bot)
    with pytest.raises(HTTPException):
        await api.delete_user_playlist(1, liked_id, bot)
    # Its cover may still be customized.
    p = await api.patch_user_playlist(1, liked_id, api.PlaylistPatchBody(thumbnail="https://img/l.png"), bot)
    assert p["thumbnail"] == "https://img/l.png"


async def test_recent_uses_history_shape() -> None:
    bot = _bot()
    bot.db.music_plays.user_recent = AsyncMock(return_value=[{
        "title": "t", "author": "a", "uri": "u", "artwork": None, "length_ms": 1, "user_id": 1,
        "played_at": datetime.datetime(2026, 1, 1),
    }])
    item = (await api.get_user_music(1, bot))["recent"][0]
    assert item["user_id"] == "1" and item["played_at"].startswith("2026-01-01")
