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
    "assess_bot_health",
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
