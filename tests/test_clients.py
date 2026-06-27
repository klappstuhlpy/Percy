"""Tests for :class:`~app.clients.base.BaseHTTPClient`.

These exercise the shared resilience behaviour every API client inherits — rate-limit
retries, transport-error backoff, standardized errors and the circuit breaker — using a
lightweight fake :class:`aiohttp.ClientSession` so no network or real sleeping happens.
"""

from __future__ import annotations

from typing import Any

import aiohttp
import pytest

from app.clients import BaseHTTPClient, CircuitBreakerOpen, HTTPClientError
from app.clients.ollama import OllamaClient, OllamaResponseError


class FakeResponse:
    """Minimal stand-in for an ``aiohttp`` response usable as an async context manager."""

    def __init__(
            self,
            *,
            status: int = 200,
            json_data: Any = None,
            headers: dict[str, str] | None = None,
            json_error: bool = False,
            raise_on_enter: BaseException | None = None,
            reason: str = 'Fake',
    ) -> None:
        self.status = status
        self.reason = reason
        self._json_data = json_data
        self.headers = headers or {}
        self._json_error = json_error
        self._raise_on_enter = raise_on_enter

    async def json(self) -> Any:
        if self._json_error:
            raise ValueError('not json')
        return self._json_data

    async def text(self) -> str:
        return 'raw-text-body'

    async def __aenter__(self) -> FakeResponse:
        if self._raise_on_enter is not None:
            raise self._raise_on_enter
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakeSession:
    """Hands out queued :class:`FakeResponse` objects and records the calls made."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: Any, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, str(url), kwargs))
        return self._responses.pop(0)


class Client(BaseHTTPClient):
    """Concrete client with small limits so retry/breaker paths are quick to drive."""

    BASE_URL = 'https://example.test/api/'
    MAX_RETRIES = 3
    BREAKER_THRESHOLD = 2
    BREAKER_COOLDOWN = 60.0


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make backoff sleeps instant so retry tests don't actually wait."""
    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr('app.clients.base.asyncio.sleep', fake_sleep)


def make_client(responses: list[FakeResponse]) -> Client:
    return Client(FakeSession(responses))  # type: ignore[arg-type]


async def test_fetch_returns_json_on_success() -> None:
    client = make_client([FakeResponse(json_data={'ok': True})])

    result = await client.fetch('GET', 'comics')

    assert result == {'ok': True}


async def test_relative_url_joined_onto_base_url() -> None:
    session = FakeSession([FakeResponse(json_data={})])
    client = Client(session)  # type: ignore[arg-type]

    await client.fetch('GET', 'comics')

    _method, url, _kwargs = session.calls[0]
    assert url == 'https://example.test/api/comics'


async def test_absolute_url_used_verbatim() -> None:
    session = FakeSession([FakeResponse(json_data={})])
    client = Client(session)  # type: ignore[arg-type]

    await client.fetch('POST', 'https://other.test/graphql')

    _method, url, _kwargs = session.calls[0]
    assert url == 'https://other.test/graphql'


async def test_fetch_falls_back_to_text_when_not_json() -> None:
    client = make_client([FakeResponse(json_data=None, json_error=True)])

    result = await client.fetch('GET', 'comics')

    assert result == 'raw-text-body'


async def test_fetch_raises_http_client_error_on_4xx() -> None:
    client = make_client([FakeResponse(status=404, json_data={'message': 'nope'})])

    with pytest.raises(HTTPClientError) as exc_info:
        await client.fetch('GET', 'comics')

    assert exc_info.value.status == 404


async def test_fetch_retries_on_429_then_succeeds() -> None:
    session = FakeSession([
        FakeResponse(status=429, headers={'Retry-After': '0'}),
        FakeResponse(status=200, json_data={'ok': True}),
    ])
    client = Client(session)  # type: ignore[arg-type]

    result = await client.fetch('GET', 'comics')

    assert result == {'ok': True}
    assert len(session.calls) == 2  # one retry


async def test_fetch_429_exhausts_retries_and_raises() -> None:
    session = FakeSession([FakeResponse(status=429) for _ in range(Client.MAX_RETRIES)])
    client = Client(session)  # type: ignore[arg-type]

    with pytest.raises(HTTPClientError):
        await client.fetch('GET', 'comics')

    assert len(session.calls) == Client.MAX_RETRIES


async def test_transport_error_retried_then_succeeds() -> None:
    session = FakeSession([
        FakeResponse(raise_on_enter=aiohttp.ClientError('boom')),
        FakeResponse(status=200, json_data={'ok': True}),
    ])
    client = Client(session)  # type: ignore[arg-type]

    result = await client.fetch('GET', 'comics')

    assert result == {'ok': True}
    assert len(session.calls) == 2


async def test_transport_error_exhausts_retries_and_raises() -> None:
    session = FakeSession([
        FakeResponse(raise_on_enter=aiohttp.ClientError('boom')) for _ in range(Client.MAX_RETRIES)
    ])
    client = Client(session)  # type: ignore[arg-type]

    with pytest.raises(aiohttp.ClientError):
        await client.fetch('GET', 'comics')


async def test_breaker_opens_after_threshold_and_fast_fails() -> None:
    # Each 4xx is one hard failure; BREAKER_THRESHOLD of them trips the breaker.
    responses = [FakeResponse(status=500, json_data={}) for _ in range(Client.BREAKER_THRESHOLD)]
    session = FakeSession(responses)
    client = Client(session)  # type: ignore[arg-type]

    for _ in range(Client.BREAKER_THRESHOLD):
        with pytest.raises(HTTPClientError):
            await client.fetch('GET', 'comics')

    assert client.breaker_open
    calls_before = len(session.calls)

    # Next call must fast-fail without touching the session.
    with pytest.raises(CircuitBreakerOpen):
        await client.fetch('GET', 'comics')
    assert len(session.calls) == calls_before


async def test_success_resets_failure_counter() -> None:
    session = FakeSession([
        FakeResponse(status=500, json_data={}),
        FakeResponse(status=200, json_data={'ok': True}),
        FakeResponse(status=500, json_data={}),
    ])
    client = Client(session)  # type: ignore[arg-type]

    with pytest.raises(HTTPClientError):
        await client.fetch('GET', 'comics')
    assert client._consecutive_failures == 1

    await client.fetch('GET', 'comics')
    assert client._consecutive_failures == 0  # success cleared it

    with pytest.raises(HTTPClientError):
        await client.fetch('GET', 'comics')
    assert client._consecutive_failures == 1  # counts from zero again, breaker stays closed
    assert not client.breaker_open


async def test_should_retry_hook_can_back_off_on_custom_signal() -> None:
    class RemainingAwareClient(Client):
        def _should_retry(self, response: Any, payload: Any) -> bool:
            return response.status == 429 or response.headers.get('X-Ratelimit-Remaining') == '0'

    session = FakeSession([
        FakeResponse(status=200, json_data={'stale': True}, headers={'X-Ratelimit-Remaining': '0'}),
        FakeResponse(status=200, json_data={'fresh': True}, headers={'X-Ratelimit-Remaining': '49'}),
    ])
    client = RemainingAwareClient(session)  # type: ignore[arg-type]

    result = await client.fetch('GET', 'comics')

    assert result == {'fresh': True}
    assert len(session.calls) == 2


# -- OllamaClient ----------------------------------------------------------------


async def test_ollama_chat_returns_message_content() -> None:
    session = FakeSession([FakeResponse(json_data={'message': {'content': '  hi there  '}})])
    client = OllamaClient(session, host='https://ai.example')  # type: ignore[arg-type]

    result = await client.chat([{'role': 'user', 'content': 'x'}])

    assert result == 'hi there'  # stripped
    _method, url, _kwargs = session.calls[0]
    assert url == 'https://ai.example/api/chat'


async def test_ollama_chat_json_mode_sets_format() -> None:
    session = FakeSession([FakeResponse(json_data={'message': {'content': '{}'}})])
    client = OllamaClient(session)  # type: ignore[arg-type]

    await client.chat([{'role': 'user', 'content': 'x'}], json_mode=True)

    _method, _url, kwargs = session.calls[0]
    assert kwargs['json']['format'] == 'json'


async def test_ollama_chat_raises_on_missing_content() -> None:
    session = FakeSession([FakeResponse(json_data={'unexpected': True})])
    client = OllamaClient(session)  # type: ignore[arg-type]

    with pytest.raises(OllamaResponseError):
        await client.chat([{'role': 'user', 'content': 'x'}])


async def test_ollama_version_probe_hits_version_endpoint() -> None:
    session = FakeSession([FakeResponse(json_data={'version': '0.3.0'})])
    client = OllamaClient(session)  # type: ignore[arg-type]

    assert await client.version() == '0.3.0'
    _method, url, _kwargs = session.calls[0]
    assert url.endswith('/api/version')
