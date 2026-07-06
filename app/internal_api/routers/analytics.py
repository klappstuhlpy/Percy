"""Internal API analytics endpoints: a flexible time-series query and a headline summary.

This is the reworked, query-driven successor to the bespoke stat endpoints — one endpoint
serves any supported metric at any supported range/granularity, zero-filled into a
contiguous series ready to plot. Metrics are sourced from the ``commands`` table, the daily
``xp_history`` snapshots, and (for member growth) the live member cache.
"""
from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.services import METRICS, fill_buckets, resolve_granularity, resolve_range

from ..dependencies import BotDep, GuildDep, verify_token

router = APIRouter(
    prefix="/guilds/{guild_id}/analytics",
    tags=["Analytics"],
    dependencies=[Depends(verify_token)],
)


def _as_utc(dt: datetime.datetime) -> datetime.datetime:
    """Attach UTC to a naive DB timestamp (``commands.used`` / ``xp_history`` are naive UTC)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.UTC)


@router.get("/series")
async def get_series(
    guild: GuildDep,
    bot: BotDep,
    metric: str = Query(..., description="One of: commands, command_failures, xp, members"),
    range_: str = Query("30d", alias="range", description="24h, 7d, 30d, 90d, or 1y"),
    granularity: str | None = Query(None, description="hour, day, or week (auto if omitted)"),
) -> dict:
    """A zero-filled time-series for one metric over a range.

    Returns ``{metric, range, granularity, total, points: [{bucket, value}]}``. ``points`` is
    contiguous — buckets with no activity report ``0`` — so a chart never shows a false gap.
    """
    if metric not in METRICS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown metric {metric!r}; expected one of {sorted(METRICS)}",
        )
    try:
        days = resolve_range(range_)
        gran = resolve_granularity(granularity, days)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    now = datetime.datetime.now(datetime.UTC)
    values: dict[datetime.datetime, float] = {}

    if metric in ("commands", "command_failures"):
        rows = await bot.db.stats.get_command_series(
            guild.id, days=days, granularity=gran, failures_only=(metric == "command_failures"),
        )
        values = {_as_utc(r["bucket"]): r["value"] for r in rows}

    elif metric == "xp":
        # xp_history is a daily cumulative snapshot; weekly/hourly buckets would sum
        # cumulative totals nonsensically, so this metric is always daily.
        gran = "day"
        rows = await bot.db.leveling.get_xp_history(guild.id, days=days)
        values = {
            datetime.datetime(r["day"].year, r["day"].month, r["day"].day, tzinfo=datetime.UTC): r["total_xp"]
            for r in rows
        }

    else:  # members — new joins per bucket, from the live member cache
        cutoff = now - datetime.timedelta(days=days)
        for member in guild.members:
            joined = member.joined_at
            if joined is not None and joined >= cutoff:
                key = _as_utc(joined)
                values[key] = values.get(key, 0) + 1

    points = fill_buckets(values, days=days, granularity=gran, now=now)
    return {
        "metric": metric,
        "range": range_,
        "granularity": gran,
        "total": sum(p["value"] for p in points),
        "points": points,
    }


@router.get("/summary")
async def get_summary(
    guild: GuildDep,
    bot: BotDep,
    range_: str = Query("30d", alias="range", description="24h, 7d, 30d, 90d, or 1y"),
) -> dict:
    """Headline numbers for a range, each with a delta vs the preceding equal window.

    Covers command volume, new-member growth, and the top commands — the at-a-glance row
    above the charts. Deltas are ``current - previous`` (previous = the window before this one).
    """
    try:
        days = resolve_range(range_)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    now = datetime.datetime.now(datetime.UTC)
    cutoff = now - datetime.timedelta(days=days)
    prev_cutoff = now - datetime.timedelta(days=days * 2)

    commands_current = await bot.db.stats.get_command_total(guild.id, days=days)
    commands_window2 = await bot.db.stats.get_command_total(guild.id, days=days * 2)
    commands_previous = max(commands_window2 - commands_current, 0)

    top_rows = await bot.db.stats.get_command_usage(guild_id=guild.id, days=days, group_by="command", limit=5)
    top_commands = [{"command": r["command"], "uses": r["uses"]} for r in top_rows]

    joins = [m.joined_at for m in guild.members if m.joined_at is not None]
    new_current = sum(1 for j in joins if j >= cutoff)
    new_previous = sum(1 for j in joins if prev_cutoff <= j < cutoff)

    return {
        "range": range_,
        "commands": {"value": commands_current, "delta": commands_current - commands_previous},
        "new_members": {"value": new_current, "delta": new_current - new_previous},
        "member_count": guild.member_count,
        "top_commands": top_commands,
    }
