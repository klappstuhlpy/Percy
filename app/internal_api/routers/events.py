"""Server-sent event stream the dashboard's live pages subscribe to (through the BFF).

``GET /guilds/{guild_id}/events?topics=music,case,stats`` -> ``text/event-stream`` of
``{type, data}`` JSON messages from the in-process :class:`~app.services.event_hub.EventHub`
(``bot.events``). Events are hints; the page refetches the real payload over its existing HTTP
endpoint. The ``stats`` topic is not published by anyone: the stream itself emits a bot-stats
snapshot every :data:`STATS_INTERVAL` seconds, only while a client asked for it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.services.event_hub import format_sse, parse_topics

from ..dependencies import BotDep, GuildDep, verify_token
from .stats import get_bot_stats

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

log = logging.getLogger(__name__)

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Events"], dependencies=[Depends(verify_token)])

HEARTBEAT = 15.0
STATS_INTERVAL = 10.0
STATS_KEYS = ('version', 'guild_count', 'user_count', 'command_count', 'total_commands_used', 'latency_ms', 'cog_count')


async def stats_event(bot: Any) -> dict:
    stats = await get_bot_stats(bot)
    return {'type': 'stats', 'data': {k: stats[k] for k in STATS_KEYS}}


async def event_stream(
    bot: Any, guild_id: int, topics: frozenset[str] | None, *,
    heartbeat: float = HEARTBEAT, stats_interval: float = STATS_INTERVAL,
) -> AsyncIterator[bytes]:
    """SSE bytes for one subscriber; always unsubscribes, however the client goes away."""
    with_stats = topics is None or 'stats' in topics
    sub = bot.events.subscribe(guild_id, topics)
    try:
        yield b'retry: 5000\n\n'
        last_beat = time.monotonic()
        next_stats = last_beat if with_stats else float('inf')  # first snapshot goes out at once
        while True:
            now = time.monotonic()
            timeout = min(heartbeat - (now - last_beat), next_stats - now)
            try:
                yield format_sse(await asyncio.wait_for(sub.get(), timeout=max(timeout, 0.01)))
                last_beat = time.monotonic()
            except TimeoutError:
                pass
            now = time.monotonic()
            if now >= next_stats:
                next_stats = now + stats_interval
                try:
                    yield format_sse(await stats_event(bot))
                    last_beat = time.monotonic()
                except Exception:
                    log.exception('Stats snapshot for the event stream failed')  # never ends the stream
            if now - last_beat >= heartbeat:
                yield b': heartbeat\n\n'
                last_beat = now
    finally:
        bot.events.unsubscribe(sub)


@router.get("/events")
async def stream_events(
    guild: GuildDep, bot: BotDep, topics: str | None = Query(None, max_length=200),
) -> StreamingResponse:
    """Live ``{type, data}`` hints for one guild. ``topics`` is a comma list (default: all)."""
    # ponytail: no per-guild subscriber cap; the BFF opens one stream per open dashboard tab.
    return StreamingResponse(
        event_stream(bot, guild.id, parse_topics(topics)),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )
