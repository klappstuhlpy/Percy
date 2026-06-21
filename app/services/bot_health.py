"""Bot health assessment.

Extracted from the ``stats`` cog's ``bothealth`` command. Reading the raw runtime
values -- the asyncpg pool's holders, event-loop tasks, spam state, process stats --
is inherently runtime/Discord-bound and stays in the cog. The *analysis* of those
values is pure logic and lives here, free of Discord and unit-testable: which pool
connections look questionable, how many warnings that adds up to, and the resulting
health level.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = (
    "BotHealthReport",
    "ConnectionState",
    "HealthLevel",
    "LavalinkMetrics",
    "assess_bot_health",
    "parse_lavalink_metrics",
    "parse_prometheus_samples",
)

# Thresholds, named to replace the original inline magic numbers.
COMMAND_WAITER_WARNING_THRESHOLD = 8
UNHEALTHY_WARNING_THRESHOLD = 9


class HealthLevel(Enum):
    """Overall health verdict; the cog maps each level to an embed colour."""

    HEALTHY = "healthy"
    WARNING = "warning"
    UNHEALTHY = "unhealthy"


@dataclass(slots=True)
class ConnectionState:
    """One asyncpg pool holder's observable state."""

    generation: int
    in_use: bool
    is_closed: bool

    def is_questionable(self, current_generation: int) -> bool:
        """A holder is questionable if it is in use or from an older pool generation."""
        return self.in_use or self.generation != current_generation


@dataclass(slots=True)
class BotHealthReport:
    """Derived health metrics for the ``bothealth`` report."""

    questionable_connections: int
    warnings: int
    level: HealthLevel


def assess_bot_health(
    connections: list[ConnectionState],
    *,
    current_generation: int,
    is_being_spammed: bool,
    command_waiters: int,
    has_failed_inner_tasks: bool,
    global_rate_limit: bool,
) -> BotHealthReport:
    """Aggregate raw runtime observations into a health verdict.

    Mirrors the original cog logic exactly: each questionable connection counts as one
    warning; active spammers, any failed inner task, and a backed-up command queue
    (>= 8 waiters) each add one more. The level is UNHEALTHY when a global rate limit is
    active or warnings reach 9, WARNING when spammers or a backed-up command queue are
    present, and HEALTHY otherwise.
    """
    questionable = sum(1 for c in connections if c.is_questionable(current_generation))

    warnings = questionable
    if is_being_spammed:
        warnings += 1
    if has_failed_inner_tasks:
        warnings += 1
    backed_up_commands = command_waiters >= COMMAND_WAITER_WARNING_THRESHOLD
    if backed_up_commands:
        warnings += 1

    if global_rate_limit or warnings >= UNHEALTHY_WARNING_THRESHOLD:
        level = HealthLevel.UNHEALTHY
    elif is_being_spammed or backed_up_commands:
        level = HealthLevel.WARNING
    else:
        level = HealthLevel.HEALTHY

    return BotHealthReport(questionable_connections=questionable, warnings=warnings, level=level)


# -- Lavalink Prometheus metrics ------------------------------------------------
#
# Lavalink exposes a flat set of gauges at its ``/metrics`` endpoint (Prometheus text
# exposition format) when ``metrics.prometheus`` is enabled. Fetching the text is
# runtime/IO work and stays in the cog; turning the raw exposition payload into typed
# numbers is pure logic and lives here.


@dataclass(slots=True)
class LavalinkMetrics:
    """The Lavalink-specific gauges scraped from its ``/metrics`` endpoint.

    The two CPU loads are fractions in ``[0, 1]`` (the gauge name says ``percentage``
    but Lavalink writes the raw fraction); callers multiply by 100 to display a percent.
    """

    players: int
    playing_players: int
    uptime_seconds: float
    memory_used_bytes: float
    memory_allocated_bytes: float
    memory_reservable_bytes: float
    memory_free_bytes: float
    cpu_cores: int
    system_load: float
    lavalink_load: float

    @property
    def memory_used_ratio(self) -> float:
        """Used memory as a fraction of the JVM's currently allocated heap (``[0, 1]``)."""
        return self.memory_used_bytes / self.memory_allocated_bytes if self.memory_allocated_bytes else 0.0


def parse_prometheus_samples(text: str) -> dict[str, float]:
    """Parse a Prometheus text-exposition payload into ``{metric_name: value}``.

    Only *unlabelled* samples are kept — sufficient for Lavalink's flat gauges. Comment
    lines (``# HELP`` / ``# TYPE``), labelled series (``name{le="0.025",} 12.0``) and any
    value that is not a float are skipped. Scientific notation (e.g. ``5.81E7``) is handled
    by :func:`float`.
    """
    samples: dict[str, float] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "{" in line:
            continue
        name, _, value = line.partition(" ")
        if not name or not value:
            continue
        try:
            samples[name] = float(value)
        except ValueError:
            continue
    return samples


def parse_lavalink_metrics(text: str) -> LavalinkMetrics | None:
    """Extract :class:`LavalinkMetrics` from a Lavalink ``/metrics`` payload.

    Returns ``None`` when the payload is missing the Lavalink gauges (e.g. a non-Lavalink
    endpoint, or metrics disabled) so the caller can fall back gracefully.
    """
    samples = parse_prometheus_samples(text)
    if "lavalink_uptime_milliseconds" not in samples:
        return None

    return LavalinkMetrics(
        players=int(samples.get("lavalink_players_total", 0)),
        playing_players=int(samples.get("lavalink_playing_players_total", 0)),
        uptime_seconds=samples.get("lavalink_uptime_milliseconds", 0.0) / 1000.0,
        memory_used_bytes=samples.get("lavalink_memory_used_bytes", 0.0),
        memory_allocated_bytes=samples.get("lavalink_memory_allocated_bytes", 0.0),
        memory_reservable_bytes=samples.get("lavalink_memory_reservable_bytes", 0.0),
        memory_free_bytes=samples.get("lavalink_memory_free_bytes", 0.0),
        cpu_cores=int(samples.get("lavalink_cpu_cores", 0)),
        system_load=samples.get("lavalink_cpu_system_load_percentage", 0.0),
        lavalink_load=samples.get("lavalink_cpu_lavalink_load_percentage", 0.0),
    )
