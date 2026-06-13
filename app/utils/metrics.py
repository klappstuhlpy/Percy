from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

__all__ = ("MetricsCollector",)


@dataclass(slots=True)
class CommandMetric:
    """A single command execution measurement."""
    command: str
    duration_ms: float
    guild_id: int | None
    user_id: int
    success: bool
    timestamp: float = field(default_factory=time.time)


class MetricsCollector:
    """Lightweight in-memory metrics collector for command latency and cache stats.

    Keeps a rolling window of recent command executions and exposes
    summary statistics that the internal API can surface to the dashboard.
    """

    def __init__(self, *, window_size: int = 1000) -> None:
        self._commands: deque[CommandMetric] = deque(maxlen=window_size)
        self._error_counts: defaultdict[str, int] = defaultdict(int)
        self._total_commands: int = 0

    def record_command(
        self,
        command: str,
        duration_ms: float,
        *,
        guild_id: int | None = None,
        user_id: int = 0,
        success: bool = True,
    ) -> None:
        """Record a command execution."""
        self._total_commands += 1
        self._commands.append(CommandMetric(
            command=command,
            duration_ms=duration_ms,
            guild_id=guild_id,
            user_id=user_id,
            success=success,
        ))

    def record_error(self, error_type: str) -> None:
        """Increment the counter for a given error type."""
        self._error_counts[error_type] += 1

    @property
    def total_commands(self) -> int:
        return self._total_commands

    def latency_percentiles(self) -> dict[str, float]:
        """Return p50, p95, p99 latency in milliseconds from the rolling window."""
        if not self._commands:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0}

        durations = sorted(m.duration_ms for m in self._commands)
        n = len(durations)

        def percentile(p: float) -> float:
            idx = int(n * p / 100)
            return durations[min(idx, n - 1)]

        return {
            "p50": round(percentile(50), 2),
            "p95": round(percentile(95), 2),
            "p99": round(percentile(99), 2),
        }

    def slowest_commands(self, top_n: int = 10) -> list[dict[str, float | str]]:
        """Return the top N slowest commands from the rolling window."""
        sorted_cmds = sorted(self._commands, key=lambda m: m.duration_ms, reverse=True)
        return [
            {"command": m.command, "duration_ms": round(m.duration_ms, 2)}
            for m in sorted_cmds[:top_n]
        ]

    def error_summary(self) -> dict[str, int]:
        """Return error counts by type."""
        return dict(self._error_counts)

    def summary(self) -> dict:
        """Return a full metrics summary for the dashboard."""
        return {
            "total_commands": self._total_commands,
            "window_size": len(self._commands),
            "latency": self.latency_percentiles(),
            "slowest": self.slowest_commands(5),
            "errors": self.error_summary(),
        }
