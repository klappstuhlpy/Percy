"""Tests for :class:`~app.clients.tmdb.TMDBClient`.

Reuses the fake-session pattern from ``tests/test_clients.py`` (transport is not
re-tested here — that's :class:`~app.clients.base.BaseHTTPClient`'s job).
"""

from __future__ import annotations

import pytest

import config
from app.clients.tmdb import TMDBClient, image_url
from tests.test_clients import FakeResponse, FakeSession


def make_client(responses: list[FakeResponse], *, token: str | None = 'tok') -> TMDBClient:
    return TMDBClient(FakeSession(responses), token=token)  # type: ignore[arg-type]


@pytest.fixture
def no_configured_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blanks ``config.tmdb.token`` so ``token=None`` really means "no token".

    ``TMDBClient`` falls back to the configured token when none is passed, so a developer with
    ``TMDB_API_TOKEN`` set in ``.env`` would otherwise get a *configured* client here and these
    assertions would flip.
    """
    monkeypatch.setattr(config.tmdb, 'token', None)


async def test_bearer_header_sent_on_request() -> None:
    client = make_client([FakeResponse(json_data={})], token='secret-token')
    session = client.session

    await client.movie(1)

    _method, _url, kwargs = session.calls[0]  # type: ignore[attr-defined]
    assert kwargs['headers']['Authorization'] == 'Bearer secret-token'


async def test_no_authorization_header_without_token(no_configured_token: None) -> None:
    client = make_client([FakeResponse(json_data={})], token=None)
    session = client.session

    await client.movie(1)

    _method, _url, kwargs = session.calls[0]  # type: ignore[attr-defined]
    assert 'Authorization' not in kwargs['headers']


async def test_available_false_without_token(no_configured_token: None) -> None:
    client = make_client([], token=None)
    assert client.available is False


async def test_available_true_with_token() -> None:
    client = make_client([], token='tok')
    assert client.available is True


async def test_movie_hits_correct_url_with_append_to_response() -> None:
    session = FakeSession([FakeResponse(json_data={'id': 1})])
    client = TMDBClient(session, token='tok')  # type: ignore[arg-type]

    result = await client.movie(42)

    assert result == {'id': 1}
    _method, url, kwargs = session.calls[0]
    assert url == 'https://api.themoviedb.org/3/movie/42'
    assert kwargs['params']['append_to_response'] == 'external_ids'


async def test_tv_hits_correct_url_with_append_to_response() -> None:
    session = FakeSession([FakeResponse(json_data={'id': 7})])
    client = TMDBClient(session, token='tok')  # type: ignore[arg-type]

    result = await client.tv(7)

    assert result == {'id': 7}
    _method, url, kwargs = session.calls[0]
    assert url == 'https://api.themoviedb.org/3/tv/7'
    assert kwargs['params']['append_to_response'] == 'external_ids'


async def test_watch_providers_rejects_invalid_kind() -> None:
    client = make_client([])

    with pytest.raises(ValueError, match="kind must be 'movie' or 'tv'"):
        await client.watch_providers('film', 1)


async def test_search_rejects_invalid_kind() -> None:
    client = make_client([])

    with pytest.raises(ValueError, match="kind must be 'movie' or 'tv'"):
        await client.search('film', 'query')


async def test_search_movie_sends_year_param() -> None:
    session = FakeSession([FakeResponse(json_data={})])
    client = TMDBClient(session, token='tok')  # type: ignore[arg-type]

    await client.search('movie', 'q', year=2020)

    _method, _url, kwargs = session.calls[0]  # type: ignore[attr-defined]
    assert kwargs['params']['year'] == 2020
    assert 'first_air_date_year' not in kwargs['params']


async def test_search_tv_sends_first_air_date_year_param() -> None:
    session = FakeSession([FakeResponse(json_data={})])
    client = TMDBClient(session, token='tok')  # type: ignore[arg-type]

    await client.search('tv', 'q', year=2020)

    _method, _url, kwargs = session.calls[0]  # type: ignore[attr-defined]
    assert kwargs['params']['first_air_date_year'] == 2020
    assert 'year' not in kwargs['params']


async def test_configuration_caches_within_window() -> None:
    session = FakeSession([FakeResponse(json_data={'images': {}})])
    client = TMDBClient(session, token='tok')  # type: ignore[arg-type]

    first = await client.configuration()
    second = await client.configuration()

    assert first == second == {'images': {}}
    assert len(session.calls) == 1  # only one request issued


async def test_image_url_none_in_none_out() -> None:
    assert image_url(None) is None


async def test_image_url_builds_default_size() -> None:
    assert image_url('/x.jpg') == 'https://image.tmdb.org/t/p/w342/x.jpg'
