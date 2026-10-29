from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.clients.base import BaseHTTPClient, HTTPClientError
from app.utils import cache

if TYPE_CHECKING:
    import aiohttp

API_ENDPOINT = 'https://graphql.anilist.co'

#: Seconds an identical query is served from memory. AniList allows 90 requests a minute (and has
#: run degraded at 30 for long stretches), so repeat lookups and card navigation must not each cost one.
CACHE_TTL = 300

# The list rows only need enough to render one line and an option; the full card is fetched by id.
_MEDIA_ROW = '''
  id
  type
  format
  status(version: 2)
  title { romaji english }
  startDate { year }
  episodes
  chapters
  meanScore
  isAdult
'''

_MEDIA_SEARCH_QUERY = '''
query ($page: Int, $perPage: Int, $type: MediaType, $search: String, $season: MediaSeason,
       $seasonYear: Int, $status: [MediaStatus], $sort: [MediaSort]) {
  Page(page: $page, perPage: $perPage) {
    media(type: $type, search: $search, season: $season, seasonYear: $seasonYear, status_in: $status,
          isAdult: false, sort: $sort) {
''' + _MEDIA_ROW + '''
    }
  }
}
'''

_MEDIA_DETAIL_QUERY = '''
query ($id: Int) {
  Media(id: $id) {
    id
    type
    format
    status(version: 2)
    description
    title { romaji english }
    startDate { year month day }
    endDate { year month day }
    episodes
    duration
    chapters
    volumes
    source(version: 3)
    coverImage { large color }
    genres
    meanScore
    popularity
    favourites
    isAdult
    siteUrl
    studios(isMain: true) { nodes { name } }
    nextAiringEpisode { airingAt episode }
    relations {
      edges {
        relationType(version: 2)
        node { id type format isAdult title { romaji } }
      }
    }
    recommendations(sort: RATING_DESC, page: 1, perPage: 10) {
      nodes { mediaRecommendation { id type format isAdult title { romaji } } }
    }
  }
}
'''

_CHARACTER_QUERY = '''
query ($page: Int, $perPage: Int, $search: String) {
  Page(page: $page, perPage: $perPage) {
    characters(search: $search) {
      id
      name { full native alternative alternativeSpoiler }
      image { large }
      description
      gender
      dateOfBirth { year month day }
      age
      favourites
      siteUrl
      media(sort: POPULARITY_DESC, page: 1, perPage: 10) {
        nodes { id type format isAdult title { romaji } }
      }
    }
  }
}
'''

_STAFF_QUERY = '''
query ($page: Int, $perPage: Int, $search: String) {
  Page(page: $page, perPage: $perPage) {
    staff(search: $search) {
      id
      name { full native alternative }
      languageV2
      image { large }
      description
      primaryOccupations
      gender
      dateOfBirth { year month day }
      age
      homeTown
      favourites
      siteUrl
      staffMedia(sort: POPULARITY_DESC, page: 1, perPage: 10) {
        nodes { id type format isAdult title { romaji } }
      }
    }
  }
}
'''

_STUDIO_QUERY = '''
query ($page: Int, $perPage: Int, $search: String) {
  Page(page: $page, perPage: $perPage) {
    studios(search: $search) {
      id
      name
      isAnimationStudio
      favourites
      siteUrl
      media(sort: POPULARITY_DESC, isMain: true, page: 1, perPage: 10) {
        nodes { id type format isAdult title { romaji } startDate { year } }
      }
    }
  }
}
'''

_AIRING_QUERY = '''
query ($page: Int, $perPage: Int, $from: Int, $to: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { hasNextPage }
    airingSchedules(airingAt_greater: $from, airingAt_lesser: $to, sort: TIME) {
      airingAt
      episode
      media {
''' + _MEDIA_ROW + '''
        siteUrl
        coverImage { large }
        studios(isMain: true) { nodes { id name } }
      }
    }
  }
}
'''


class AniListClient(BaseHTTPClient):
    """Read-only GraphQL client for AniList, hardened by :class:`~app.clients.BaseHTTPClient`.

    Rate-limit retries, transport-error backoff and circuit-breaking are inherited. Every
    query is cached for :data:`CACHE_TTL` seconds, and AniList's 404 (unknown id) and 500
    (its answer for some transient or empty results) both come back as ``None``.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        super().__init__(session, name='AniList')

    @cache.cache(maxsize=CACHE_TTL, strategy=cache.Strategy.TIMED)
    async def _request(self, query: str, **variables: Any) -> dict[str, Any] | None:
        try:
            payload = await self.fetch('POST', API_ENDPOINT, json={'query': query, 'variables': variables})
        except HTTPClientError as exc:
            if exc.status in (404, 500):
                return None
            raise
        return payload.get('data') if isinstance(payload, dict) else None

    async def _page(self, query: str, key: str, **variables: Any) -> list[dict[str, Any]]:
        data = await self._request(query, **variables)
        return list((data or {}).get('Page', {}).get(key) or [])

    async def search_media(self, **variables: Any) -> list[dict[str, Any]]:
        """Media rows (see ``_MEDIA_ROW``) for a search, season or sort; adult titles are never returned."""
        return await self._page(_MEDIA_SEARCH_QUERY, 'media', **variables)

    async def media(self, media_id: int) -> dict[str, Any] | None:
        """One media with everything its card shows, including relations and recommendations."""
        data = await self._request(_MEDIA_DETAIL_QUERY, id=media_id)
        return (data or {}).get('Media')

    async def characters(self, search: str, *, limit: int) -> list[dict[str, Any]]:
        return await self._page(_CHARACTER_QUERY, 'characters', page=1, perPage=limit, search=search)

    async def staff(self, search: str, *, limit: int) -> list[dict[str, Any]]:
        return await self._page(_STAFF_QUERY, 'staff', page=1, perPage=limit, search=search)

    async def studios(self, search: str, *, limit: int) -> list[dict[str, Any]]:
        return await self._page(_STUDIO_QUERY, 'studios', page=1, perPage=limit, search=search)

    async def airing(self, start: int, end: int) -> list[dict[str, Any]]:
        """Episodes airing in ``[start, end)`` (unix seconds), soonest first, adult titles dropped."""
        rows: list[dict[str, Any]] = []
        # ponytail: 4 pages (200 episodes) covers a busy week; raise it if the list ever ends early
        for page in range(1, 5):
            data = await self._request(_AIRING_QUERY, page=page, perPage=50, **{'from': start, 'to': end})
            block = (data or {}).get('Page') or {}
            rows.extend(block.get('airingSchedules') or [])
            if not (block.get('pageInfo') or {}).get('hasNextPage'):
                break
        return [row for row in rows if not (row.get('media') or {}).get('isAdult')]
