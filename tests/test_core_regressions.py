"""Regression tests for bugs fixed in ``app/core`` and ``app/utils``."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from app.core.bot import Bot
from app.core.context import Context
from app.core.converter import ColorTransformer
from app.core.pagination import BasePaginator, LinePaginator
from app.core.timer import Timer
from app.utils.helpers import Colour
from app.utils.text import slugify


def _timer(bot: object, args: list | tuple) -> Timer:
    now = datetime.datetime(2026, 1, 1)
    return Timer(
        bot=bot,
        record={
            "id": 7, "event": "reminder", "created": now, "expires": now, "timezone": "UTC",
            "metadata": {"args": args, "kwargs": {"connection": 1}},
        },
    )


async def test_rerun_persisted_timer_with_list_args() -> None:
    # jsonb hands persisted args back as a list; list + tuple raised TypeError on snooze.
    bot = SimpleNamespace(timers=SimpleNamespace(create=AsyncMock()))
    when = datetime.timedelta(minutes=5)
    await _timer(bot, [1, 2, "msg"]).rerun(when, timezone="UTC")
    bot.timers.create.assert_awaited_once_with(when, "reminder", 1, 2, "msg", connection=1, timezone="UTC")


def test_timer_eq_other_type() -> None:
    timer = _timer(None, [])
    assert timer != None
    assert timer == _timer(None, [9])


async def test_safe_send_forwards_kwargs() -> None:
    ctx = Context.__new__(Context)
    ctx.send = AsyncMock()  # type: ignore[method-assign]
    await ctx.safe_send("hi", ephemeral=True)
    ctx.send.assert_awaited_once_with("hi", ephemeral=True)


async def test_send_edit_edits_last_message() -> None:
    # send(edit=True) passed the content as maybe_edit's *message* argument.
    ctx = Context.__new__(Context)
    message = MagicMock()
    message.edit = AsyncMock()
    ctx._message = message
    assert await ctx.send("new", edit=True) is message
    message.edit.assert_awaited_once_with(content="new")


async def test_line_paginator_numbers_duplicates_by_position() -> None:
    paginator = LinePaginator(entries=["a", "a", "b"], per_page=10)
    paginator.embed = discord.Embed()
    paginator.location = "description"
    paginator.numerate = True
    embed = await paginator.format_page(paginator.pages[0])
    assert embed.description == "1. a\n2. a\n3. b"


async def test_send_after_response_returns_the_followup() -> None:
    interaction = MagicMock()
    interaction.response.is_done.return_value = True
    sent = object()
    interaction.followup.send = AsyncMock(return_value=sent)
    interaction.original_response = AsyncMock()
    assert await BasePaginator._send(interaction, True, content="x") is sent
    interaction.original_response.assert_not_awaited()


def test_colour_transformer_parses_hex_and_names() -> None:
    convert = ColorTransformer()._convert
    assert convert(None, "#fff") == Colour(0xFFFFFF)
    assert convert(None, "0x00ff00") == Colour(0x00FF00)
    # Named colours used to always come back blurple ("#rrggbb" fed to bytes.fromhex).
    named = convert(None, "red")
    assert named is not None and named != Colour.blurple()
    # Eight hex digits used to raise TypeError from Colour.from_rgb; now it falls to name lookup.
    convert(None, "#12345678")


def test_find_member_from_user_is_not_cached() -> None:
    # A cached None outlived the user joining a mutual guild.
    user = discord.Object(id=5)
    me = SimpleNamespace(guilds=[])
    assert Bot.find_member_from_user(me, user) is None  # type: ignore[arg-type]
    member = object()
    me.guilds.append(SimpleNamespace(get_member=lambda _id: member))
    assert Bot.find_member_from_user(me, user) is member  # type: ignore[arg-type]


async def test_on_error_survives_a_long_argument_repr() -> None:
    me = SimpleNamespace()
    try:
        raise KeyError("boom")
    except KeyError:
        # TextSource.add_line raised RuntimeError on the over-long repr, killing the report.
        await Bot.on_error(me, "on_message", "x" * 5000)  # type: ignore[arg-type]


def test_slugify() -> None:
    assert slugify("Avengers: Endgame") == "avengers-endgame"
    assert slugify("Pokémon") == "pokemon"
    assert slugify("Marvel's Daredevil") == "marvels-daredevil"
    assert slugify("  --Hello__World--  ") == "hello-world"
    assert slugify("東京") == ""


async def test_empty_paginators_send_notice_instead_of_crashing() -> None:
    from app.core.pagination import EmbedPaginator, FilePaginator, TextPaginator

    for cls in (EmbedPaginator, TextPaginator, FilePaginator):
        ctx = MagicMock(spec=Context)
        ctx.send = AsyncMock()
        await cls.start(ctx, entries=[])
        ctx.send.assert_awaited_once()


def test_check_failure_message() -> None:
    from discord import app_commands

    from app.core.tree import check_failure_message

    assert "cooldown" in check_failure_message(app_commands.CommandOnCooldown(app_commands.Cooldown(1, 1), 2.0))
    assert check_failure_message(app_commands.MissingPermissions(["ban_members"])) is not None
    assert check_failure_message(app_commands.BotMissingPermissions(["ban_members"])) is not None
    assert check_failure_message(app_commands.CheckFailure()) is None  # blacklist path stays silent
    assert check_failure_message(RuntimeError()) is None
