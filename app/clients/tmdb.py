"""TMDB (The Movie Database) client built on :class:`BaseHTTPClient`.

Metadata source for the universe watchlist feature (see ``migrations/V38__watchlist_core.sql``
and ``V39__watchlist_progress.sql``). This stays a thin transport wrapper: it returns TMDB's
decoded JSON verbatim and does **no** reshaping — mapping payloads onto database rows belongs
to a later ingest-service task, not here.

See https://developer.themoviedb.org/reference/intro/getting-started for the upstream API.
"""

from __future__ import annotations

from time import monotonic
from typing import Any, ClassVar

import aiohttp

import config
from app.clients.base import BaseHTTPClient

__all__ = ('TMDBClient', 'image_url')

#: Offline fallback for building image URLs. The authoritative base comes from
#: ``configuration()['images']['secure_base_url']``, but that requires a round trip;
#: this constant lets callers build a URL without waiting on/caching that response.
_IMAGE_BASE_URL = 'https://image.tmdb.org/t/p/'

#: How long a cached ``configuration()`` result stays fresh, in seconds.
_CONFIGURATION_TTL = 24 * 60 * 60

_SEARCH_KINDS = ('movie', 'tv')


def image_url(path: str | None, size: str = 'w342') -> str | None:
    """Build a TMDB CDN URL from a stored image path (``None`` in, ``None`` out)."""
    if not path:
        return None
    return f'{_IMAGE_BASE_URL}{size}/{path.lstrip("/")}'


class TMDBClient(BaseHTTPClient):
    """Minimal async client for the TMDB v3 REST API, authenticated with a v4 bearer token."""

    BASE_URL: ClassVar[str] = 'https://api.themoviedb.org/3/'
    # Metadata is replayable — a stale poster/title beats no page at all while the
    # circuit breaker is open, unlike e.g. Ollama's conversational responses.
    SERVE_STALE: ClassVar[bool] = True

    # TMDB's documented ceiling is ~40 requests/second (the legacy 40-per-10-seconds
    # window was retired in December 2019). The inherited 429 handling in BaseHTTPClient
    # already covers us here, so no extra client-side throttle is added.

    def __init__(self, session: aiohttp.ClientSession, *, token: str | None = None, timeout: float | None = None) -> None:
        super().__init__(session, name='TMDB')
        self.token: str | None = token if token is not None else config.tmdb.token
        self.timeout: float = timeout if timeout is not None else config.tmdb.timeout
        self._configuration_cache: dict[str, Any] | None = None
        self._configuration_cached_at: float = 0.0

    @property
    def available(self) -> bool:
        """Whether a bearer token is configured (mirrors ``AIService.available``)."""
        return self.token is not None

    def _headers(self) -> dict[str, str]:
        return {'Authorization': f'Bearer {self.token}'}

    def _params(self, **extra: Any) -> dict[str, Any]:
        params: dict[str, Any] = {'language': config.tmdb.language}
        params.update({k: v for k, v in extra.items() if v is not None})
        return params

    async def _get(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        return await self.fetch(
            'GET', url, params=params, headers=self._headers(),
            timeout=aiohttp.ClientTimeout(total=self.timeout),
        )

    async def configuration(self) -> Any:
        """``GET configuration`` — cached on the instance for 24h; TMDB's config barely changes."""
        now = monotonic()
        if self._configuration_cache is not None and (now - self._configuration_cached_at) < _CONFIGURATION_TTL:
            return self._configuration_cache

        data = await self._get('configuration', params=self._params())
        self._configuration_cache = data
        self._configuration_cached_at = now
        return data

    async def movie(self, movie_id: int) -> Any:
        """``GET movie/{id}`` with external IDs appended."""
        return await self._get(f'movie/{movie_id}', params=self._params(append_to_response='external_ids'))

    async def tv(self, tv_id: int) -> Any:
        """``GET tv/{id}`` with external IDs appended."""
        return await self._get(f'tv/{tv_id}', params=self._params(append_to_response='external_ids'))

    async def tv_season(self, tv_id: int, season: int) -> Any:
        """``GET tv/{id}/season/{n}``."""
        return await self._get(f'tv/{tv_id}/season/{season}', params=self._params())

    async def watch_providers(self, kind: str, item_id: int) -> Any:
        """``GET {kind}/{id}/watch/providers``; ``kind`` is ``'movie'`` or ``'tv'``."""
        if kind not in _SEARCH_KINDS:
            raise ValueError(f"kind must be 'movie' or 'tv', got {kind!r}")
        return await self._get(f'{kind}/{item_id}/watch/providers', params=self._params())

    async def collection(self, collection_id: int) -> Any:
        """``GET collection/{id}``."""
        return await self._get(f'collection/{collection_id}', params=self._params())

    async def search(self, kind: str, query: str, *, year: int | None = None) -> Any:
        """``GET search/{kind}``; ``kind`` is ``'movie'`` or ``'tv'``."""
        if kind not in _SEARCH_KINDS:
            raise ValueError(f"kind must be 'movie' or 'tv', got {kind!r}")
        return await self._get(f'search/{kind}', params=self._params(query=query, year=year))
