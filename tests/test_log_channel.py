"""Tests for :mod:`app.services.log_channel` (what the private log channel posts)."""

from __future__ import annotations

from app.services import (
    BurstCollapser,
    DailyNumbers,
    HealthTracker,
    StartupFacts,
    daily_summary_fields,
    describe_health_change,
    format_duration,
    rewrite_gateway_line,
    startup_fields,
)

SESSION = "2c0e1f6a9b8d7c6e5f4a3b2c1d0e9f8a"


# -- gateway lines -----------------------------------------------------------


def test_resumed_line_never_carries_the_session_id() -> None:
    line = rewrite_gateway_line(f"Shard ID 0 has successfully RESUMED session {SESSION}.", downtime=12.4)
    assert line == "Reconnected (shard 0) after 12 s"
    assert SESSION not in line


def test_unsharded_bot_has_no_shard_suffix() -> None:
    assert rewrite_gateway_line(f"Shard ID None has successfully RESUMED session {SESSION}.") == "Reconnected"


def test_connected_line_with_and_without_downtime() -> None:
    raw = f"Shard ID None has connected to Gateway (Session ID: {SESSION})."
    assert rewrite_gateway_line(raw) == "Gateway session started"
    assert rewrite_gateway_line(raw, downtime=95) == "New gateway session started after 1 min"


def test_other_known_lines() -> None:
    assert rewrite_gateway_line("Shard ID 1 session has been invalidated.") == (
        "Gateway session expired (shard 1), starting a new one"
    )
    assert rewrite_gateway_line("Shard ID None heartbeat blocked for more than 20 seconds.\nLoop thread traceback") == (
        "Heartbeat blocked for 20 s: the event loop is busy"
    )
    assert rewrite_gateway_line("Can't keep up, shard ID None websocket is 12.3s behind.") == "Gateway running 12 s behind"
    assert rewrite_gateway_line("Attempting a reconnect in 4.21s") == "Connection lost, reconnecting in 4 s"
    assert rewrite_gateway_line("WebSocket in shard ID 0 is ratelimited, waiting 3.00 seconds") == (
        "Gateway rate limited (shard 0), waiting 3 s"
    )


def test_unknown_lines_are_dropped() -> None:
    assert rewrite_gateway_line("logging in using static token") is None
    assert rewrite_gateway_line(f"Something new about session {SESSION}") is None


# -- burst collapsing --------------------------------------------------------


def test_first_line_goes_out_and_a_burst_folds_into_one() -> None:
    burst = BurstCollapser(window=60)
    assert burst.push("a", now=0) == "a"
    assert burst.push("b", now=5) is None
    assert burst.push("c", now=10) is None
    assert burst.flush(now=30) is None  # window still closed
    assert burst.flush(now=61) == "c (+1 earlier gateway event folded)"
    assert burst.flush(now=200) is None  # nothing held


def test_push_after_the_window_carries_the_held_count() -> None:
    burst = BurstCollapser(window=60)
    burst.push("a", now=0)
    burst.push("b", now=1)
    burst.push("c", now=2)
    assert burst.push("d", now=70) == "d (+2 earlier gateway events folded)"
    assert burst.push("e", now=71) is None


# -- health state changes ----------------------------------------------------


def test_first_observation_is_silent_and_unchanged_state_never_repeats() -> None:
    health = HealthTracker()
    assert health.observe("ll", True, now=0) is None
    assert health.observe("ll", True, now=60) is None

    down = health.observe("ll", False, now=100, label="Lavalink", detail="node disconnected")
    assert down is not None and not down.ok
    assert describe_health_change(down) == "**Lavalink** is down: node disconnected"
    assert health.observe("ll", False, now=160) is None
    assert health.observe("ll", False, now=220) is None

    up = health.observe("ll", True, now=400, label="Lavalink")
    assert up is not None and up.ok and up.down_for == 300
    assert describe_health_change(up) == "**Lavalink** recovered after 5 min down"
    assert health.observe("ll", True, now=460) is None


def test_initially_down_is_silent_but_its_recovery_is_reported() -> None:
    health = HealthTracker()
    assert health.observe("feed:x", False, now=0) is None
    change = health.observe("feed:x", True, now=30, label="News feed X")
    assert change is not None and change.ok


def test_confirm_needs_consecutive_observations() -> None:
    health = HealthTracker()
    health.observe("latency", True, now=0)
    assert health.observe("latency", False, now=60, confirm=3) is None
    assert health.observe("latency", True, now=120, confirm=3) is None  # streak broken
    assert health.observe("latency", False, now=180, confirm=3) is None
    assert health.observe("latency", False, now=240, confirm=3) is None
    change = health.observe("latency", False, now=300, confirm=3, label="Gateway latency", detail="1500 ms")
    assert change is not None and not change.ok


# -- cards -------------------------------------------------------------------


def test_format_duration() -> None:
    assert format_duration(12.4) == "12 s"
    assert format_duration(240) == "4 min"
    assert format_duration(3 * 3600) == "3 h"
    assert format_duration(3 * 3600 + 12 * 60) == "3 h 12 min"
    assert format_duration(2 * 86400 + 4 * 3600) == "2 d 4 h"
    assert format_duration(-5) == "0 s"


def test_startup_fields() -> None:
    fields = dict(startup_fields(StartupFacts(
        version="2.7.0+abc123", guilds=1234, members=56789, shards=1, latency=0.0423,
        startup_seconds=8.2, lavalink=True, ai=None, db_ok=True, schema_version=44, pending_timers=17,
    )))
    assert fields["Version"] == "`2.7.0+abc123`"
    assert fields["Members"] == "56,789"
    assert fields["Latency"] == "42 ms"
    assert fields["Startup"] == "8 s"
    assert fields["AI"] == "disabled"
    assert fields["Database"] == "ok (schema V44)"
    assert fields["Pending timers"] == "17"


def test_startup_fields_unknowns() -> None:
    fields = dict(startup_fields(StartupFacts(
        version="2.7.0", guilds=0, members=0, shards=1, latency=float("inf"), startup_seconds=None,
        lavalink=False, ai=False, db_ok=False, schema_version=None, pending_timers=None,
    )))
    assert fields["Latency"] == "n/a"
    assert fields["AI"] == "not reachable"
    assert fields["Database"] == "not reachable"
    assert fields["Lavalink"] == "not connected"


def _numbers(**overrides: object) -> DailyNumbers:
    values: dict = {
        "guilds_joined": 3, "guilds_left": 1, "guilds_total": 1236, "guilds_full_day": True,
        "commands_run": 4210, "command_errors": 12, "top_commands": [("play", 900), ("rank", 600)],
        "mod_cases": 7, "uptime_seconds": 3 * 86400 + 5 * 3600, "latency_samples": [0.04, 0.06, float("inf")],
        "memory_mib": 512.4, "cpu_percent": 3.25,
    }
    values.update(overrides)
    return DailyNumbers(**values)


def test_daily_summary_fields() -> None:
    fields = dict(daily_summary_fields(_numbers()))
    assert fields["Guilds"] == "+3 / -1 (now 1,236)"
    assert fields["Commands"] == "4,210 run, 12 failed"
    assert fields["Top commands"] == "`play` 900\n`rank` 600"
    assert fields["Mod cases"] == "7"
    assert fields["Uptime"] == "3 d 5 h"
    assert fields["Avg latency"] == "50 ms"
    assert fields["Process"] == "512 MiB, 3.2% CPU"


def test_daily_summary_partial_day_and_empty() -> None:
    fields = dict(daily_summary_fields(_numbers(guilds_full_day=False, top_commands=[], latency_samples=[])))
    assert fields["Guilds"].endswith("counted since the last restart")
    assert fields["Top commands"] == "none"
    assert fields["Avg latency"] == "n/a"
