from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any, Literal

import discord

from app.cogs.games.models import Game
from app.core import Accent, NoticeView, make_notice
from app.utils import (
    fnumb,
    medal_emoji,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence



#: Discord's caps for one Components V2 message.
MAX_COMPONENTS = 40
MAX_TEXT = 4000
#: Text budget for card bodies; the rest is headroom for headings, field names and footers.
_CARD_BUDGET = 3600


def _fit(text: str, limit: int) -> str:
    """Trims ``text`` to ``limit`` characters on a line boundary where possible."""
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit - 2)
    return text[: cut if cut > 0 else limit - 2] + "\n…"


def _since(dt: datetime.datetime | None, style: Literal["R", "D", "F"] = "D") -> str:
    """Formats a timestamp column; ``commands.used`` is naive UTC, the history tables are aware."""
    if dt is None:
        return "*never*"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return discord.utils.format_dt(dt, style)


def _ranking(rows: Iterable[tuple[str, int]], *, unit: str = "uses", empty: str = "*Nothing recorded yet.*") -> str:
    """One medal-prefixed line per ``(label, count)``; ranks start at 1 so first place gets gold."""
    return "\n".join(
        f"{medal_emoji(rank)} {label} (`{count}` {unit})" for rank, (label, count) in enumerate(rows, start=1)
    ) or empty


def stats_card(
    title: str,
    description: str | None = None,
    *,
    fields: Sequence[tuple[str, str]] = (),
    footer: str | None = None,
    accent: Accent | discord.Colour | int = Accent.brand,
    thumbnail: str | None = None,
) -> NoticeView:
    """:func:`make_notice` with field values trimmed so the card always fits Discord's text cap."""
    description = _fit(description, _CARD_BUDGET // 3) if description else None
    remaining = _CARD_BUDGET - len(description or "")
    fitted = []
    for index, (name, value) in enumerate(fields):
        # Fair share of what is left, so a short field hands its headroom to the next one.
        value = _fit(value, remaining // (len(fields) - index))
        remaining -= len(value)
        fitted.append((name, value))
    return make_notice(title, description, accent=accent, thumbnail=thumbnail or None, fields=fitted, footer=footer)


def chart_card(
    title: str,
    description: str | None,
    filename: str,
    *,
    footer: str | None = None,
    accent: Accent | discord.Colour | int = Accent.brand,
) -> NoticeView:
    """A card around a rendered image; send it with ``file=`` carrying ``filename``."""
    colour = accent.colour if isinstance(accent, Accent) else accent
    container = discord.ui.Container(accent_colour=colour)
    container.add_item(discord.ui.TextDisplay(f"## {title}\n{description}" if description else f"## {title}"))
    container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{filename}")))
    if footer:
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"-# {footer}"))
    return NoticeView(container)


def failure_ranking(
    rows: Iterable[tuple[str, int, int]], *, min_uses: int = 5
) -> list[tuple[str, int, int, float]]:
    """``(command, total, failed, rate%)`` for commands with failures, worst rate first."""
    ranked = [(name, total, bad, bad / total * 100) for name, total, bad in rows if total >= min_uses and bad]
    return sorted(ranked, key=lambda r: (-r[3], -r[2]))


def activity_grid(rows: Iterable[Mapping[str, Any]]) -> list[list[int]]:
    """Folds ``(dow, hour, count)`` rows (dow 0 = Sunday) into ``grid[weekday][hour]``, Monday first."""
    grid = [[0] * 24 for _ in range(7)]
    for r in rows:
        grid[(r["dow"] - 1) % 7][r["hour"]] += r["count"]
    return grid


def hour_totals(rows: Iterable[Mapping[str, Any]]) -> list[int]:
    """Commands per hour of day (24 buckets) from ``(dow, hour, count)`` rows."""
    totals = [0] * 24
    for r in rows:
        totals[r["hour"]] += r["count"]
    return totals


def daily_series(rows: Iterable[Mapping[str, Any]], days: int) -> tuple[list[str], list[int]]:
    """Zero-filled ``days`` daily buckets ending today (naive UTC buckets from ``date_trunc``)."""
    by_day = {r["bucket"].date(): r["value"] for r in rows}
    today = datetime.datetime.now(datetime.UTC).date()
    day_list = [today - datetime.timedelta(days=i) for i in range(days - 1, -1, -1)]
    return [d.strftime("%d %b") for d in day_list], [by_day.get(d, 0) for d in day_list]


def _streak(current: int, *, fire: str = "🔥", snowflake: str = "❄") -> str:
    if current > 0:
        return f"{fire} {current}W"
    if current < 0:
        return f"{snowflake} {abs(current)}L"
    return "—"


def _winrate(won: int, played: int) -> float:
    return (won / played * 100) if played else 0.0


def _icon(game: Game) -> str:
    """``game.icon``, tolerating games added to the enum without a badge (``Game.icon`` raises)."""
    try:
        return game.icon
    except KeyError:
        return "🎮"


def game_stats_card(
    name: str,
    display_name: str,
    totals: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    *,
    cash: str = "💵",
    fire: str = "🔥",
    snowflake: str = "❄",
) -> NoticeView:
    """A member's game record: totals up top, one compact block per game (most played first).

    Every game shares one text block, so the card stays a handful of components however many
    games exist; the block is trimmed to the text cap instead.
    """
    rate = _winrate(totals["won"], totals["played"])
    summary = (
        f"**{totals['played']}** rounds • **{totals['won']}**W / **{totals['lost']}**L ({rate:.0f}% win-rate)\n"
        f"Net profit: {cash} **{fnumb(totals['profit'])}** • Biggest win: {cash} **{fnumb(totals['biggest_win'])}**\n"
        f"Streak: {_streak(totals['current_streak'])} • Best: {_streak(totals['best_streak'])}"
    )
    blocks = []
    for record in rows:
        game = Game(record["game"])
        tied = f" / `{record['tied']}`T" if record["tied"] else ""
        blocks.append(
            f"**{_icon(game)} {game.label}** — `{record['played']}` played • `{record['won']}`W / `{record['lost']}`L"
            f"{tied} • **{_winrate(record['won'], record['played']):.0f}%**\n"
            f"-# Net {cash} {fnumb(record['profit'])} • Streak "
            f"{_streak(record['current_streak'], fire=fire, snowflake=snowflake)} (best {record['best_streak']}W)"
        )
    return stats_card(
        f"{name}'s Game Stats",
        f"-# {display_name} has played **{totals['played']}** games in this server.",
        fields=[("Overall", summary), ("Per game", "\n".join(blocks) or "*No games yet.*")],
        footer="Want to play? See ?help Games",
    )

