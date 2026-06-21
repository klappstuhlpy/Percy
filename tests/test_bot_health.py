"""Tests for :mod:`app.services.bot_health`."""

from __future__ import annotations

from app.services import BotHealthReport, ConnectionState, HealthLevel, assess_bot_health
from app.services.bot_health import parse_lavalink_metrics, parse_prometheus_samples

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


# -- Lavalink metrics parsing ---------------------------------------------------

# A trimmed but faithful sample of a real Lavalink ``/metrics`` payload, including the
# comment lines, scientific-notation values and labelled histogram series that the parser
# must skip.
LAVALINK_SAMPLE = """\
# HELP lavalink_players_total Total number of players connected.
# TYPE lavalink_players_total gauge
lavalink_players_total 3.0
# HELP lavalink_playing_players_total Number of players currently playing audio.
# TYPE lavalink_playing_players_total gauge
lavalink_playing_players_total 2.0
# HELP lavalink_uptime_milliseconds Uptime of the node in milliseconds.
# TYPE lavalink_uptime_milliseconds gauge
lavalink_uptime_milliseconds 5.8102206E7
lavalink_memory_free_bytes 1.24235832E8
lavalink_memory_used_bytes 1.68316872E8
lavalink_memory_allocated_bytes 2.92552704E8
lavalink_memory_reservable_bytes 2.147483648E9
lavalink_cpu_cores 6.0
lavalink_cpu_system_load_percentage 0.25
lavalink_cpu_lavalink_load_percentage 0.05
lavalink_gc_pauses_seconds_bucket{le="0.025",} 12.0
lavalink_gc_pauses_seconds_count 12.0
process_cpu_seconds_total 191.01
"""


def test_parse_prometheus_samples_skips_comments_and_labelled_series() -> None:
    samples = parse_prometheus_samples(LAVALINK_SAMPLE)

    assert samples["lavalink_players_total"] == 3.0
    assert samples["lavalink_uptime_milliseconds"] == 5.8102206e7  # scientific notation
    assert samples["process_cpu_seconds_total"] == 191.01
    # Labelled histogram series are ignored.
    assert "lavalink_gc_pauses_seconds_bucket" not in samples


def test_parse_lavalink_metrics_maps_units() -> None:
    metrics = parse_lavalink_metrics(LAVALINK_SAMPLE)

    assert metrics is not None
    assert metrics.players == 3
    assert metrics.playing_players == 2
    assert metrics.uptime_seconds == 58102.206  # milliseconds -> seconds
    assert metrics.cpu_cores == 6
    # Loads stay raw fractions; the cog multiplies by 100 for display.
    assert metrics.system_load == 0.25
    assert metrics.lavalink_load == 0.05
    assert metrics.memory_used_ratio == 168316872.0 / 292552704.0


def test_parse_lavalink_metrics_returns_none_without_lavalink_gauges() -> None:
    # A payload with only JVM/process metrics is not a Lavalink stats payload.
    assert parse_lavalink_metrics("process_cpu_seconds_total 191.01\n") is None
    assert parse_lavalink_metrics("") is None
