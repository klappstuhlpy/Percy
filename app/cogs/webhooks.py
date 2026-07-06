"""Outbound webhook dispatcher: pushes signed event payloads to guild-registered URLs.

The subscription CRUD lives in the internal API (``app/internal_api/routers/subscriptions.py``);
this cog is the delivery half. It listens to a curated set of gateway events — plus an internal
``percy_event`` seam any other cog can dispatch to — and POSTs a signed JSON envelope to every
enabled subscription that asked for that event. The pure envelope/signing helpers live in
``app.services.webhooks`` (Discord-free); turning Discord objects into payloads happens here.

To emit a domain event from elsewhere::

    self.bot.dispatch('percy_event', guild.id, 'level.up', {'user_id': str(member.id), 'level': 5})
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import aiohttp

from app.core import Cog
from app.services import SIGNATURE_HEADER, build_envelope, serialize_envelope, sign_body

if TYPE_CHECKING:
    import asyncpg
    import discord

    from app.core import Bot

log = logging.getLogger(__name__)

#: Per-attempt timeout (seconds) and retry budget for a single delivery.
_DELIVERY_TIMEOUT = 10.0
_MAX_ATTEMPTS = 3
#: Auto-disable a subscription after this many consecutive failed deliveries.
_FAILURE_LIMIT = 15
#: How long the "which guilds have a webhook" set is cached before a refresh.
_ACTIVE_TTL = 60.0


class WebhookDispatcher(Cog, name="Webhooks"):
    """Delivers signed event payloads to a guild's registered outbound webhooks.

    Delivery is fire-and-forget with bounded retries; every attempt is logged to
    ``webhook_deliveries`` and a subscription that keeps failing is auto-disabled so a dead
    endpoint never generates unbounded retries.
    """

    __hidden__ = True
    emoji = "\N{GLOBE WITH MERIDIANS}"

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self.bot = bot
        self._tasks: set[asyncio.Task] = set()
        self._active_guilds: set[int] = set()
        self._active_loaded_at: float = 0.0

    async def cog_unload(self) -> None:
        for task in list(self._tasks):
            task.cancel()

    # -- payload serialization (Discord objects -> plain dicts) -----------

    @staticmethod
    def _user_data(user: discord.abc.User) -> dict:
        return {
            "id": str(user.id),
            "name": user.name,
            "display_name": getattr(user, "display_name", user.name),
            "bot": user.bot,
            "avatar_url": str(user.display_avatar.url),
        }

    @classmethod
    def _member_data(cls, member: discord.Member) -> dict:
        data = cls._user_data(member)
        data["joined_at"] = member.joined_at.isoformat() if member.joined_at else None
        return data

    @staticmethod
    def _role_data(role: discord.Role) -> dict:
        return {"id": str(role.id), "name": role.name, "color": role.color.value, "position": role.position}

    # -- gateway listeners ------------------------------------------------

    @Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        self.emit(member.guild.id, "member.join", self._member_data(member))

    @Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        self.emit(member.guild.id, "member.remove", self._member_data(member))

    @Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.abc.User) -> None:
        self.emit(guild.id, "member.ban", self._user_data(user))

    @Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.abc.User) -> None:
        self.emit(guild.id, "member.unban", self._user_data(user))

    @Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        self.emit(role.guild.id, "role.create", self._role_data(role))

    @Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        self.emit(role.guild.id, "role.delete", self._role_data(role))

    @Cog.listener()
    async def on_percy_event(self, guild_id: int, event: str, data: dict) -> None:
        """Internal seam: ``bot.dispatch('percy_event', guild_id, event, data)`` from any cog."""
        self.emit(guild_id, event, data)

    # -- dispatch + delivery ---------------------------------------------

    def emit(self, guild_id: int, event: str, data: dict) -> None:
        """Schedule delivery of ``event`` to all matching subscriptions (fire-and-forget)."""
        task = asyncio.create_task(self._dispatch(guild_id, event, data))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _guild_has_subscriptions(self, guild_id: int) -> bool:
        """Cached membership test so events in webhook-less guilds skip the per-event query."""
        now = time.monotonic()
        if now - self._active_loaded_at > _ACTIVE_TTL:
            try:
                self._active_guilds = await self.bot.db.event_webhooks.active_guild_ids()
            except Exception:
                log.exception("Failed to refresh active webhook guilds")
                return True  # fail open: better a wasted query than a dropped event
            self._active_loaded_at = now
        return guild_id in self._active_guilds

    async def _dispatch(self, guild_id: int, event: str, data: dict) -> None:
        if not await self._guild_has_subscriptions(guild_id):
            return
        try:
            subs = await self.bot.db.event_webhooks.matching_for_event(guild_id, event)
        except Exception:
            log.exception("Failed to load webhook subscriptions for guild %s", guild_id)
            return
        for sub in subs:
            await self._deliver(sub, event, data)

    async def _deliver(self, sub: asyncpg.Record, event: str, data: dict) -> dict:
        """POST a signed envelope to one subscription with bounded retries; log the outcome."""
        envelope = build_envelope(event, sub["guild_id"], data)
        body = serialize_envelope(envelope)
        headers = {
            "Content-Type": "application/json",
            SIGNATURE_HEADER: sign_body(sub["secret"], body),
            "X-Percy-Event": event,
            "User-Agent": "Percy-Webhooks/1.0",
        }

        status_code: int | None = None
        error: str | None = None
        success = False
        attempts = 0

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            attempts = attempt
            try:
                async with self.bot.session.post(
                    sub["url"], data=body, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=_DELIVERY_TIMEOUT),
                ) as resp:
                    status_code = resp.status
                    if 200 <= resp.status < 300:
                        success = True
                        break
                    error = f"HTTP {resp.status}"
            except TimeoutError:
                error = "timeout"
            except aiohttp.ClientError as exc:
                error = f"{type(exc).__name__}: {exc}"
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(2 ** (attempt - 1))

        try:
            failures = await self.bot.db.event_webhooks.record_attempt(
                sub["id"], event, success=success, status_code=status_code, attempts=attempts, error=error,
            )
            if not success and failures >= _FAILURE_LIMIT:
                await self.bot.db.event_webhooks.update(sub["id"], sub["guild_id"], {"enabled": False})
                self._active_loaded_at = 0.0  # force a refresh so we stop dispatching to it
                log.warning("Auto-disabled webhook subscription %s after %d consecutive failures", sub["id"], failures)
        except Exception:
            log.exception("Failed to record webhook delivery for subscription %s", sub["id"])

        return {"success": success, "status_code": status_code, "attempts": attempts, "error": error}

    async def deliver_test(self, sub: asyncpg.Record) -> dict:
        """Synchronously deliver a sample ``ping`` payload (used by the /test endpoint)."""
        return await self._deliver(sub, "ping", {"message": "This is a test delivery from Percy."})


async def setup(bot: Bot) -> None:
    await bot.add_cog(WebhookDispatcher(bot))
