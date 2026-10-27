"""The stats cog's Components V2 cards build from plain data and stay inside Discord's limits."""

from __future__ import annotations

import datetime

import discord

from app.cogs.games.models import Game
from app.cogs.stats import MAX_COMPONENTS, MAX_TEXT, _fit, _ranking, _since, chart_card, game_stats_card, stats_card


def _within_limits(view: discord.ui.LayoutView) -> None:
    items = list(view.walk_children())
    text = sum(len(i.content) for i in items if isinstance(i, discord.ui.TextDisplay))
    assert len(items) <= MAX_COMPONENTS
    assert text <= MAX_TEXT


async def test_ranking_gives_first_place_gold() -> None:
    lines = _ranking([("ping", 9), ("help", 4)]).splitlines()
    assert lines[0].startswith("\N{FIRST PLACE MEDAL}")
    assert lines[1].startswith("\N{SECOND PLACE MEDAL}")
    assert _ranking([]) == "*Nothing recorded yet.*"


async def test_fit_trims_to_limit() -> None:
    text = "\n".join(f"line {i}" for i in range(1000))
    assert len(_fit(text, 500)) <= 500
    assert len(_fit("x" * 2000, 100)) <= 100
    assert _fit("short", 100) == "short"


async def test_since_treats_naive_as_utc() -> None:
    naive = datetime.datetime(2026, 1, 1, 12, 0)
    assert _since(naive) == _since(naive.replace(tzinfo=datetime.UTC))
    assert _since(None) == "*never*"


async def test_stats_card_with_huge_fields_fits() -> None:
    huge = "\n".join(f"\N{SPORTS MEDAL} <@{i}> (`{i}` uses)" for i in range(500))
    view = stats_card(
        "Server Command Stats",
        "Total of `123` commands used.",
        fields=[("Top Commands", "short"), ("A", huge), ("B", huge), ("C", huge)],
        footer="Tracking command usage since now",
        thumbnail="https://cdn.discordapp.com/icons/1/a.png",
    )
    _within_limits(view)
    assert any(isinstance(i, discord.ui.Thumbnail) for i in view.walk_children())


async def test_stats_card_drops_empty_thumbnail() -> None:
    # get_asset_url returns "" for an icon-less guild; a Thumbnail("") would be rejected.
    view = stats_card("Uptime", "**1 day**", thumbnail="")
    assert not any(isinstance(i, discord.ui.Thumbnail) for i in view.walk_children())


async def test_chart_card_references_attachment() -> None:
    view = chart_card("Socket Events", "`10` events", "bar_chart.png", footer="since boot")
    galleries = [i for i in view.walk_children() if isinstance(i, discord.ui.MediaGallery)]
    assert galleries and galleries[0].items[0].media.url == "attachment://bar_chart.png"
    _within_limits(view)


async def test_game_stats_card_every_game_fits() -> None:
    row = {
        "played": 12345, "won": 6000, "lost": 6000, "tied": 345, "profit": 123456789,
        "current_streak": -7, "best_streak": 42,
    }
    rows = [{"game": game.value, **row} for game in Game]
    totals = {**row, "biggest_win": 999999, "current_streak": 3}
    view = game_stats_card(
        "someone", "Someone", totals, rows,
        cash="<:cash:123456789012345678>", fire="<a:fire:123456789012345678>", snowflake="<a:snow:123456789012345678>",
    )
    _within_limits(view)
