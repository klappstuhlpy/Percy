"""Deezer worldwide chart client.

The global "what is everyone listening to" shelf of the dashboard music app. Deezer's chart API is
public and key-less (Spotify's editorial Top 50 playlists are closed to new API apps, and YouTube's
charts need a key), so it is the one source that works with zero configuration.

Entries carry no Lavalink uri: the dashboard plays them as a ``title artist`` search, the same
fallback any uri-less card uses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from app.clients.base import BaseHTTPClient, StaleResult

if TYPE_CHECKING:
    import aiohttp

__all__ = ('DeezerChartClient', 'chart_tracks')


def chart_tracks(payload: Any) -> list[dict[str, Any]]:
    """Deezer ``/chart/0/tracks`` payload -> ``ChartTrack`` dicts; malformed rows are skipped."""
    rows = payload.get('data') if isinstance(payload, dict) else None
    out: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get('title'):
            continue
        artist, album = row.get('artist') or {}, row.get('album') or {}
        duration = row.get('duration')
        out.append({
            'title': str(row['title']),
            'author': artist.get('name') if isinstance(artist, dict) else None,
            'artwork': album.get('cover_medium') if isinstance(album, dict) else None,
            'length_ms': int(duration) * 1000 if isinstance(duration, int) else None,
            'url': row.get('link'),
        })
    return out


class DeezerChartClient(BaseHTTPClient):
    BASE_URL: ClassVar[str] = 'https://api.deezer.com/'

    def __init__(self, session: aiohttp.ClientSession) -> None:
        super().__init__(session, name='DeezerCharts')

    async def top_tracks(self, limit: int = 12) -> list[dict[str, Any]]:
        """The worldwide top tracks; raises :class:`HTTPClientError` when Deezer is unreachable."""
        payload = await self.fetch('GET', 'chart/0/tracks', params={'limit': limit})
        if isinstance(payload, StaleResult):
            payload = payload.data
        return chart_tracks(payload)
