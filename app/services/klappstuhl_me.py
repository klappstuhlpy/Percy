"""Percy's klappstuhl.me client.

This is a thin, Percy-only extension of the public :mod:`klappstuhl` wrapper
(the ``klappstuhl.py`` package). The base :class:`klappstuhl.Client` already
covers every general endpoint — uploads, guild galleries, media, render, scan,
short links, pastes, QR, unfurl — so Percy does **not** re-implement a bespoke
HTTP client. It layers on three deployment-specific concerns:

* **Per-guild key provisioning.** Percy never holds a personal API key for
  gallery calls. It presents a shared ``provision_token`` to the host's
  internal ``POST /guilds/{id}/provision-key`` endpoint, which mints
  (get-or-creates) a narrow ``images:guild`` key for that guild. Those keys are
  cached in-process and used for every subsequent gallery call; a legacy
  personal ``api_key`` is only a fallback.
* **Discord-native file coercion.** Gallery uploads accept ``discord.File`` /
  ``discord.Attachment`` (and ``(filename, bytes)`` tuples) and convert them to
  :class:`klappstuhl.File`, so cogs can hand Percy objects straight through.
* **Account-key routing for account-scoped features.** Short links, pastes, QR,
  and unfurl are *not* per-guild, so they cannot use a provisioned
  ``images:guild`` key. Percy routes them through a separate client bound to a
  real personal ``api_key`` (carrying the ``links:*`` / ``pastes:*`` /
  ``images:read`` scopes); :attr:`account_available` reports whether one is set.

Everything else is inherited from :class:`klappstuhl.Client` unchanged.
"""
from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Any

import discord
from klappstuhl import Client, File
from klappstuhl.client import _bare_id
from klappstuhl.errors import Forbidden, Unauthorized
from klappstuhl.file import resolve_file, FileInput
from klappstuhl.http import DEFAULT_BASE_URL
from klappstuhl.models import DeleteResult, UploadResult

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import aiohttp

# A non-empty sentinel handed to the base client when the hoster is unconfigured
# (the base client requires *some* token). It is never sent: every guild call
# checks :attr:`KlappstuhlMeClient.available` first.
_UNCONFIGURED = "unconfigured"


class KlappstuhlClient(Client):
    """Percy's extension of :class:`klappstuhl.Client` for public api use (see the module docstring)."""

    def __init__(
        self,
        api_token: str | None = None,
        *,
        session: aiohttp.ClientSession | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        # The base client's token is used for all normal api calls
        super().__init__(api_token or _UNCONFIGURED, session=session, base_url=base_url)


class KlappstuhlInternalClient(Client):
    """Percy's extension of :class:`klappstuhl.Client` for internal api use (see the module docstring)."""

    def __init__(
        self,
        provision_token: str | None = None,
        *,
        session: aiohttp.ClientSession | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        # The base client's token is only ever used for the provisioning call
        # (which needs the service token); guild galleries use per-guild keys.
        super().__init__(provision_token or _UNCONFIGURED, session=session, base_url=base_url)
        # Per-guild clients, each bound to that guild's minted images:guild key
        # and sharing the one aiohttp session. Built on demand, cached here.
        self._guild_clients: dict[int, Client] = {}
        self._guild_lock: asyncio.Lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"<KlappstuhlMeClient base_url={self._http.base_url!r} available={self.available}>"

    @property
    def available(self) -> bool:
        """Whether the hoster is configured (a provision token or a personal key)."""
        return bool(self._http.token and self._http.token != _UNCONFIGURED)

    # -- per-guild key provisioning ------------------------------------------

    async def _provision_guild_key(self, guild_id: int) -> str:
        """Get-or-create a guild's ``images:guild`` key from the host."""
        # Uses the escape hatch with the base (service) token as the bearer.
        data = await self.request("POST", f"/guilds/{guild_id}/provision-key")
        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            raise ValueError(f"provision endpoint returned no token for guild {guild_id}")
        return token

    async def _guild_client(self, guild_id: int) -> Client:
        """Return a cached per-guild :class:`Client`, provisioning its key if needed."""
        client = self._guild_clients.get(guild_id)
        if client is not None:
            return client

        async with self._guild_lock:
            # Re-check under the lock: a concurrent caller may have just filled it.
            client = self._guild_clients.get(guild_id)
            if client is not None:
                return client

            if self._http.token:
                token = await self._provision_guild_key(guild_id)
            else:
                raise ValueError(
                    "cannot authorise guild-gallery calls: set KLAPPSTUHL_ME_PROVISION_TOKEN "
                    "(preferred) or the legacy KLAPPSTUHL_ME_API_TOKEN"
                )
            client = KlappstuhlInternalClient(token, session=self._http._session, base_url=self._http.base_url)
            self._guild_clients[guild_id] = client
            return client

    def invalidate_guild_key(self, guild_id: int) -> None:
        """Drop a cached guild client (e.g. after its key was revoked)."""
        self._guild_clients.pop(guild_id, None)

    async def _guild_call[T](self, guild_id: int, call: Callable[[KlappstuhlInternalClient], Awaitable[T]]) -> T:
        """Run a per-guild gallery op, re-provisioning once if the key is rejected."""
        for attempt in range(2):
            client = await self._guild_client(guild_id)
            try:
                return await call(client)  # type: ignore[return-value]
            except (Unauthorized, Forbidden):
                # A cached key that got revoked/rotated → re-provision and retry once.
                if attempt == 0 and self._http.token:
                    self.invalidate_guild_key(guild_id)
                    continue
                raise
        raise AssertionError("unreachable")  # pragma: no cover

    # -- discord-native file coercion ----------------------------------------

    @staticmethod
    async def _coerce(item: object) -> File:
        """Coerce a Percy gallery input into a :class:`klappstuhl.File`."""
        if isinstance(item, File):
            return item
        if isinstance(item, discord.Attachment):
            return File(await item.read(), filename=item.filename)
        if isinstance(item, discord.File):
            item.fp.seek(0)
            data = item.fp.read()
            item.fp.seek(0)
            return File(data, filename=item.filename or "image.png")
        if isinstance(item, tuple) and len(item) == 2:
            name, data = item
            raw = data.getvalue() if isinstance(data, io.BytesIO) else data
            return File(raw, filename=name)
        return resolve_file(item)  # type: ignore[arg-type]  # path/bytes/stream

    # -- guild galleries (per-guild key + discord coercion) ------------------

    async def upload_guild_images(
        self,
        guild_id: int,
        *files: object,
        expires_in: int | None = None,
    ) -> UploadResult:
        """Upload one or more images into a guild's shared gallery.

        Accepts Percy-native inputs (``discord.File`` / ``discord.Attachment`` /
        ``(filename, bytes)`` tuples) in addition to everything the base client
        takes, and routes through the guild's provisioned ``images:guild`` key.
        """
        parts = [await self._coerce(f) for f in files]
        return await self._guild_call(
            guild_id, lambda c: c._upload_guild_images(guild_id, *parts, expires_in=expires_in)
        )

    async def list_guild_images(self, guild_id: int) -> GuildImagesResult:
        """List a guild's shared gallery (newest first, expired omitted)."""
        return await self._guild_call(guild_id, lambda c: c._list_guild_images(guild_id))

    async def delete_guild_image(self, guild_id: int, image_id: str) -> DeleteResult:
        """Delete an image from a guild's shared gallery (scoped by ``guild_id``)."""
        return await self._guild_call(guild_id, lambda c: c._delete_guild_image(guild_id, image_id))

    async def _upload_guild_images(
        self,
        guild_id: int | str,
        *files: FileInput,
        expires_in: int | None = None,
    ) -> UploadResult:
        """Upload images into a Discord guild's shared gallery.

        Requires the ``images:guild`` scope. Identical to :meth:`upload` but
        every row is tagged with ``guild_id`` so it appears in that guild's
        gallery.
        """
        if not files:
            raise ValueError("upload_guild_images() requires at least one file")
        parts = [("file", await resolve_file(f)) for f in files]
        params = {"expires_in": expires_in} if expires_in is not None else None
        data = await self._http.request(
            "POST", f"/guilds/{guild_id}/images/upload", params=params, files=parts
        )
        return UploadResult.from_dict(data)

    async def _list_guild_images(self, guild_id: int | str) -> GuildImagesResult:
        """List a guild's gallery, newest first. Requires ``images:guild``."""
        data = await self._http.request("GET", f"/guilds/{guild_id}/images")
        return GuildImagesResult.from_dict(data)

    async def _delete_guild_image(self, guild_id: int | str, image_id: str) -> DeleteResult:
        """Delete an image from a guild's gallery. Requires ``images:guild``."""
        data = await self._http.request("DELETE", f"/guilds/{guild_id}/images/{_bare_id(image_id)}")
        return DeleteResult.from_dict(data)

    # -- account-scoped features (short links, pastes, QR, unfurl) ------------

    @property
    def account_available(self) -> bool:
        """Whether a personal account key is configured for account-scoped calls.

        Short links, pastes, QR, and unfurl need a real account key (with the
        ``links:*`` / ``pastes:*`` / ``images:read`` scopes), not a per-guild
        provisioned key — set ``KLAPPSTUHL_ME_API_TOKEN`` to enable them.
        """
        return bool(self.api_key)


@dataclass(frozen=True)
class GuildImageInfo:
    """A single image in a Discord guild's shared gallery."""

    id: str
    ext: str
    mimetype: str
    size: int
    uploaded_at: str
    url: str
    raw_url: str
    original_name: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GuildImageInfo:
        return cls(
            id=str(data["id"]),
            ext=str(data["ext"]),
            mimetype=str(data["mimetype"]),
            size=int(data["size"]),
            uploaded_at=str(data["uploaded_at"]),
            url=str(data["url"]),
            raw_url=str(data["raw_url"]),
            original_name=data.get("original_name"),
        )


@dataclass(frozen=True)
class GuildImagesResult:
    """A listing of a guild's gallery."""

    images: list[GuildImageInfo]
    total: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GuildImagesResult:
        images = [GuildImageInfo.from_dict(i) for i in data.get("images", []) or []]
        return cls(images=images, total=int(data.get("total", len(images))))
