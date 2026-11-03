"""In-process pub/sub behind the dashboard's server-sent event stream.

Pure and Discord-free. Publishers (cogs, the internal API) call :meth:`EventHub.publish`, which
is synchronous and never blocks: each subscriber owns a bounded queue and, when it is full, the
*oldest* event is dropped, so a slow browser can never stall the bot. Events are tiny
``{type, data}`` hints; the client refetches the heavy payload over plain HTTP.

The topic of an event is the part of its type before the first dot (``music.state`` -> ``music``),
which is what a subscriber's ``topics`` filter matches.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

__all__ = ('EventHub', 'Subscription', 'format_sse', 'parse_topics')

#: Events a subscriber may fall behind by before the oldest are dropped.
QUEUE_SIZE = 64


def parse_topics(raw: str | None) -> frozenset[str] | None:
    """``"music, cases"`` -> ``{"music", "cases"}``; empty/None means every topic."""
    topics = frozenset(t.strip() for t in (raw or '').split(',') if t.strip())
    return topics or None


def format_sse(event: dict[str, Any]) -> bytes:
    """One unnamed SSE message whose data is the event's JSON."""
    return b'data: ' + json.dumps(event, separators=(',', ':'), default=str).encode() + b'\n\n'


class Subscription:
    """One listener: a bounded queue plus an optional topic filter."""

    __slots__ = ('dropped', 'guild_id', 'queue', 'topics')

    def __init__(self, guild_id: int, topics: frozenset[str] | None, size: int = QUEUE_SIZE) -> None:
        self.guild_id = guild_id
        self.topics = topics
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=size)
        self.dropped = 0

    def wants(self, event_type: str) -> bool:
        return self.topics is None or event_type.split('.', 1)[0] in self.topics

    def offer(self, event: dict[str, Any]) -> None:
        if not self.wants(event['type']):
            return
        if self.queue.full():
            self.queue.get_nowait()  # drop-oldest: the newest state is the one that matters
            self.dropped += 1
        self.queue.put_nowait(event)

    async def get(self) -> dict[str, Any]:
        return await self.queue.get()


class EventHub:
    """Per-guild fan-out of events to the live streams."""

    def __init__(self) -> None:
        self._subs: dict[int, set[Subscription]] = defaultdict(set)

    def subscribe(self, guild_id: int, topics: frozenset[str] | None = None) -> Subscription:
        sub = Subscription(guild_id, topics)
        self._subs[guild_id].add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        subs = self._subs.get(sub.guild_id)
        if subs is not None:
            subs.discard(sub)
            if not subs:
                del self._subs[sub.guild_id]

    def has_subscribers(self, guild_id: int) -> bool:
        return guild_id in self._subs

    def publish(self, guild_id: int, type: str, data: dict[str, Any] | None = None) -> None:
        """Deliver to every subscriber of ``guild_id``; cheap no-op when nobody listens."""
        subs = self._subs.get(guild_id)
        if not subs:
            return
        event = {'type': type, 'data': data or {}}
        for sub in tuple(subs):
            sub.offer(event)
