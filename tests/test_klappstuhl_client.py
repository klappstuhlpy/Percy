"""Tests for :class:`KlappstuhlMeClient`, Percy's extension of ``klappstuhl.Client``.

The base wrapper owns the HTTP transport, so these drive a tiny fake
``aiohttp.ClientSession`` (matching what ``klappstuhl.http.HTTPClient`` calls:
``session.request(method, url, ...)`` → an async context manager exposing
``read()`` / ``status`` / ``headers``). No network is involved.

The Percy-only behaviour under test is the per-guild ``images:guild`` key flow:
with a ``provision_token`` the client fetches (get-or-creates) a narrow per-guild
key from the host and caches it; a personal ``api_key`` is only a legacy
fallback. Discord-native inputs (``(filename, bytes)`` tuples, ``bytes``) are
coerced to ``klappstuhl.File`` before upload.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from klappstuhl.errors import Forbidden

from app.services.klappstuhl_me import KlappstuhlMeClient

ROOT = "https://klappstuhl.me"
API = f"{ROOT}/api/v1"


class FakeResp:
    """An async-context-manager response mimicking the bits HTTPClient reads."""

    def __init__(self, payload: Any = None, *, status: int = 200) -> None:
        self._body = json.dumps(payload).encode() if payload is not None else b""
        self.status = status
        self.headers: dict[str, str] = {}

    async def read(self) -> bytes:
        return self._body

    async def __aenter__(self) -> FakeResp:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakeSession:
    """Records ``(method, url, kwargs)`` and returns a canned response.

    ``routes`` maps a URL substring to a specific :class:`FakeResp` so a test can
    give the provision endpoint a different reply than the gallery endpoints;
    anything unmatched falls back to the default response.
    """

    closed = False

    def __init__(
        self,
        payload: Any = None,
        *,
        status: int = 200,
        routes: dict[str, FakeResp] | None = None,
    ) -> None:
        self._resp = FakeResp(payload, status=status)
        self._routes = routes or {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResp:
        self.calls.append((method, url, kwargs))
        for fragment, resp in self._routes.items():
            if fragment in url:
                return resp
        return self._resp


def make_client(
    session: FakeSession,
    *,
    api_key: str | None = "secret",
    provision_token: str | None = None,
) -> KlappstuhlMeClient:
    return KlappstuhlMeClient(  # type: ignore[arg-type]
        session, api_key=api_key, provision_token=provision_token, base_url=ROOT
    )


def _auth(kwargs: dict[str, Any]) -> str | None:
    return kwargs["headers"]["Authorization"]


# ── legacy personal-key fallback (no provision token) ────────────────────────


async def test_upload_guild_images_targets_guild_path_and_returns_links() -> None:
    session = FakeSession({"total": 1, "errors": 0, "raw_links": [f"{ROOT}/gallery/raw/abc.png"], "links": []})
    client = make_client(session)

    result = await client.upload_guild_images(123, ("banner.png", b"bytes"))

    assert result.raw_links[0].endswith("abc.png")
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == f"{API}/guilds/123/images/upload"
    assert _auth(kwargs) == "secret"


async def test_upload_guild_images_forwards_expires_in() -> None:
    session = FakeSession({"total": 0, "errors": 0, "raw_links": [], "links": []})
    client = make_client(session)

    await client.upload_guild_images(7, b"x", expires_in=3600)

    _method, _url, kwargs = session.calls[0]
    assert kwargs["params"] == {"expires_in": "3600"}


async def test_list_guild_images_targets_guild_path() -> None:
    session = FakeSession({"images": [], "total": 0})
    client = make_client(session)

    await client.list_guild_images(456)

    method, url, _kwargs = session.calls[0]
    assert method == "GET"
    assert url == f"{API}/guilds/456/images"


async def test_delete_guild_image_targets_guild_path() -> None:
    session = FakeSession({"file": "abc", "failed": False})
    client = make_client(session)

    result = await client.delete_guild_image(456, "abc")

    assert result.file == "abc"
    method, url, _kwargs = session.calls[0]
    assert method == "DELETE"
    assert url == f"{API}/guilds/456/images/abc"


async def test_guild_upload_requires_a_credential() -> None:
    # No provision token and no personal key → nothing can authorise the call.
    client = make_client(FakeSession({}), api_key=None)

    with pytest.raises(ValueError):
        await client.upload_guild_images(1, b"x")


async def test_unavailable_when_no_credentials() -> None:
    client = make_client(FakeSession({}), api_key=None)
    assert client.available is False


async def test_forbidden_response_raises_without_provision_token() -> None:
    # Legacy key that lacks the scope → 403 surfaces (no re-provision possible).
    session = FakeSession({"error": "no perms"}, status=403)
    client = make_client(session)

    with pytest.raises(Forbidden):
        await client.list_guild_images(1)


# ── per-guild provisioning (preferred path) ──────────────────────────────────


async def test_provisions_and_uses_per_guild_key() -> None:
    session = FakeSession(
        {"images": [], "total": 0},
        routes={"/provision-key": FakeResp({"token": "guild-42-key"})},
    )
    client = make_client(session, api_key=None, provision_token="service-token")

    await client.list_guild_images(42)

    # First call provisions the key with the service token as the bearer.
    prov_method, prov_url, prov_kwargs = session.calls[0]
    assert prov_method == "POST"
    assert prov_url == f"{API}/guilds/42/provision-key"
    assert _auth(prov_kwargs) == "service-token"

    # The gallery call then uses the minted per-guild key, not the service token.
    gallery_method, gallery_url, gallery_kwargs = session.calls[1]
    assert gallery_method == "GET"
    assert gallery_url == f"{API}/guilds/42/images"
    assert _auth(gallery_kwargs) == "guild-42-key"


async def test_per_guild_key_is_cached() -> None:
    session = FakeSession(
        {"images": [], "total": 0},
        routes={"/provision-key": FakeResp({"token": "cached-key"})},
    )
    client = make_client(session, api_key=None, provision_token="service-token")

    await client.list_guild_images(9)
    await client.list_guild_images(9)

    # Exactly one provision call across two gallery reads (the key was cached).
    provision_calls = [c for c in session.calls if c[1].endswith("/provision-key")]
    assert len(provision_calls) == 1


async def test_revoked_key_is_reprovisioned_once() -> None:
    # The gallery endpoint rejects the first (stale) key with 403, then the
    # client re-provisions and the second attempt is allowed to surface.
    class SequencedSession(FakeSession):
        def __init__(self) -> None:
            super().__init__(routes={"/provision-key": FakeResp({"token": "fresh-key"})})
            self._gallery_status = [403, 200]

        def request(self, method: str, url: str, **kwargs: Any) -> FakeResp:
            self.calls.append((method, url, kwargs))
            if "/provision-key" in url:
                return self._routes["/provision-key"]
            status = self._gallery_status.pop(0) if self._gallery_status else 200
            return FakeResp({"images": [], "total": 0}, status=status)

    session = SequencedSession()
    client = make_client(session, api_key=None, provision_token="service-token")

    await client.list_guild_images(5)

    # Two provision calls: initial + one re-provision after the 403.
    provision_calls = [c for c in session.calls if c[1].endswith("/provision-key")]
    assert len(provision_calls) == 2
