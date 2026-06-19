"""Tests for :class:`~app.database.repositories.music.MusicSessionsRepository`.

These confirm the repository forwards the right SQL and arguments to the shared
pool helpers when persisting / restoring music sessions (the data behind the
24/7 feature and restart recovery).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from app.database.repositories import MusicSessionsRepository

if TYPE_CHECKING:
    from unittest.mock import MagicMock


def make_repo(mock_db: MagicMock) -> MusicSessionsRepository:
    return MusicSessionsRepository(mock_db)


async def test_get_session_fetches_for_guild(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    await repo.get_session(123)

    query, *params = mock_db.fetchrow.await_args.args
    assert "FROM music_sessions" in query
    assert "guild_id = $1" in query
    assert params == [123]


async def test_get_all_sessions_selects_all(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    await repo.get_all_sessions()

    query, *_ = mock_db.fetch.await_args.args
    assert "FROM music_sessions" in query
    assert "WHERE" not in query  # restore reads every row


async def test_upsert_session_serialises_tracks_and_upserts(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)
    tracks = [{"uri": "spotify:track:1", "title": "A", "requester_id": 7}]

    await repo.upsert_session(
        999,
        voice_channel_id=10,
        text_channel_id=20,
        volume=80,
        paused=True,
        queue_mode=2,
        shuffle=True,
        autoplay=0,
        always_on=True,
        always_on_mode="radio",
        always_on_source="https://stream.example/lofi",
        current_uri="spotify:track:0",
        position=12345,
        tracks=tracks,
    )

    query, *params = mock_db.execute.await_args.args
    assert "INSERT INTO music_sessions" in query
    assert "ON CONFLICT (guild_id) DO UPDATE" in query
    # guild_id first, tracks serialised to a JSON string for the ::jsonb cast.
    assert params[0] == 999
    assert json.loads(params[-1]) == tracks


async def test_delete_session_removes_guild_row(mock_db: MagicMock) -> None:
    repo = make_repo(mock_db)

    await repo.delete_session(555)

    query, *params = mock_db.execute.await_args.args
    assert "DELETE FROM" in query and "music_sessions" in query
    assert params == [555]
