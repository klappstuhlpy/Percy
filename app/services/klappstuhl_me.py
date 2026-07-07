"""Percy's klappstuhl.me client.

This is a thin, Percy-only extension of the public :mod:`klappstuhl` wrapper
(the ``klappstuhl.py`` package). The base :class:`klappstuhl.Client` already
covers every general endpoint — uploads, guild galleries, media, render, scan —
so Percy does **not** re-implement a bespoke HTTP client. It only adds the two
things that are deliberately absent from the public library because they are
internal to Percy's deployment:

* **Per-guild key provisioning.** Percy never holds a personal API key. It
  presents a shared ``provision_token`` to the host's internal
  ``POST /guilds/{id}/provision-key`` endpoint, which mints (get-or-creates) a
  narrow ``images:guild`` key for that guild. Those keys are cached in-process
  and used for every subsequent gallery call; a legacy personal ``api_key`` is
  only a fallback.
* **Discord-native file coercion.** Gallery uploads accept ``discord.File`` /
  ``discord.Attachment`` (and ``(filename, bytes)`` tuples) and convert them to
  :class:`klappstuhl.File`, so cogs can hand Percy objects straight through.

Everything else is inherited from :class:`klappstuhl.Client` unchanged.
"""
from __future__ import annotations

import asyncio
import io
from typing import TYPE_CHECKING

import discord
from klappstuhl import Client, File
from klappstuhl.errors import Forbidden, Unauthorized
from klappstuhl.file import resolve_file
from klappstuhl.http import DEFAULT_BASE_URL

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import aiohttp
    from klappstuhl.models import DeleteResult, GuildImagesResult, UploadResult

# A non-empty sentinel handed to the base client when the hoster is unconfigured
# (the base client requires *some* token). It is never sent: every guild call
# checks :attr:`KlappstuhlMeClient.available` first.
_UNCONFIGURED = "unconfigured"


class KlappstuhlMeClient(Client):
    """Percy's extension of :class:`klappstuhl.Client` (see the module docstring)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        api_key: str | None = None,
        provision_token: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        # The base client's token is only ever used for the provisioning call
        # (which needs the service token); guild galleries use per-guild keys.
        super().__init__(provision_token or api_key or _UNCONFIGURED, session=session, base_url=base_url)
        self.api_key: str | None = api_key
        self.provision_token: str | None = provision_token
        self._session: aiohttp.ClientSession = session
        self._base_url: str = base_url
        # Per-guild clients, each bound to that guild's minted images:guild key
        # and sharing the one aiohttp session. Built on demand, cached here.
        self._guild_clients: dict[int, Client] = {}
        self._guild_lock: asyncio.Lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"<KlappstuhlMeClient base_url={self._base_url!r} available={self.available}>"

    @property
    def available(self) -> bool:
        """Whether the hoster is configured (a provision token or a personal key)."""
        return bool(self.provision_token or self.api_key)

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

            if self.provision_token:
                token = await self._provision_guild_key(guild_id)
            elif self.api_key:
                token = self.api_key  # legacy fallback: a personal images:guild key
            else:
                raise ValueError(
                    "cannot authorise guild-gallery calls: set KLAPPSTUHL_ME_PROVISION_TOKEN "
                    "(preferred) or the legacy KLAPPSTUHL_ME_API_TOKEN"
                )
            client = Client(token, session=self._session, base_url=self._base_url)
            self._guild_clients[guild_id] = client
            return client

    def invalidate_guild_key(self, guild_id: int) -> None:
        """Drop a cached guild client (e.g. after its key was revoked)."""
        self._guild_clients.pop(guild_id, None)

    async def _guild_call[T](self, guild_id: int, call: Callable[[Client], Awaitable[T]]) -> T:
        """Run a per-guild gallery op, re-provisioning once if the key is rejected."""
        for attempt in range(2):
            client = await self._guild_client(guild_id)
            try:
                return await call(client)
            except (Unauthorized, Forbidden):
                # A cached key that got revoked/rotated → re-provision and retry once.
                if attempt == 0 and self.provision_token:
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
            guild_id, lambda c: c.upload_guild_images(guild_id, *parts, expires_in=expires_in)
        )

    async def list_guild_images(self, guild_id: int) -> GuildImagesResult:
        """List a guild's shared gallery (newest first, expired omitted)."""
        return await self._guild_call(guild_id, lambda c: c.list_guild_images(guild_id))

    async def delete_guild_image(self, guild_id: int, image_id: str) -> DeleteResult:
        """Delete an image from a guild's shared gallery (scoped by ``guild_id``)."""
        return await self._guild_call(guild_id, lambda c: c.delete_guild_image(guild_id, image_id))
