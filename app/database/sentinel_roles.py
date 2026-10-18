"""The background worker that gives and takes the sentinel lockdown role.

``guild_sentinel_members`` is the queue: a ``pending_add`` row still needs the lockdown role,
a ``pending_remove`` row still needs it taken away, an ``added`` row is done. One worker task
per guild drains those rows and exits when nothing is left. It belongs to the database, not to
a cached :class:`~app.database.base.Sentinel`, so cache invalidation, LRU eviction and menu
edits can no longer stop it or drop its work, and :meth:`SentinelRoleQueue.resume` picks up
whatever a restart interrupted, whether or not the sentinel flag is still on.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from collections.abc import Awaitable, Iterable

    from app.core import Bot
    from app.database.base import Sentinel

log = logging.getLogger("mod")

UNKNOWN_ROLE = 10011

RETRY_DELAY = 30.0
"""Seconds to wait before another pass after Discord answered with a server error."""

ALERT_COOLDOWN = 600.0
"""At most one hierarchy/permission alert per guild in this many seconds."""


class RoleOutcome(enum.Enum):
    """What one role add/remove call ended in, and therefore what the worker does next."""

    done = "done"  # the call went through
    member_gone = "member_gone"  # 10007 Unknown Member and friends: drop the row, carry on
    role_gone = "role_gone"  # 10011 Unknown Role: nothing more can be done with this role
    forbidden = "forbidden"  # hierarchy or missing Manage Roles: keep the row, alert, carry on
    retry = "retry"  # Discord had a server error: keep the row, try again shortly
    failed = "failed"  # anything else: keep the row, log, carry on


async def attempt(call: Awaitable[object]) -> RoleOutcome:
    """Run one role call and classify how it ended. Never raises an HTTP error."""
    try:
        await call
    except discord.NotFound as e:
        return RoleOutcome.role_gone if e.code == UNKNOWN_ROLE else RoleOutcome.member_gone
    except discord.Forbidden:
        return RoleOutcome.forbidden
    except discord.DiscordServerError:
        return RoleOutcome.retry
    except discord.HTTPException:
        log.exception("[Sentinel] A role call failed")
        return RoleOutcome.failed
    return RoleOutcome.done


class SentinelRoleQueue:
    """Drains ``guild_sentinel_members`` for every guild, one task per guild with work."""

    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._wake: dict[int, asyncio.Event] = {}
        self._alerted_at: dict[tuple[int, str], float] = {}
        self._strips: set[asyncio.Task[None]] = set()

    def wake(self, guild_id: int) -> None:
        """Tell the guild's worker there is new work, starting it if it is not running."""
        self._wake.setdefault(guild_id, asyncio.Event()).set()
        task = self._tasks.get(guild_id)
        if task is None or task.done():
            self._tasks[guild_id] = asyncio.create_task(self._drain(guild_id))

    async def resume(self) -> None:
        """Start a worker for every guild a restart left with unfinished rows."""
        for guild_id in await self.bot.db.guilds.get_sentinel_queue_guild_ids():
            self.wake(guild_id)

    async def _drain(self, guild_id: int) -> None:
        event = self._wake[guild_id]
        while True:
            # Cleared *before* reading the rows: work queued while a pass runs sets it again,
            # so the worker cannot exit between that write and the next pass.
            event.clear()
            try:
                retry = await self.run_once(guild_id)
            except Exception:
                log.exception("[Sentinel] The role queue of guild ID %d stopped", guild_id)
                return
            if retry:
                await asyncio.sleep(RETRY_DELAY)
                continue
            if not event.is_set():
                return

    async def run_once(self, guild_id: int) -> bool:
        """Work through every unfinished row once. Returns whether a pass should be retried."""
        rows = await self.bot.db.guilds.get_queued_sentinel_members(guild_id)
        if not rows:
            return False
        sentinel = await self.bot.db.get_guild_sentinel(guild_id)
        if sentinel is None or sentinel.role_id is None:
            return False

        retry = False
        forbidden: dict[str, int] = {}
        for row in rows:
            member_id: int = row["user_id"]
            adding = row["state"] == "pending_add"
            outcome = await self.apply(sentinel, member_id, adding=adding)
            if outcome is RoleOutcome.role_gone:
                await self._role_gone(sentinel)
                return False
            if outcome is RoleOutcome.forbidden:
                verb = "give it to" if adding else "take it away from"
                forbidden[verb] = forbidden.get(verb, 0) + 1
            retry = retry or outcome is RoleOutcome.retry

        if forbidden:
            await self._alert_forbidden(sentinel, forbidden)
        return retry

    async def apply(self, sentinel: Sentinel, member_id: int, *, adding: bool) -> RoleOutcome:
        """Give or take the lockdown role for one member and settle their row accordingly."""
        guild_id, role_id = sentinel.id, sentinel.role_id
        assert role_id is not None
        guilds = self.bot.db.guilds
        http = self.bot.http

        if adding:
            outcome = await attempt(http.add_role(guild_id, member_id, role_id, reason="Started Sentinel verification"))
            if outcome is RoleOutcome.done:
                # Conditional: a member who verified meanwhile is ``pending_remove`` now and stays so.
                await guilds.update_sentinel_member_state(guild_id, member_id, "added", only_from="pending_add")
        else:
            outcome = await attempt(
                http.remove_role(guild_id, member_id, role_id, reason="Completed Sentinel verification")
            )
            # Conditional: a member blocked again meanwhile is ``pending_add`` now and stays held.
            if outcome is RoleOutcome.done and await guilds.delete_sentinel_member(
                guild_id, member_id, only_state="pending_remove"
            ):
                sentinel.members.discard(member_id)
                await self._give_starter_role(sentinel, member_id)

        if outcome is RoleOutcome.member_gone:
            # They left or were banned: nobody is left to hold or release.
            await guilds.delete_sentinel_member(guild_id, member_id)
            sentinel.members.discard(member_id)
        return outcome

    async def _give_starter_role(self, sentinel: Sentinel, member_id: int) -> None:
        starter_id = sentinel.starter_role_id
        if starter_id is None:
            return
        outcome = await attempt(
            self.bot.http.add_role(sentinel.id, member_id, starter_id, reason="Completed Sentinel verification")
        )
        if outcome is RoleOutcome.role_gone:
            await sentinel.edit(starter_role_id=None)
            await self._alert(sentinel.id, "The Sentinel **starter role** no longer exists, so it has been unset.")
        elif outcome is RoleOutcome.forbidden:
            await self._alert(
                sentinel.id,
                f"Sentinel could not give the starter role <@&{starter_id}> to verified members. "
                "I need **Manage Roles** and a highest role above it.",
                cooldown="starter",
            )

    async def _role_gone(self, sentinel: Sentinel) -> None:
        await self._alert(sentinel.id, "The Sentinel **lockdown role** no longer exists, so Sentinel has been disabled.")
        await sentinel.edit(started_at=None, role_id=None)

    async def _alert_forbidden(self, sentinel: Sentinel, counts: dict[str, int]) -> None:
        what = " or ".join(f"{verb} {n} member(s)" for verb, n in counts.items())
        await self._alert(
            sentinel.id,
            f"Sentinel could not use the lockdown role <@&{sentinel.role_id}>: I could not {what}. "
            "I need **Manage Roles** and a highest role above the lockdown role. "
            "Fix that and I retry on the next join or verification.",
            cooldown="lockdown",
        )

    async def _alert(self, guild_id: int, content: str, *, cooldown: str | None = None) -> None:
        """Alert the moderators; with ``cooldown``, at most once per ``ALERT_COOLDOWN`` for that kind."""
        if cooldown is not None:
            now = time.monotonic()
            last = self._alerted_at.get((guild_id, cooldown))
            if last is not None and now - last < ALERT_COOLDOWN:
                return
            self._alerted_at[guild_id, cooldown] = now
        config = await self.bot.db.get_guild_config(guild_id)
        await config.send_alert(content, force=True)

    def strip(self, guild_id: int, role_id: int, member_ids: Iterable[int]) -> None:
        """Take a role that is no longer the lockdown role away from members still holding it."""
        task = asyncio.create_task(self._strip(guild_id, role_id, list(member_ids)))
        self._strips.add(task)
        task.add_done_callback(self._strips.discard)

    async def _strip(self, guild_id: int, role_id: int, member_ids: list[int]) -> None:
        forbidden = 0
        for member_id in member_ids:
            outcome = await attempt(
                self.bot.http.remove_role(guild_id, member_id, role_id, reason="Sentinel lockdown role changed")
            )
            if outcome is RoleOutcome.role_gone:
                return
            forbidden += outcome is RoleOutcome.forbidden
        if forbidden:
            await self._alert(
                guild_id,
                f"Sentinel could not take the previous lockdown role <@&{role_id}> from {forbidden} member(s). "
                "Remove it by hand, or give me **Manage Roles** and a highest role above it.",
                cooldown="strip",
            )

