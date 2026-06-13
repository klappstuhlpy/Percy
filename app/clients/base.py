from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, ClassVar

import aiohttp
import discord
import yarl

if TYPE_CHECKING:
    from collections.abc import Mapping

log = logging.getLogger(__name__)

__all__ = (
    'BaseHTTPClient',
    'CircuitBreakerOpen',
    'HTTPClientError',
    'StaleResult',
)


class HTTPClientError(discord.HTTPException):
    """Raised for a non-retryable HTTP failure from a :class:`BaseHTTPClient`.

    Subclasses :class:`discord.HTTPException`, so existing ``except discord.HTTPException``
    handlers (and the bot's error renderer) keep working unchanged.
    """


class CircuitBreakerOpen(HTTPClientError):
    """Raised when a request is rejected because the client's circuit breaker is open.

    The breaker opens after too many consecutive failures and fast-fails subsequent
    calls for a cooldown window, so a struggling upstream API never blocks the event
    loop behind a pile of timing-out requests.
    """

    def __init__(self, client_name: str, retry_after: float) -> None:
        self.client_name = client_name
        self.retry_after = retry_after
        super(discord.HTTPException, self).__init__(
            f'{client_name}: circuit breaker is open, retry in {retry_after:.1f}s'
        )


class StaleResult:
    """Wraps a cached response served when the upstream is unreachable.

    Consumers can check ``isinstance(result, StaleResult)`` to show a staleness
    disclaimer to the user. The actual payload is in ``.data``.
    """

    __slots__ = ("data", "age_seconds")

    def __init__(self, data: Any, age_seconds: float) -> None:
        self.data = data
        self.age_seconds = age_seconds

    def __repr__(self) -> str:
        return f"<StaleResult age={self.age_seconds:.0f}s>"


class BaseHTTPClient:
    """Shared async HTTP client with rate-limit handling, retries, and a circuit breaker.

    Concrete API clients (e.g. AniList, Marvel) subclass this and issue calls through
    :meth:`fetch`, which centralizes the resilience concerns every upstream needs:

    * **429 handling** — honours a ``Retry-After`` header (falling back to exponential
      backoff) and retries up to :attr:`MAX_RETRIES` times instead of hammering the API.
    * **Transport resilience** — :class:`aiohttp.ClientError` transport failures are
      retried with backoff rather than bubbling up on the first blip.
    * **Circuit breaker** — after :attr:`BREAKER_THRESHOLD` consecutive hard failures the
      breaker opens for :attr:`BREAKER_COOLDOWN` seconds and calls fast-fail with
      :class:`CircuitBreakerOpen`, protecting the event loop from a dead upstream.
    * **Standardized errors** — non-2xx responses raise :class:`HTTPClientError` (a
      :class:`discord.HTTPException`); subclasses refine the message via :meth:`_build_error`.

    Subclasses customise behaviour by overriding the hooks: :meth:`_should_retry`,
    :meth:`_retry_after`, and :meth:`_build_error`. Relative ``url`` arguments are joined
    onto :attr:`BASE_URL`; absolute URLs are used as-is.
    """

    #: Prepended to relative request URLs. Leave empty to require absolute URLs.
    BASE_URL: ClassVar[str] = ''
    #: Total attempts for a single logical request before giving up.
    MAX_RETRIES: ClassVar[int] = 5
    #: Consecutive hard failures that trip the circuit breaker.
    BREAKER_THRESHOLD: ClassVar[int] = 5
    #: Seconds the breaker stays open before allowing a trial request through.
    BREAKER_COOLDOWN: ClassVar[float] = 30.0
    #: Ceiling (seconds) for a single exponential-backoff sleep.
    MAX_BACKOFF: ClassVar[float] = 30.0

    #: Whether to serve stale cached responses when the circuit breaker is open.
    SERVE_STALE: ClassVar[bool] = True

    def __init__(self, session: aiohttp.ClientSession, *, name: str | None = None) -> None:
        self.session: aiohttp.ClientSession = session
        self.name: str = name or type(self).__name__
        self.log: logging.Logger = logging.getLogger(f'{__name__}.{self.name}')
        self._consecutive_failures: int = 0
        self._breaker_until: float = 0.0
        self._response_cache: dict[str, tuple[Any, float]] = {}

    # -- circuit breaker -------------------------------------------------

    def _now(self) -> float:
        return asyncio.get_event_loop().time()

    @property
    def breaker_open(self) -> bool:
        """Whether the breaker is currently rejecting requests."""
        return self._breaker_until > self._now()

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._breaker_until = 0.0

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.BREAKER_THRESHOLD:
            self._breaker_until = self._now() + self.BREAKER_COOLDOWN
            self.log.warning(
                'Circuit breaker opened after %d consecutive failures; cooling down for %.0fs',
                self._consecutive_failures, self.BREAKER_COOLDOWN,
            )

    # -- hooks (override in subclasses) ---------------------------------

    def _should_retry(self, response: aiohttp.ClientResponse, payload: Any) -> bool:
        """Return whether a (non-exceptional) response should be retried after a delay."""
        return response.status == 429

    def _retry_after(self, response: aiohttp.ClientResponse, attempt: int) -> float:
        """Seconds to wait before the next attempt; honours ``Retry-After`` then backs off."""
        header = response.headers.get('Retry-After')
        if header:
            try:
                return float(header)
            except ValueError:
                pass
        return self._backoff(attempt)

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff (1s, 2s, 4s, ...) capped at :attr:`MAX_BACKOFF`."""
        return min(2.0 ** (attempt - 1), self.MAX_BACKOFF)

    def _build_error(self, response: aiohttp.ClientResponse, payload: Any) -> HTTPClientError:
        """Construct the exception raised for a non-2xx response."""
        return HTTPClientError(response, payload)

    # -- request plumbing -----------------------------------------------

    def _build_url(self, url: str) -> yarl.URL | str:
        if url.startswith(('http://', 'https://')):
            return url
        if self.BASE_URL:
            return yarl.URL(self.BASE_URL) / url
        return url

    async def _read(self, response: aiohttp.ClientResponse) -> Any:
        """Decode a response body as JSON, falling back to raw text."""
        try:
            return await response.json()
        except (aiohttp.ContentTypeError, ValueError):
            return await response.text()

    async def fetch(
            self,
            method: str,
            url: str,
            *,
            params: Mapping[str, Any] | None = None,
            data: Any = None,
            json: Any = None,
            headers: Mapping[str, Any] | None = None,
            **kwargs: Any,
    ) -> Any:
        """Perform an HTTP request, returning the decoded JSON/text body.

        Applies rate-limit retries, transport-error backoff and the circuit breaker.
        Raises :class:`CircuitBreakerOpen` if the breaker is open, or
        :class:`HTTPClientError` (or a subclass) for a non-2xx response.
        """
        cache_key = f'{method}:{url}'

        if self.breaker_open:
            if self.SERVE_STALE and cache_key in self._response_cache:
                data_cached, cached_at = self._response_cache[cache_key]
                age = self._now() - cached_at
                self.log.info('Serving stale response for %s %s (age=%.0fs)', method, url, age)
                return StaleResult(data_cached, age)
            raise CircuitBreakerOpen(self.name, self._breaker_until - self._now())

        full_url = self._build_url(url)

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                async with self.session.request(
                        method, full_url, params=params, data=data, json=json, headers=headers, **kwargs,
                ) as response:
                    payload = await self._read(response)

                    if self._should_retry(response, payload):
                        if attempt >= self.MAX_RETRIES:
                            self._record_failure()
                            raise self._build_error(response, payload)
                        delay = self._retry_after(response, attempt)
                        self.log.warning(
                            'Rate limited on %s %s (status=%s); retry %d/%d in %.2fs',
                            method, full_url, response.status, attempt, self.MAX_RETRIES, delay,
                        )
                        await asyncio.sleep(delay)
                        continue

                    if 200 <= response.status < 300:
                        self._record_success()
                        if method.upper() == 'GET':
                            self._response_cache[cache_key] = (payload, self._now())
                        return payload

                    self._record_failure()
                    raise self._build_error(response, payload)

            except aiohttp.ClientError as exc:
                if attempt >= self.MAX_RETRIES:
                    self.log.warning('Transport error on %s %s, giving up: %r', method, full_url, exc)
                    self._record_failure()
                    raise
                delay = self._backoff(attempt)
                self.log.warning(
                    'Transport error on %s %s: %r; retry %d/%d in %.2fs',
                    method, full_url, exc, attempt, self.MAX_RETRIES, delay,
                )
                await asyncio.sleep(delay)

        # Unreachable: the loop either returns, raises, or sleeps and continues.
        raise RuntimeError('unreachable: fetch retry loop exited without a result')
