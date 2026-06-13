from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

__all__ = ("QueryTracker", "SlowQuery")

log = logging.getLogger(__name__)


@dataclass(slots=True)
class SlowQuery:
    """A single slow query record."""
    query: str
    duration_ms: float
    timestamp: float = field(default_factory=time.time)

    @property
    def query_preview(self) -> str:
        """First 120 chars of the query for display."""
        return self.query[:120] + ("..." if len(self.query) > 120 else "")


class QueryTracker:
    """Tracks database query execution times and logs slow queries.

    Usage::

        tracker = QueryTracker(threshold_ms=100)
        tracker.record("SELECT ...", duration_ms=150.3)
        # -> logs a warning and stores it in the slow queries buffer
    """

    def __init__(self, *, threshold_ms: float = 100.0, buffer_size: int = 50) -> None:
        self._threshold_ms = threshold_ms
        self._slow_queries: deque[SlowQuery] = deque(maxlen=buffer_size)
        self._total_queries: int = 0
        self._total_time_ms: float = 0.0

    def record(self, query: str, duration_ms: float) -> None:
        """Record a query execution. Logs if it exceeds the threshold."""
        self._total_queries += 1
        self._total_time_ms += duration_ms

        if duration_ms >= self._threshold_ms:
            slow = SlowQuery(query=query, duration_ms=duration_ms)
            self._slow_queries.append(slow)
            log.warning(
                "Slow query (%.1fms): %.120s",
                duration_ms,
                query.replace("\n", " "),
            )

    @property
    def total_queries(self) -> int:
        return self._total_queries

    @property
    def avg_duration_ms(self) -> float:
        if self._total_queries == 0:
            return 0.0
        return self._total_time_ms / self._total_queries

    def slow_queries(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the most recent slow queries for inspection."""
        sorted_q = sorted(self._slow_queries, key=lambda q: q.duration_ms, reverse=True)
        return [
            {"query": q.query_preview, "duration_ms": round(q.duration_ms, 2)}
            for q in sorted_q[:limit]
        ]

    def summary(self) -> dict[str, Any]:
        """Full metrics summary."""
        return {
            "total_queries": self._total_queries,
            "avg_duration_ms": round(self.avg_duration_ms, 2),
            "slow_query_count": len(self._slow_queries),
            "threshold_ms": self._threshold_ms,
            "slowest_queries": self.slow_queries(5),
        }
