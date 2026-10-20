"""Tempban/tempmute timers: a newer action cancels the old expiry, and a failed expiry is retried.

Covers :meth:`TimerManager.delete_member_timer` (the one cancellation every ban/unban/mute path
calls), the retry helpers in ``app/cogs/moderation/infractions.py``, the two expiry handlers on
the :class:`Moderation` cog, and the final muted-member flush on unload.
"""

from __future__ import annotations

import asyncio
import datetime
from collections import defaultdict
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import discord

from app.cogs.moderation import cog as cog_module
from app.cogs.moderation.cog import Moderation
from app.cogs.moderation.infractions import (
    TIMER_MAX_RETRIES,
    TIMER_RETRY_BASE,
    mute_role_error,
    retry_expiry_timer,
    timer_retry_delay,
)
from app.core.timer import TimerManager

if TYPE_CHECKING:
    import pytest


def _http(status: int) -> discord.HTTPException:
    cls = {403: discord.Forbidden, 404: discord.NotFound}.get(status, discord.HTTPException)
    return cls(MagicMock(status=status, reason="x"), "error")


def _timer(event: str, *args: object, **kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(
        id=11,
        event=event,
        args=list(args),
        kwargs=kwargs,
        created=datetime.datetime(2026, 1, 1),
        timezone="UTC",
    )


# -- cancellation -------------------------------------------------------------------------------


class _ShortTimer:
    def __init__(self, id: int, event: str, *args: int) -> None:
        self.id, self.event, self.args = id, event, args

    def __getitem__(self, key: int) -> int:
        return self.args[key]

    @property
    def is_short_dispatch(self) -> bool:
        return True


def _manager(db_ids: list[int], loaded_id: int | None = None) -> TimerManager:
    manager = object.__new__(TimerManager)
    manager.bot = MagicMock()
    manager.bot.db.timers.delete_member_timer = AsyncMock(return_value=db_ids)
    manager._loaded_timer = SimpleNamespace(id=loaded_id) if loaded_id is not None else None
    manager._short_timers = {}
    manager._dispatch = MagicMock()
    manager.reset_task = MagicMock()
    return manager


async def test_delete_member_timer_drops_every_matching_timer() -> None:
    manager = _manager([7, 8], loaded_id=8)
    manager._short_timers = {
        -1: _ShortTimer(-1, "tempmute", 1, 2, 3),  # this guild + member
        -2: _ShortTimer(-2, "tempmute", 1, 2, 4),  # another member
        -3: _ShortTimer(-3, "tempban", 1, 2, 3),  # another event
    }

    assert await manager.delete_member_timer("tempmute", 1, 3) is True

    assert set(manager._short_timers) == {-2, -3}
    manager.reset_task.assert_called_once()  # the next-due timer was one of them


async def test_delete_member_timer_reports_nothing_found() -> None:
    manager = _manager([])
    assert await manager.delete_member_timer("tempban", 1, 3) is False
    manager.reset_task.assert_not_called()


async def test_cancelled_short_timer_never_fires() -> None:
    manager = _manager([])
    await manager.call(_ShortTimer(-5, "tempmute", 1, 2, 3))  # type: ignore[arg-type]
    manager._dispatch.assert_not_called()


# -- retry helpers ------------------------------------------------------------------------------


def test_retry_delay_backs_off_then_gives_up() -> None:
    assert timer_retry_delay(0) == datetime.timedelta(minutes=5)
    assert timer_retry_delay(1) == datetime.timedelta(minutes=10)
    assert timer_retry_delay(TIMER_MAX_RETRIES - 1) == datetime.timedelta(hours=6)
    assert timer_retry_delay(TIMER_MAX_RETRIES) is None


async def test_retry_rearms_with_same_args_and_original_time() -> None:
    bot = MagicMock()
    bot.timers.create = AsyncMock()
    timer = _timer("tempban", 1, 2, 3, attempt=2)

    await retry_expiry_timer(bot, timer, "boom")  # type: ignore[arg-type]

    when, event, *args = bot.timers.create.await_args.args
    assert (event, args) == ("tempban", [1, 2, 3])
    assert when > discord.utils.utcnow() + datetime.timedelta(minutes=19)
    kwargs = bot.timers.create.await_args.kwargs
    assert kwargs["attempt"] == 3
    assert kwargs["origin"] == "2026-01-01 00:00:00"


async def test_retry_stops_after_max_attempts() -> None:
    bot = MagicMock()
    bot.timers.create = AsyncMock()
    await retry_expiry_timer(bot, _timer("tempban", 1, 2, 3, attempt=TIMER_MAX_RETRIES), "boom")  # type: ignore[arg-type]
    bot.timers.create.assert_not_awaited()


def test_mute_role_error_handles_a_deleted_role() -> None:
    guild = MagicMock()
    guild.get_role.return_value = None
    assert "no longer exists" in (mute_role_error(guild, 5) or "")


# -- expiry handlers ----------------------------------------------------------------------------


def _cog(guild: object) -> SimpleNamespace:
    bot = MagicMock()
    bot.wait_until_ready = AsyncMock()
    bot.get_guild.return_value = guild
    bot.get_or_fetch_member = AsyncMock(return_value=None)
    bot.fetch_user = AsyncMock(side_effect=_http(404))
    bot.timers.create = AsyncMock()
    config = MagicMock(mute_role_id=50)
    config.alert_missing_permission = AsyncMock()
    bot.db.get_guild_config = AsyncMock(return_value=config)
    return SimpleNamespace(bot=bot, _mute_data_batch=defaultdict(list))


def _guild(**kwargs: object) -> MagicMock:
    guild = MagicMock(unavailable=False)
    for key, value in kwargs.items():
        setattr(guild, key, value)
    return guild


async def test_tempban_expiry_retries_when_guild_unavailable() -> None:
    cog = _cog(_guild(unavailable=True))
    await Moderation.on_tempban_timer_complete(cog, _timer("tempban", 1, 2, 3))  # type: ignore[arg-type]
    cog.bot.timers.create.assert_awaited_once()


async def test_expiry_is_dropped_once_percy_left_the_guild() -> None:
    cog = _cog(None)  # after ready, an outage keeps the guild cached; missing means Percy left
    await Moderation.on_tempban_timer_complete(cog, _timer("tempban", 1, 2, 3))  # type: ignore[arg-type]
    await Moderation.on_tempmute_timer_complete(cog, _timer("tempmute", 1, 2, 3, 40))  # type: ignore[arg-type]
    await Moderation.on_lockdown_timer_complete(cog, _timer("lockdown", 1, 2, 5, [10]))  # type: ignore[arg-type]
    cog.bot.timers.create.assert_not_awaited()


async def test_tempban_expiry_retries_on_server_error() -> None:
    cog = _cog(_guild(unban=AsyncMock(side_effect=_http(500))))
    await Moderation.on_tempban_timer_complete(cog, _timer("tempban", 1, 2, 3))  # type: ignore[arg-type]
    cog.bot.timers.create.assert_awaited_once()


async def test_tempban_expiry_alerts_and_retries_when_forbidden() -> None:
    cog = _cog(_guild(unban=AsyncMock(side_effect=_http(403))))
    await Moderation.on_tempban_timer_complete(cog, _timer("tempban", 1, 2, 3))  # type: ignore[arg-type]
    config = cog.bot.db.get_guild_config.return_value
    config.alert_missing_permission.assert_awaited_once()
    cog.bot.timers.create.assert_awaited_once()


async def test_tempban_expiry_already_unbanned_is_done() -> None:
    cog = _cog(_guild(unban=AsyncMock(side_effect=_http(404))))
    await Moderation.on_tempban_timer_complete(cog, _timer("tempban", 1, 2, 3))  # type: ignore[arg-type]
    cog.bot.timers.create.assert_not_awaited()


def _member(*role_ids: int, remove: AsyncMock | None = None) -> MagicMock:
    member = MagicMock()
    member._roles.has.side_effect = lambda r: r in role_ids
    member.remove_roles = remove or AsyncMock()
    return member


async def test_tempmute_expiry_removes_old_and_rebound_role() -> None:
    member = _member(40, 50)
    cog = _cog(_guild(get_member=MagicMock(return_value=member)))
    await Moderation.on_tempmute_timer_complete(cog, _timer("tempmute", 1, 2, 3, 40))  # type: ignore[arg-type]

    removed = {o.id for o in member.remove_roles.await_args.args}
    assert removed == {40, 50}  # the stored role and the current mute role after a rebind
    cog.bot.timers.create.assert_not_awaited()


async def test_tempmute_expiry_failure_keeps_member_muted_and_retries() -> None:
    member = _member(40, remove=AsyncMock(side_effect=_http(500)))
    cog = _cog(_guild(get_member=MagicMock(return_value=member)))
    await Moderation.on_tempmute_timer_complete(cog, _timer("tempmute", 1, 2, 3, 40))  # type: ignore[arg-type]

    assert cog._mute_data_batch == {}  # still muted, so still tracked for rejoins
    cog.bot.timers.create.assert_awaited_once()


async def test_tempmute_expiry_retries_when_member_lookup_fails() -> None:
    guild = _guild(get_member=MagicMock(return_value=None), fetch_member=AsyncMock(side_effect=_http(503)))
    cog = _cog(guild)
    await Moderation.on_tempmute_timer_complete(cog, _timer("tempmute", 1, 2, 3, 40))  # type: ignore[arg-type]
    cog.bot.timers.create.assert_awaited_once()


async def test_tempmute_expiry_member_gone_is_untracked() -> None:
    guild = _guild(get_member=MagicMock(return_value=None), fetch_member=AsyncMock(side_effect=_http(404)))
    cog = _cog(guild)
    await Moderation.on_tempmute_timer_complete(cog, _timer("tempmute", 1, 2, 3, 40))  # type: ignore[arg-type]
    assert cog._mute_data_batch[1] == [(3, False)]
    cog.bot.timers.create.assert_not_awaited()


# -- mute evasion -------------------------------------------------------------------------------


async def test_unload_flushes_pending_mutes() -> None:
    flush = AsyncMock()
    cog = SimpleNamespace(
        bulk_mute_insert=MagicMock(cancel=MagicMock(), side_effect=flush), _sentinel_resume=MagicMock()
    )

    await Moderation.cog_unload(cog)  # type: ignore[arg-type]

    cog.bulk_mute_insert.cancel.assert_called_once()
    cog._sentinel_resume.cancel.assert_called_once()
    flush.assert_awaited_once()


async def test_unload_survives_a_cog_load_that_never_ran() -> None:
    flush = AsyncMock()
    cog = SimpleNamespace(bulk_mute_insert=MagicMock(cancel=MagicMock(), side_effect=flush), _sentinel_resume=None)
    await Moderation.cog_unload(cog)  # type: ignore[arg-type]
    flush.assert_awaited_once()


# -- lockdown timers ----------------------------------------------------------------------------


async def test_drop_lockdown_channels_narrows_and_cancels_short_timers() -> None:
    manager = _manager([])
    manager.bot.db.timers.drop_lockdown_channels = AsyncMock(return_value=[8])
    manager._loaded_timer = SimpleNamespace(id=8)  # narrowed in the db, so its in-memory args are stale
    manager._short_timers = {
        -1: _ShortTimer(-1, "lockdown", 1, 2, 5, [10, 11]),  # type: ignore[arg-type]  narrowed
        -2: _ShortTimer(-2, "lockdown", 1, 2, 5, [10]),  # type: ignore[arg-type]  emptied -> cancelled
        -3: _ShortTimer(-3, "lockdown", 9, 2, 5, [10]),  # type: ignore[arg-type]  another guild
    }

    await manager.drop_lockdown_channels(1, [10])

    assert set(manager._short_timers) == {-1, -3}
    assert manager._short_timers[-1][3] == [11] and manager._short_timers[-3][3] == [10]
    manager.reset_task.assert_called_once()


async def test_lockdown_expiry_retries_only_failed_channels_and_alerts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = MagicMock(id=11, mention="<#11>")
    monkeypatch.setattr(cog_module, "end_lockdown", AsyncMock(return_value=[failed]))
    cog = _cog(_guild(get_channel_or_thread=MagicMock(return_value=None)))
    config = cog.bot.db.get_guild_config.return_value

    await Moderation.on_lockdown_timer_complete(cog, _timer("lockdown", 1, 2, 5, [10, 11]))  # type: ignore[arg-type]
    _, event, *args = cog.bot.timers.create.await_args.args
    assert (event, args) == ("lockdown", [1, 2, 5, [11]])
    config.alert_missing_permission.assert_awaited_once()

    await Moderation.on_lockdown_timer_complete(cog, _timer("lockdown", 1, 2, 5, [11], attempt=1))  # type: ignore[arg-type]
    config.alert_missing_permission.assert_awaited_once()  # not again per retry
    assert cog.bot.timers.create.await_count == 2


async def test_lockdown_end_retries_failed_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = MagicMock(id=11, mention="<#11>")
    monkeypatch.setattr(cog_module, "end_lockdown", AsyncMock(return_value=[failed]))
    cog = _cog(None)
    cog.bot.db.moderation.get_lockdowns = AsyncMock(return_value=[object()])
    ctx = MagicMock()
    ctx.guild.id, ctx.author.id, ctx.channel.id = 1, 2, 5
    ctx.message.created_at = discord.utils.utcnow()
    ctx.send_info = AsyncMock()

    await Moderation.lockdown_end.callback(cog, ctx)  # type: ignore[arg-type]

    when, event, *args = cog.bot.timers.create.await_args.args
    assert (when, event, args) == (TIMER_RETRY_BASE, "lockdown", [1, 2, 5, [11]])
    assert cog.bot.timers.create.await_args.kwargs["attempt"] == 1  # the moderator was told here, no alert
    ctx.send_info.assert_awaited_once()


async def test_idle_wait_holds_no_pooled_connection() -> None:
    manager = _manager([])
    manager.db = MagicMock(acquire=MagicMock(side_effect=AssertionError("held a connection")))
    manager._TimerManager__event = asyncio.Event()  # type: ignore[attr-defined]
    loaded = object()
    manager.load_next_timer = AsyncMock(side_effect=[None, loaded])

    waiting = asyncio.create_task(manager.wait())
    await asyncio.sleep(0)
    assert not waiting.done()  # idle, and shutdown's pool.close() has no connection to wait for

    manager._TimerManager__event.set()  # type: ignore[attr-defined]
    assert await waiting is loaded
    manager.db.acquire.assert_not_called()
