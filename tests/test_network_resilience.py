"""Riding out a lost network: loops survive, waiting logs once, music knows when to rebuild."""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import asyncpg
import pytest

from app.cogs.music.cog import VOICE_LOST_AFTER, Music, needs_voice_rebuild
from app.utils import tasks as task_utils
from app.utils.logging import QuietReconnects
from app.utils.tasks import network_loop, wait_for_network

APP = Path(__file__).resolve().parents[1] / 'app'


async def test_network_loop_survives_an_outage_and_logs_once(caplog: pytest.LogCaptureFixture) -> None:
    outcomes = [ConnectionError('down'), asyncpg.InterfaceError('closed'), 'ok', 'ok']

    async def body() -> str:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    iteration = network_loop(seconds=60)(body).coro
    with caplog.at_level(logging.INFO, logger='app.utils.tasks'):
        assert [await iteration() for _ in range(4)] == [None, None, 'ok', 'ok']

    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 2
    assert 'waiting for the network' in messages[0]
    assert 'recovered' in messages[1]


async def test_network_loop_still_raises_real_bugs() -> None:
    async def body() -> None:
        raise KeyError('bug')

    with pytest.raises(KeyError):
        await network_loop(seconds=60)(body).coro()


async def test_wait_for_network_retries_until_reachable(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    class Writer:
        def close(self) -> None:
            pass

    attempts = [OSError('no route'), TimeoutError(), (None, Writer())]
    sleeps: list[float] = []

    async def connect(*_: object) -> tuple[None, Writer]:
        attempt = attempts.pop(0)
        if isinstance(attempt, Exception):
            raise attempt
        return attempt

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(task_utils.asyncio, 'open_connection', connect)
    monkeypatch.setattr(task_utils.asyncio, 'sleep', sleep)
    with caplog.at_level(logging.INFO, logger='app.utils.tasks'):
        await asyncio.wait_for(wait_for_network(), timeout=5)

    assert sleeps == [2.0, 4.0]
    assert sum('waiting for the network' in r.getMessage() for r in caplog.records) == 1


def test_reconnect_log_loses_its_traceback_only() -> None:
    try:
        raise OSError('dns')
    except OSError:
        exc_info = sys.exc_info()
    record = logging.LogRecord('discord.client', logging.ERROR, __file__, 1, 'Attempting a reconnect in %.2fs', (2.0,), exc_info)
    assert QuietReconnects().filter(record)
    assert record.exc_info is None
    assert record.levelno == logging.WARNING
    assert 'in 2.00s' in record.getMessage()

    other = logging.LogRecord('discord.client', logging.ERROR, __file__, 1, 'Something else', (), exc_info)
    assert QuietReconnects().filter(other)
    assert other.exc_info is not None


def test_music_rebuilds_only_when_voice_is_really_gone() -> None:
    assert needs_voice_rebuild(1.0, resumed=False)
    assert not needs_voice_rebuild(VOICE_LOST_AFTER - 1, resumed=True)
    assert needs_voice_rebuild(VOICE_LOST_AFTER, resumed=True)


@pytest.mark.parametrize('node_up', [True, False])
def test_music_rebuild_waits_for_lavalink(monkeypatch: pytest.MonkeyPatch, node_up: bool) -> None:
    cog = Music.__new__(Music)
    cog.bot = MagicMock()
    cog.bot.loop.create_task.side_effect = lambda coro: coro.close()
    cog._restored, cog._outage_pending, cog._offline_since = True, False, time.monotonic()
    monkeypatch.setattr(Music, '_node_connected', staticmethod(lambda: node_up))

    cog._after_reconnect(resumed=False)

    assert cog.bot.loop.create_task.called is node_up
    assert cog._outage_pending is not node_up


def test_background_loops_use_network_loop() -> None:
    bare = [str(p.relative_to(APP)) for p in APP.glob('**/*.py') if '@tasks.loop(' in p.read_text(encoding='utf-8')]
    assert not bare, f'use app.utils.tasks.network_loop, or an outage can stop these loops: {bare}'
