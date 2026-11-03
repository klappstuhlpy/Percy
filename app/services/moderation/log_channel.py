"""What the owner's private log channel (the stats webhook) posts.

The channel is readable by moderators and friends of the owner, so everything here deals
in aggregates and state changes only: no tokens, no gateway session ids, no message
content, no member names or ids. The ``Stats`` cog gathers the numbers and sends; this
module decides *what* is worth posting and how it reads, free of Discord.

* :func:`rewrite_gateway_line` turns discord.py's raw gateway log lines into short human
  ones and drops every line it does not know (an unknown line could carry anything).
* :class:`BurstCollapser` keeps a reconnect storm to one line per window.
* :class:`HealthTracker` reports a component going down once and recovering once.
* :func:`startup_fields` / :func:`daily_summary_fields` format the cards.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = (
    "BurstCollapser",
    "DailyNumbers",
    "HealthChange",
    "HealthTracker",
    "StartupFacts",
    "daily_summary_fields",
    "describe_health_change",
    "feed_is_healthy",
    "format_duration",
    "rewrite_gateway_line",
    "startup_fields",
)


def format_duration(seconds: float) -> str:
    """A compact duration: ``12 s``, ``4 min``, ``3 h 12 min``, ``2 d 4 h``."""
    seconds = max(0, round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, _ = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if hours else f"{days} d"


# -- gateway lines -----------------------------------------------------------

# discord.py's own wording (gateway.py / client.py). Each pattern names only the shard and
# a number; the session id in the RESUMED / connected lines is never captured.
_GATEWAY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^Shard ID (?P<shard>\S+) has connected to Gateway"), "connected"),
    (re.compile(r"^Shard ID (?P<shard>\S+) has successfully RESUMED"), "resumed"),
    (re.compile(r"^Shard ID (?P<shard>\S+) session has been invalidated"), "invalidated"),
    (re.compile(r"^Shard ID (?P<shard>\S+) has stopped responding"), "unresponsive"),
    (re.compile(r"^Shard ID (?P<shard>\S+) heartbeat blocked for more than (?P<n>[\d.]+) seconds"), "blocked"),
    (re.compile(r"^Can't keep up, shard ID (?P<shard>\S+) websocket is (?P<n>[\d.]+)s behind"), "behind"),
    (re.compile(r"^WebSocket in shard ID (?P<shard>\S+) is ratelimited, waiting (?P<n>[\d.]+) seconds"), "ratelimited"),
    (re.compile(r"^Attempting a reconnect in (?P<n>[\d.]+)s"), "reconnecting"),
)


def rewrite_gateway_line(message: str, *, downtime: float | None = None) -> str | None:
    """Turn a raw gateway log line into a short human one, or ``None`` to drop it.

    ``downtime`` is how long the connection was down before this line (when the caller
    knows it); it is only used by the lines that mark the connection coming back.
    """
    for pattern, kind in _GATEWAY_PATTERNS:
        match = pattern.match(message)
        if match is None:
            continue
        groups = match.groupdict()
        shard = groups.get("shard")
        where = f" (shard {shard})" if shard not in (None, "None") else ""
        n = format_duration(float(groups["n"])) if groups.get("n") else ""
        after = f" after {format_duration(downtime)}" if downtime is not None and downtime >= 0 else ""
        match kind:
            case "connected":
                return f"New gateway session started{where}{after}" if after else f"Gateway session started{where}"
            case "resumed":
                return f"Reconnected{where}{after}"
            case "invalidated":
                return f"Gateway session expired{where}, starting a new one"
            case "unresponsive":
                return f"Gateway stopped responding{where}, reconnecting"
            case "blocked":
                return f"Heartbeat blocked for {n}{where}: the event loop is busy"
            case "behind":
                return f"Gateway running {n} behind{where}"
            case "ratelimited":
                return f"Gateway rate limited{where}, waiting {n}"
            case _:
                return f"Connection lost, reconnecting in {n}"
    return None


@dataclass(slots=True)
class BurstCollapser:
    """At most one line per ``window`` seconds; the rest are folded into a count.

    :meth:`push` sends a line straight away when the window is open and holds it otherwise.
    :meth:`flush` (called periodically) sends the newest held line once the window has
    passed, saying how many earlier ones it folded.
    """

    window: float = 60.0
    _last_sent: float | None = None
    _held: list[str] = field(default_factory=list)

    def _is_open(self, now: float) -> bool:
        return self._last_sent is None or now - self._last_sent >= self.window

    def _emit(self, line: str, folded: int, now: float) -> str:
        self._held.clear()
        self._last_sent = now
        if folded:
            line += f" (+{folded} earlier gateway event{'s' if folded != 1 else ''} folded)"
        return line

    def push(self, line: str, now: float) -> str | None:
        if self._is_open(now):
            return self._emit(line, len(self._held), now)
        self._held.append(line)
        return None

    def flush(self, now: float) -> str | None:
        if not self._held or not self._is_open(now):
            return None
        return self._emit(self._held[-1], len(self._held) - 1, now)


# -- health state changes ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class HealthChange:
    key: str
    label: str
    ok: bool
    detail: str | None
    #: How long the component was down, on a recovery whose start was seen.
    down_for: float | None
    #: What "not ok" means: "down" for an outage, "high" for a reading over its limit.
    problem: str = "down"


@dataclass(slots=True)
class _State:
    ok: bool
    since: float
    streak: int = 0


@dataclass(slots=True)
class HealthTracker:
    """Reports a component going down once and coming back once, never while unchanged.

    The first observation of a key only records its state: what is down at boot is on the
    startup card, and a feed that was already red does not repost on every restart.
    ``confirm`` is how many consecutive contrary observations flip the state, so a
    single slow sample does not count as "sustained".
    """

    _states: dict[str, _State] = field(default_factory=dict)

    def observe(
        self,
        key: str,
        ok: bool,
        *,
        now: float,
        label: str | None = None,
        detail: str | None = None,
        confirm: int = 1,
        problem: str = "down",
    ) -> HealthChange | None:
        state = self._states.get(key)
        if state is None:
            self._states[key] = _State(ok=ok, since=now)
            return None
        if ok == state.ok:
            state.streak = 0
            return None
        state.streak += 1
        if state.streak < confirm:
            return None
        down_for = now - state.since if ok else None
        self._states[key] = _State(ok=ok, since=now)
        return HealthChange(key=key, label=label or key, ok=ok, detail=detail, down_for=down_for, problem=problem)


def feed_is_healthy(status: str) -> bool:
    """A feed is healthy only on a bare 2xx/3xx code; ``200 <parse error>`` is not."""
    return status.isdigit() and status[0] in "23"


def describe_health_change(change: HealthChange) -> str:
    """One line for a health change (the cog adds the emoji)."""
    if change.ok:
        high = change.problem == "high"
        text = f"**{change.label}** is back to normal" if high else f"**{change.label}** recovered"
        if change.down_for is not None:
            text += f" after {format_duration(change.down_for)} {'high' if high else 'down'}"
        return text
    text = f"**{change.label}** is {change.problem}"
    if change.detail:
        text += f": {change.detail}"
    return text


# -- cards -------------------------------------------------------------------


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _ms(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "n/a"
    return f"{seconds * 1000:.0f} ms"


@dataclass(frozen=True, slots=True)
class StartupFacts:
    version: str
    guilds: int
    members: int
    shards: int
    latency: float | None
    startup_seconds: float | None
    lavalink: bool
    #: ``None`` when AI is switched off by config.
    ai: bool | None
    db_ok: bool
    schema_version: int | None
    pending_timers: int | None


def startup_fields(facts: StartupFacts) -> list[tuple[str, str]]:
    """``(name, value)`` embed fields for the startup card."""
    ai = "disabled" if facts.ai is None else "reachable" if facts.ai else "not reachable"
    db = "ok" if facts.db_ok else "not reachable"
    if facts.schema_version is not None:
        db += f" (schema V{facts.schema_version})"
    started = format_duration(facts.startup_seconds) if facts.startup_seconds is not None else "n/a"
    timers = str(facts.pending_timers) if facts.pending_timers is not None else "n/a"
    return [
        ("Version", f"`{facts.version}`"),
        ("Guilds", f"{facts.guilds:,}"),
        ("Members", f"{facts.members:,}"),
        ("Shards", str(facts.shards)),
        ("Latency", _ms(facts.latency)),
        ("Startup", started),
        ("Lavalink", "connected" if facts.lavalink else "not connected"),
        ("AI", ai),
        ("Database", db),
        ("Pending timers", timers),
    ]


@dataclass(frozen=True, slots=True)
class DailyNumbers:
    guilds_joined: int
    guilds_left: int
    guilds_total: int
    #: Whether the guild counts cover the full day (False after a restart within it).
    guilds_full_day: bool
    commands_run: int
    command_errors: int
    top_commands: Sequence[tuple[str, int]]
    mod_cases: int
    uptime_seconds: float
    latency_samples: Sequence[float]
    memory_mib: float
    cpu_percent: float


def daily_summary_fields(numbers: DailyNumbers) -> list[tuple[str, str]]:
    """``(name, value)`` embed fields for the daily summary."""
    guilds = f"+{numbers.guilds_joined} / -{numbers.guilds_left} (now {numbers.guilds_total:,})"
    if not numbers.guilds_full_day:
        guilds += "\n-# counted since the last restart"
    commands = f"{numbers.commands_run:,} run, {numbers.command_errors:,} failed"
    top = "\n".join(f"`{name}` {uses:,}" for name, uses in numbers.top_commands[:5]) or "none"
    samples = [s for s in numbers.latency_samples if math.isfinite(s)]
    latency = _ms(sum(samples) / len(samples)) if samples else "n/a"
    return [
        ("Guilds", guilds),
        ("Commands", commands),
        ("Top commands", top),
        ("Mod cases", f"{numbers.mod_cases:,}"),
        ("Uptime", format_duration(numbers.uptime_seconds)),
        ("Avg latency", latency),
        ("Process", f"{numbers.memory_mib:.0f} MiB, {numbers.cpu_percent:.1f}% CPU"),
    ]
