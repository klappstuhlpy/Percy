"""Tests for :mod:`app.services.bot_health`."""

from __future__ import annotations

from app.services import BotHealthReport, ConnectionState, HealthLevel, assess_bot_health

GEN = 5


def _healthy_conn() -> ConnectionState:
    # Same generation as the pool and not in use -> not questionable.
    return ConnectionState(generation=GEN, in_use=False, is_closed=False)


def _assess(connections: list[ConnectionState] | None = None, **kwargs: object) -> BotHealthReport:
    defaults = {
        "current_generation": GEN,
        "is_being_spammed": False,
        "command_waiters": 0,
        "has_failed_inner_tasks": False,
        "global_rate_limit": False,
    }
    defaults.update(kwargs)
    return assess_bot_health(connections or [], **defaults)  # type: ignore[arg-type]


def test_idle_bot_is_healthy_with_no_warnings() -> None:
    report = _assess([_healthy_conn(), _healthy_conn()])

    assert report == BotHealthReport(questionable_connections=0, warnings=0, level=HealthLevel.HEALTHY)


def test_in_use_or_stale_generation_connections_are_questionable() -> None:
    connections = [
        _healthy_conn(),
        ConnectionState(generation=GEN, in_use=True, is_closed=False),  # in use
        ConnectionState(generation=GEN - 1, in_use=False, is_closed=False),  # old generation
        ConnectionState(generation=GEN - 1, in_use=True, is_closed=True),  # both
    ]

    report = _assess(connections)

    assert report.questionable_connections == 3
    assert report.warnings == 3
    # Questionable connections alone never escalate past HEALTHY.
    assert report.level == HealthLevel.HEALTHY


def test_closed_state_alone_does_not_make_a_connection_questionable() -> None:
    report = _assess([ConnectionState(generation=GEN, in_use=False, is_closed=True)])

    assert report.questionable_connections == 0


def test_spammers_add_a_warning_and_raise_to_warning_level() -> None:
    report = _assess(is_being_spammed=True)

    assert report.warnings == 1
    assert report.level == HealthLevel.WARNING


def test_failed_inner_tasks_add_a_warning_but_not_a_level_bump() -> None:
    report = _assess(has_failed_inner_tasks=True)

    assert report.warnings == 1
    assert report.level == HealthLevel.HEALTHY


def test_command_queue_threshold_is_inclusive() -> None:
    below = _assess(command_waiters=7)
    assert below.warnings == 0
    assert below.level == HealthLevel.HEALTHY

    at = _assess(command_waiters=8)
    assert at.warnings == 1
    assert at.level == HealthLevel.WARNING


def test_global_rate_limit_forces_unhealthy() -> None:
    report = _assess(global_rate_limit=True)

    assert report.level == HealthLevel.UNHEALTHY


def test_nine_warnings_forces_unhealthy() -> None:
    # Nine questionable connections -> nine warnings -> UNHEALTHY even without a rate limit.
    connections = [ConnectionState(generation=GEN - 1, in_use=False, is_closed=False) for _ in range(9)]

    report = _assess(connections)

    assert report.questionable_connections == 9
    assert report.warnings == 9
    assert report.level == HealthLevel.UNHEALTHY


def test_warnings_accumulate_across_sources() -> None:
    connections = [ConnectionState(generation=GEN, in_use=True, is_closed=False)]  # 1 questionable

    report = _assess(
        connections,
        is_being_spammed=True,  # +1
        has_failed_inner_tasks=True,  # +1
        command_waiters=8,  # +1
    )

    assert report.warnings == 4
    # Below the UNHEALTHY threshold, but spammers/backed-up queue keep it at WARNING.
    assert report.level == HealthLevel.WARNING
