"""The sentinel role queue survives departed members, hierarchy failures, restarts and role changes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import discord

# app.database cannot be imported first (circular), so load app.core before it.
import app.core  # pyright: ignore[reportUnusedImport]
from app.database.base import Sentinel
from app.database.sentinel_roles import RoleOutcome, SentinelRoleQueue, attempt

if TYPE_CHECKING:
    import pytest


def _not_found(code: int) -> discord.NotFound:
    return discord.NotFound(MagicMock(status=404, reason="Not Found"), {"code": code, "message": "Unknown"})


def _forbidden() -> discord.Forbidden:
    return discord.Forbidden(MagicMock(status=403, reason="Forbidden"), {"code": 50013, "message": "Missing Permissions"})


def _server_error() -> discord.DiscordServerError:
    return discord.DiscordServerError(MagicMock(status=500, reason="Server Error"), "boom")


def _setup(
    rows: list[tuple[int, str]], *, add: Any = None, remove: Any = None, starter_role_id: int | None = None
) -> Any:
    sentinel = SimpleNamespace(
        id=1, role_id=50, starter_role_id=starter_role_id, members={uid for uid, _ in rows}, edit=AsyncMock()
    )
    config = SimpleNamespace(send_alert=AsyncMock())
    guilds = SimpleNamespace(
        get_queued_sentinel_members=AsyncMock(return_value=[{"user_id": uid, "state": st} for uid, st in rows]),
        get_sentinel_queue_guild_ids=AsyncMock(return_value=[]),
        delete_sentinel_member=AsyncMock(return_value=True),
        update_sentinel_member_state=AsyncMock(),
    )
    bot = SimpleNamespace(
        http=SimpleNamespace(add_role=add or AsyncMock(), remove_role=remove or AsyncMock()),
        db=SimpleNamespace(
            guilds=guilds,
            get_guild_sentinel=AsyncMock(return_value=sentinel),
            get_guild_config=AsyncMock(return_value=config),
        ),
    )
    return SentinelRoleQueue(bot), bot, sentinel, config  # type: ignore[arg-type]


async def test_attempt_classifies_discord_errors() -> None:
    async def raising(exc: Exception) -> None:
        raise exc

    assert await attempt(asyncio.sleep(0)) is RoleOutcome.done
    assert await attempt(raising(_not_found(10007))) is RoleOutcome.member_gone
    assert await attempt(raising(_not_found(10013))) is RoleOutcome.member_gone
    assert await attempt(raising(_not_found(10011))) is RoleOutcome.role_gone
    assert await attempt(raising(_forbidden())) is RoleOutcome.forbidden
    assert await attempt(raising(_server_error())) is RoleOutcome.retry


async def test_departed_member_is_dropped_and_the_queue_continues() -> None:
    remove = AsyncMock(side_effect=[_not_found(10007), None])
    queue, bot, sentinel, _ = _setup([(10, "pending_remove"), (11, "pending_remove")], remove=remove)

    retry = await queue.run_once(1)

    assert retry is False
    assert remove.await_count == 2
    deleted = [c.args[1] for c in bot.db.guilds.delete_sentinel_member.await_args_list]
    assert deleted == [10, 11]
    assert sentinel.members == set()


async def test_hierarchy_failure_alerts_once_keeps_rows_and_continues() -> None:
    add = AsyncMock(side_effect=_forbidden())
    queue, bot, _, config = _setup([(10, "pending_add"), (11, "pending_add")], add=add)

    await queue.run_once(1)
    await queue.run_once(1)  # the next pass fails the same way

    assert add.await_count == 4  # every member was tried on both passes
    config.send_alert.assert_awaited_once()
    assert "<@&50>" in config.send_alert.await_args.args[0]
    assert config.send_alert.await_args.kwargs["force"] is True
    bot.db.guilds.update_sentinel_member_state.assert_not_awaited()
    bot.db.guilds.delete_sentinel_member.assert_not_awaited()


async def test_added_state_is_only_written_over_pending_add() -> None:
    queue, bot, _, _ = _setup([(10, "pending_add")])

    await queue.run_once(1)

    bot.db.guilds.update_sentinel_member_state.assert_awaited_once_with(1, 10, "added", only_from="pending_add")


async def test_unknown_role_disables_the_sentinel_and_stops() -> None:
    add = AsyncMock(side_effect=_not_found(10011))
    queue, _, sentinel, config = _setup([(10, "pending_add"), (11, "pending_add")], add=add)

    await queue.run_once(1)

    add.assert_awaited_once()
    sentinel.edit.assert_awaited_once_with(started_at=None, role_id=None)
    config.send_alert.assert_awaited_once()


async def test_server_error_asks_for_a_retry() -> None:
    queue, *_ = _setup([(10, "pending_add")], add=AsyncMock(side_effect=_server_error()))

    assert await queue.run_once(1) is True


async def test_verified_member_gets_the_starter_role() -> None:
    add = AsyncMock()
    queue, *_ = _setup([(10, "pending_remove")], add=add, starter_role_id=77)

    await queue.run_once(1)

    add.assert_awaited_once()
    assert add.await_args.args[:3] == (1, 10, 77)


async def test_worker_runs_another_pass_when_woken_mid_pass_then_exits() -> None:
    queue, *_ = _setup([])
    passes = 0

    async def run_once(guild_id: int) -> bool:
        nonlocal passes
        passes += 1
        if passes == 1:
            queue.wake(guild_id)  # new work arrives while the first pass runs
        return False

    queue.run_once = run_once  # type: ignore[method-assign]
    queue.wake(1)
    await queue._tasks[1]

    assert passes == 2
    queue.wake(1)  # an idle worker is started again on the next wake
    await queue._tasks[1]
    assert passes == 3


async def test_resume_wakes_every_guild_with_unfinished_rows() -> None:
    queue, bot, _, _ = _setup([])
    bot.db.guilds.get_sentinel_queue_guild_ids.return_value = [1, 2]
    queue.wake = MagicMock()  # type: ignore[method-assign]

    await queue.resume()

    assert [c.args[0] for c in queue.wake.call_args_list] == [1, 2]


# -- Sentinel record ---------------------------------------------------------


def _sentinel(rows: list[dict], monkeypatch: pytest.MonkeyPatch) -> Sentinel:
    monkeypatch.setattr(Sentinel, "update", AsyncMock())
    guilds = SimpleNamespace(
        release_sentinel_members=AsyncMock(),
        get_sentinel_members=AsyncMock(return_value=rows),
        delete_sentinel_members=AsyncMock(),
    )
    bot = SimpleNamespace(db=SimpleNamespace(guilds=guilds, sentinel_roles=MagicMock()))
    record = {
        "id": 1, "started_at": None, "channel_id": 2, "role_id": 50, "starter_role_id": None,
        "message_id": 3, "bypass_action": "kick", "rate": None,
    }
    return Sentinel(rows, bot=bot, record=record)


def test_pending_add_rows_count_as_held(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"user_id": 1, "state": "pending_add"},
        {"user_id": 2, "state": "added"},
        {"user_id": 3, "state": "pending_remove"},
    ]
    assert _sentinel(rows, monkeypatch).members == {1, 2}


async def test_disable_releases_everyone_and_wakes_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = _sentinel([{"user_id": 1, "state": "added"}], monkeypatch)

    await sentinel.disable()

    sentinel.bot.db.guilds.release_sentinel_members.assert_awaited_once_with(1)
    sentinel.bot.db.sentinel_roles.wake.assert_called_once_with(1)
    assert sentinel.members == set()


async def test_changing_the_role_strips_the_old_one(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"user_id": 1, "state": "pending_remove"}, {"user_id": 2, "state": "pending_remove"}]
    sentinel = _sentinel(rows, monkeypatch)

    await sentinel.edit(role_id=60)

    strip = sentinel.bot.db.sentinel_roles.strip
    strip.assert_called_once()
    guild_id, old_role, members = strip.call_args.args
    assert (guild_id, old_role, list(members)) == (1, 50, [1, 2])
    assert sentinel.role_id == 60


async def test_reselecting_the_same_role_keeps_the_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = _sentinel([{"user_id": 1, "state": "added"}], monkeypatch)

    await sentinel.edit(role_id=50)

    sentinel.bot.db.guilds.delete_sentinel_members.assert_not_awaited()
    assert sentinel.members == {1}
