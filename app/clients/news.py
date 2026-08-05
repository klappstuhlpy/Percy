"""RSS/Atom feed transport built on :class:`~app.clients.base.BaseHTTPClient`.

Deliberately thin: it fetches one feed URL and hands the body to
:func:`app.services.news.feeds.parse_feed`. No parsing, no tagging, no database -- the split
that keeps the parser unit-testable against fixture XML.

Two things it does that a bare ``session.get`` would not:

* **It identifies itself.** A real ``User-Agent`` naming Percy, its version and a contact URL,
  because feed publishers routinely block anonymous clients, and because the plan's §9 says to
  honour each source's terms -- which starts with being identifiable enough to be asked to
  stop.
* **It reports the status instead of swallowing it.** :meth:`NewsClient.fetch_feed` never
  raises: every outcome comes back as a :class:`FeedResponse` carrying a ``status`` string for
  ``news_sources.last_status``. A feed that 404s, times out or trips the circuit breaker has to
  be *visible* in that column, and an exception escaping a polling loop is how one dead source
  takes the rest of the run down with it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import aiohttp

import config
from app.clients.base import BaseHTTPClient, CircuitBreakerOpen, HTTPClientError, TransportError

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ('FeedResponse', 'NewsClient')

log = logging.getLogger(__name__)

#: Feeds are small and a slow one must not hold up the rest of a polling run.
DEFAULT_TIMEOUT = 20.0


@dataclass(frozen=True, slots=True)
class FeedResponse:
    """One fetch attempt: the body if there is one, and what to record either way.

    ``status`` goes straight into ``news_sources.last_status`` and is a string because the
    interesting failures have no HTTP status -- ``'timeout'``, ``'unreachable'``,
    ``'breaker-open'`` belong in the same column as ``'200'`` and ``'404'``.
    """

    url: str
    status: str
    body: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Whether a body came back to parse."""
        return self.body is not None


class _Body:
    """Marker wrapper so :meth:`NewsClient._read` can carry the status alongside the text."""

    __slots__ = ('status', 'text')

    def __init__(self, status: int, text: str) -> None:
        self.status = status
        self.text = text


class NewsClient(BaseHTTPClient):
    """Fetches RSS/Atom documents.

    ``SERVE_STALE`` is off: replaying a cached feed would re-offer stories the ingest has
    already stored (harmless but pointless, since the upsert is idempotent) while holding every
    polled feed's body in memory for the client's lifetime. A feed that cannot be reached is
    better reported than faked.
    """

    BASE_URL: ClassVar[str] = ''
    SERVE_STALE: ClassVar[bool] = False
    #: A feed is a scheduled poll, not a user-facing request -- three attempts, then report.
    MAX_RETRIES: ClassVar[int] = 3

    def __init__(self, session: aiohttp.ClientSession, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        super().__init__(session, name='News')
        self.timeout: float = timeout
        self.user_agent: str = f'Percy/{config.version} (+{config.website}; feed reader)'

    async def _read(self, response: aiohttp.ClientResponse) -> Any:
        """Reads the body as text, keeping the status with it.

        Overridden because the base client decodes JSON and returns only the payload, and this
        client needs both halves: XML must not go through a JSON decoder, and the caller has to
        write the status to ``news_sources.last_status`` even on success.
        """
        return _Body(response.status, await response.text())

    def _build_error(self, response: aiohttp.ClientResponse, payload: Any) -> HTTPClientError:
        """Builds the non-2xx error from the body *text*.

        The base implementation hands the payload to :class:`discord.HTTPException`, which
        expects a dict or a string -- and :meth:`_read` returns neither, so the wrapper is
        unwrapped here rather than left to the base class's fallback.
        """
        return super()._build_error(response, payload.text if isinstance(payload, _Body) else payload)

    @staticmethod
    def _status_of(exc: HTTPClientError) -> str:
        """The ``last_status`` string for a client failure.

        The two failure modes with no HTTP status get names of their own: they are the ones an
        operator most needs to tell apart from a 404 (the source is fine, *we* stopped asking).
        """
        if isinstance(exc, CircuitBreakerOpen):
            return 'breaker-open'
        if isinstance(exc, TransportError):
            return 'unreachable'
        return str(getattr(exc, 'status', '') or 'error')

    def _headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = {
            'User-Agent': self.user_agent,
            'Accept': 'application/atom+xml, application/rss+xml, application/xml;q=0.9, text/xml;q=0.8',
        }
        headers.update(extra or {})
        return headers

    async def fetch_feed(self, url: str, *, headers: Mapping[str, str] | None = None) -> FeedResponse:
        """Fetches one feed. Never raises -- every failure comes back as a status.

        The base client already handles 429s, transport backoff and the circuit breaker; what
        is added here is turning the exceptions it raises back into a recordable status, since
        a polling loop's job is to note that a source is broken and move on to the next one.
        """
        try:
            body = await self.fetch(
                'GET', url, headers=self._headers(headers),
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            )
        except HTTPClientError as exc:
            log.warning('Feed fetch failed for %s: %s', url, exc)
            return FeedResponse(url=url, status=self._status_of(exc), error=str(exc))
        except TimeoutError as exc:
            log.warning('Feed fetch timed out for %s after %.0fs', url, self.timeout)
            return FeedResponse(url=url, status='timeout', error=str(exc) or 'timeout')

        if not isinstance(body, _Body):
            # A stale cache hit (SERVE_STALE is off, so this should not happen) or a body the
            # override did not produce: report it rather than guessing at its shape.
            return FeedResponse(url=url, status='unknown', error='unexpected response body')
        return FeedResponse(url=url, status=str(body.status), body=body.text)
