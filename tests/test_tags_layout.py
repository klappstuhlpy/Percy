"""Tag CV2 list paginator and content-send safety, built from fake data (no bot)."""

from __future__ import annotations

import discord

from app.cogs.tags import TAG_MENTIONS, TagListPaginator, TagPageEntry, content_payload


async def test_list_paginator_builds_within_limits() -> None:
    rows = [{"name": f"tag_{i}" + "*" * 90, "id": i} for i in range(40)]
    view = TagListPaginator(entries=rows, per_page=15)  # type: ignore[arg-type]
    view.extras.update(title="Tag List", description="someone's tags", sort="name")
    await view.build()
    children = list(view.walk_children())
    assert len(children) <= 40
    assert sum(len(c.content) for c in children if isinstance(c, discord.ui.TextDisplay)) <= 4000
    assert view.total_pages == 3


def test_entry_escapes_markdown() -> None:
    assert str(TagPageEntry(record={"name": "**x**", "id": 1})) == "\\*\\*x\\*\\* [`1`]"


def test_content_never_pings() -> None:
    kwargs = content_payload("@everyone <@&1> <@2>")
    assert kwargs["allowed_mentions"] is TAG_MENTIONS
    assert not TAG_MENTIONS.everyone and not TAG_MENTIONS.roles and not TAG_MENTIONS.users
    long = content_payload("x" * 2500)
    assert "content" not in long and isinstance(long["file"], discord.File)
