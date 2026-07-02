"""LRCLIB lyrics client.

LRCLIB (https://lrclib.net) is a free, key-less, rate-limit-free community lyrics
database that serves both plain and time-synced (LRC) lyrics. It is the synced
source for the music cog's live-lyrics feature; Genius (scraped) remains a
plain-text fallback in the cog.

Subclasses :class:`BaseHTTPClient` for the shared retry/backoff/circuit-breaker
plumbing. A 404 from LRCLIB means "no lyrics for this query" -- a normal outcome,
not an upstream fault -- so it is mapped to ``None`` without tripping the breaker.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from app.clients.base import BaseHTTPClient, CircuitBreakerOpen, HTTPClientError, StaleResult

if TYPE_CHECKING:
    import aiohttp

__all__ = ("LRCLibClient",)


class LRCLibClient(BaseHTTPClient):
    """Thin async client over the LRCLIB public API."""

    BASE_URL: ClassVar[str] = "https://lrclib.net/"
    # LRCLIB asks clients to identify themselves via User-Agent.
    HEADERS: ClassVar[dict[str, str]] = {"User-Agent": "Percy-Bot (https://klappstuhl.me)"}
    # Lyrics are keyed per song, so a cached response served for a *different* query would
    # be wrong (and the shared cache key ignores query params). Never serve stale here.
    SERVE_STALE: ClassVar[bool] = False

    def __init__(self, session: aiohttp.ClientSession) -> None:
        super().__init__(session, name="LRCLib")

    async def get_lyrics(
        self,
        *,
        track: str,
        artist: str,
        album: str | None = None,
        duration: int | None = None,
    ) -> dict[str, Any] | None:
        """Fetch the best-matching record via ``/api/get`` (exact signature match).

        ``duration`` is in **seconds**; LRCLIB uses it to disambiguate releases.
        Returns the raw record (with ``syncedLyrics`` / ``plainLyrics``) or ``None``
        when nothing matches.
        """
        params: dict[str, Any] = {"track_name": track, "artist_name": artist}
        if album:
            params["album_name"] = album
        if duration:
            params["duration"] = int(duration)
        return await self._get_optional("api/get", params)

    async def search_lyrics(self, *, track: str, artist: str | None = None) -> list[dict[str, Any]]:
        """Fuzzy-search records via ``/api/search``. Returns ``[]`` when nothing matches."""
        params: dict[str, Any] = {"track_name": track}
        if artist:
            params["artist_name"] = artist
        result = await self._get_optional("api/search", params)
        return result if isinstance(result, list) else []

    async def _get_optional(self, path: str, params: dict[str, Any]) -> Any:
        """GET that treats a 404 (and any client error) as "no result" -> ``None``."""
        try:
            return await self.fetch("GET", path, params=params, headers=self.HEADERS)
        except HTTPClientError as exc:
            if getattr(exc, "status", None) == 404:
                # Not-found is an expected answer, not a fault: undo the failure the
                # base client recorded so a run of lyric-less songs can't open the breaker.
                self._record_success()
                return None
            raise
