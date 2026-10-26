"""Track lookups shared by the music commands and the internal API.

Lavalink has no artist or album endpoint, so an "artist" is a search filtered by author and an
"album" is a link that resolves to a playlist. Both the dashboard (``/music/artist|album``) and the
``discover`` commands build their pages from these, so the two never disagree on what an artist is.
"""

from __future__ import annotations

from typing import Any

import wavelink

from .player import Player

__all__ = ("album", "artist_tracks", "flatten")


def flatten(result: Any) -> list[wavelink.Playable]:
    """A search result (track, playlist, list or a SearchReturn marker) as a plain track list."""
    if isinstance(result, wavelink.Playlist):
        return list(result.tracks)
    if isinstance(result, wavelink.Playable):
        return [result]
    return list(result) if isinstance(result, list) else []


async def artist_tracks(name: str, url: str | None = None) -> tuple[list[wavelink.Playable], str | None]:
    """An artist's tracks and artwork.

    With ``url`` (LavaSrc artist links: Spotify, Deezer, Apple Music) the link resolves to a playlist of
    the artist's top tracks. Otherwise (or when that yields nothing) the name is searched on Spotify,
    then YouTube Music, keeping tracks whose author contains the name.
    """
    tracks: list[wavelink.Playable] = []
    artwork: str | None = None
    if url:
        result = await Player.search(url)
        if isinstance(result, wavelink.Playlist):
            tracks, artwork = list(result.tracks), result.artwork
    if not tracks and name:
        wanted = name.casefold()
        for source in ("spsearch", wavelink.TrackSource.YouTubeMusic):
            found = flatten(await Player.search(name, source=source))
            tracks = [t for t in found if wanted in (t.author or "").casefold()]
            if tracks:
                break
    return tracks, artwork


async def album(url: str) -> wavelink.Playlist | None:
    """An album/single/playlist link loaded as a playlist, or ``None`` when it does not resolve to one."""
    result = await Player.search(url)
    return result if isinstance(result, wavelink.Playlist) and result.tracks else None
