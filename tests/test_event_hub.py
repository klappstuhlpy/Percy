"""EventHub + the SSE generator behind ``GET /guilds/{id}/events``. Every await is time-boxed."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.internal_api.routers.events import event_stream
from app.services.infra.event_hub import QUEUE_SIZE, EventHub, format_sse, parse_topics

T = 2.0


async def test_publish_reaches_only_that_guilds_subscribers() -> None:
    hub = EventHub()
    a, b = hub.subscribe(1), hub.subscribe(2)
    hub.publish(1, 'music.state', {'x': 1})
    assert await asyncio.wait_for(a.get(), T) == {'type': 'music.state', 'data': {'x': 1}}
    assert b.queue.empty()


async def test_topic_filter() -> None:
    hub = EventHub()
    sub = hub.subscribe(1, parse_topics('case, giveaway'))
    hub.publish(1, 'music.state')
    hub.publish(1, 'case.created', {'case_index': 7})
    assert (await asyncio.wait_for(sub.get(), T))['type'] == 'case.created'
    assert sub.queue.empty()


async def test_overflow_drops_oldest_and_never_blocks() -> None:
    hub = EventHub()
    sub = hub.subscribe(1)
    for i in range(QUEUE_SIZE + 5):
        hub.publish(1, 'music.state', {'i': i})
    assert sub.queue.qsize() == QUEUE_SIZE
    assert sub.dropped == 5
    assert (await asyncio.wait_for(sub.get(), T))['data']['i'] == 5  # 0-4 were dropped


async def test_unsubscribe_stops_delivery_and_cleans_up() -> None:
    hub = EventHub()
    sub = hub.subscribe(1)
    assert hub.has_subscribers(1)
    hub.unsubscribe(sub)
    hub.unsubscribe(sub)  # idempotent
    assert not hub.has_subscribers(1)
    hub.publish(1, 'music.state')
    assert sub.queue.empty()


def test_format_sse_is_one_data_message() -> None:
    raw = format_sse({'type': 'a', 'data': {}})
    assert raw.endswith(b'\n\n') and json.loads(raw[len(b'data: '):]) == {'type': 'a', 'data': {}}


async def test_stream_forwards_events_heartbeats_and_unsubscribes_on_close() -> None:
    bot = SimpleNamespace(events=EventHub())
    stream = event_stream(bot, 1, parse_topics('music'), heartbeat=0.05)
    assert await asyncio.wait_for(anext(stream), T) == b'retry: 5000\n\n'
    assert bot.events.has_subscribers(1)
    assert await asyncio.wait_for(anext(stream), T) == b': heartbeat\n\n'
    bot.events.publish(1, 'music.state')
    assert b'music.state' in await asyncio.wait_for(anext(stream), T)
    await stream.aclose()
    assert not bot.events.has_subscribers(1)


def test_events_route_is_registered_and_authenticated() -> None:
    from app.internal_api.dependencies import verify_token
    from app.internal_api.routers import ALL_ROUTERS
    from app.internal_api.routers.events import router as events_router

    routes = [r for router in ALL_ROUTERS for r in router.routes if getattr(r, 'path', '') == '/guilds/{guild_id}/events']
    assert len(routes) == 1
    assert any(d.dependency is verify_token for d in events_router.dependencies)
