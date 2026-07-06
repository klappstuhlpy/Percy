"""Tests for the guild-scoped image methods on :class:`KlappstuhlMeClient`.

The client talks to ``aiohttp.ClientSession`` directly (it is not a
``BaseHTTPClient``), so these use a tiny fake session that records the calls and
hands back a canned response as an async context manager — no network involved.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.klappstuhl_me import KlappstuhlMeClient

BASE = "https://klappstuhl.me/api/v1"


class FakeResp:
    def __init__(self, payload: Any, *, ok: bool = True, status: int = 200) -> None:
        self._payload = payload
        self.ok = ok
        self.status = status

    async def json(self) -> Any:
        return self._payload

    async def text(self) -> str:
        return "error-body"

    async def __aenter__(self) -> FakeResp:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakeSession:
    """Records ``(method, url, kwargs)`` and returns a queued/canned response."""

    def __init__(self, payload: Any, *, ok: bool = True, status: int = 200) -> None:
        self._resp = FakeResp(payload, ok=ok, status=status)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResp:
        self.calls.append(("POST", url, kwargs))
        return self._resp

    def get(self, url: str, **kwargs: Any) -> FakeResp:
        self.calls.append(("GET", url, kwargs))
        return self._resp

    def delete(self, url: str, **kwargs: Any) -> FakeResp:
        self.calls.append(("DELETE", url, kwargs))
        return self._resp


def make_client(session: FakeSession, *, api_key: str | None = "secret") -> KlappstuhlMeClient:
    return KlappstuhlMeClient(session, api_key=api_key, base_url=BASE)  # type: ignore[arg-type]


async def test_upload_guild_images_targets_guild_path_and_returns_links() -> None:
    session = FakeSession({"errors": 0, "raw_links": [f"{BASE}/gallery/raw/abc.png"], "links": []})
    client = make_client(session)

    result = await client.upload_guild_images(123, [("banner.png", b"bytes")])

    assert result["raw_links"][0].endswith("abc.png")
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == f"{BASE}/guilds/123/images/upload"
    assert kwargs["headers"] == {"Authorization": "secret"}


async def test_upload_guild_images_forwards_expires_in() -> None:
    session = FakeSession({"errors": 0, "raw_links": [], "links": []})
    client = make_client(session)

    await client.upload_guild_images(7, [("a.png", b"x")], expires_in=3600)

    _method, _url, kwargs = session.calls[0]
    assert kwargs["params"] == {"expires_in": "3600"}


async def test_list_guild_images_targets_guild_path() -> None:
    session = FakeSession({"images": [], "total": 0})
    client = make_client(session)

    await client.list_guild_images(456)

    method, url, _kwargs = session.calls[0]
    assert method == "GET"
    assert url == f"{BASE}/guilds/456/images"


async def test_delete_guild_image_targets_guild_path() -> None:
    session = FakeSession({"file": "abc", "failed": False})
    client = make_client(session)

    await client.delete_guild_image(456, "abc")

    method, url, _kwargs = session.calls[0]
    assert method == "DELETE"
    assert url == f"{BASE}/guilds/456/images/abc"


async def test_guild_upload_requires_api_key() -> None:
    client = make_client(FakeSession({}), api_key=None)

    with pytest.raises(ValueError):
        await client.upload_guild_images(1, [("a.png", b"x")])


async def test_non_ok_response_raises() -> None:
    session = FakeSession({}, ok=False, status=403)
    client = make_client(session)

    with pytest.raises(Exception):
        await client.list_guild_images(1)
