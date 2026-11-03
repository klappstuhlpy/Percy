"""Regression tests for bugs fixed in ``app/core`` and ``app/utils``."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import discord

from app.core.bot import Bot
from app.core.context import Context
from app.core.converter import ColorTransformer
from app.core.flags import Flags, flag
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


async def test_on_error_survives_a_long_argument_repr(monkeypatch) -> None:
    sent = AsyncMock()
    monkeypatch.setattr("app.core.bot.send_webhook", sent)
    me = SimpleNamespace(stats_webhook=object())
    try:
        raise KeyError("boom")
    except KeyError:
        # TextSource.add_line raised RuntimeError on the over-long repr, killing the report.
        await Bot.on_error(me, "on_message", "x" * 5000)  # type: ignore[arg-type]
    # A first argument that is no context/message/member used to drop the report silently.
    sent.assert_awaited_once()


async def test_on_error_reports_events_without_arguments(monkeypatch) -> None:
    sent = AsyncMock()
    monkeypatch.setattr("app.core.bot.send_webhook", sent)
    me = SimpleNamespace(stats_webhook=object())
    try:
        raise KeyError("boom")
    except KeyError:
        # args[0] raised IndexError inside the handler for argument-less events (on_ready).
        await Bot.on_error(me, "on_ready")  # type: ignore[arg-type]
    sent.assert_awaited_once()


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


def test_embed_builder_keeps_the_first_field_of_a_generator() -> None:
    from app.core.embeds import EmbedBuilder

    # The dict/tuple sniff consumed the first item of a one-shot iterable.
    embed = EmbedBuilder(fields=((str(i), "v", False) for i in range(3)))
    assert [f.name for f in embed.fields] == ["0", "1", "2"]
    embed.add_fields([{"name": "d", "value": 1}])
    assert embed.fields[-1].value == "1" and embed.fields[-1].inline is True


def test_paginator_accepts_embed_subclasses() -> None:
    from app.core.embeds import EmbedBuilder

    paginator = LinePaginator(entries=["a"], per_page=10)
    page = EmbedBuilder()
    # An exact-class lookup rejected EmbedBuilder with "Unsupported type".
    assert BasePaginator.resolve_msg_kwargs(paginator, page)["embed"] is page


def test_presence_summary_skips_unmapped_status() -> None:
    from app.services.stats.presence_stats import summarize_presence

    now = datetime.datetime(2026, 1, 1, 12)
    hour = datetime.timedelta(hours=1)
    # status_before is NULL for invisible; it raised KeyError(None).
    breakdown = summarize_presence([(now, "Online"), (now - hour, None), (now - 2 * hour, "Idle")])
    assert breakdown.durations["Idle"] == 3600
    assert sum(breakdown.durations.values()) == 3600


def test_flags_keep_their_own_docstring() -> None:
    from app.core.flags import Flags

    class DocFlags(Flags):
        """Mine."""

    assert DocFlags.__doc__ == "Mine."


def test_command_suggestion_only_fires_on_a_near_identical_name() -> None:
    from app.core.bot import closest_command_name

    ask, ban, bal, leaderboard = object(), object(), object(), object()
    names = {"ask": ask, "ban": ban, "balance": bal, "bal": bal, "leaderboard": leaderboard}
    assert closest_command_name("aks", names) == "ask"            # transposition
    assert closest_command_name("BALANCE", {"balance": bal}) == "balance"
    assert closest_command_name("balanc", names) == "balance"     # one deletion
    assert closest_command_name("leaderbaord", names) == "leaderboard"
    assert closest_command_name("ledarboadr", names) == "leaderboard"  # two slips, long word
    assert closest_command_name("baldheu", names) is None         # not close enough
    assert closest_command_name("bas", names) is None             # ties ban (ban) and bal (balance)
    assert closest_command_name("hi", names) is None              # too short to judge
    assert closest_command_name("what is the weather", names) is None


def test_collection_flag_annotations_fail_at_definition() -> None:
    import pytest

    # tuple/dict flags ran the whole value through e.g. tuple("ab"); reject them up front.
    with pytest.raises(TypeError, match="not supported"):

        class _Bad(Flags):  # pyright: ignore[reportUnusedClass]
            pair: tuple[str, int]


class _CopyFlags(Flags):
    amount: int = flag(default=1)


class _CopyCog:
    seen: ClassVar[dict] = {}

    async def cb(self, ctx, *, text: str, flags: _CopyFlags) -> None:
        _CopyCog.seen = {"text": text, "ctx": ctx, "flags": flags}


async def test_flag_wrapper_survives_a_copy_after_add() -> None:
    from app.core.command import Command

    # The original command (cog-less) was transformed, then a copy bound to a cog was
    # transformed again: the copy's wrapper wrapped the original's, whose stale ``cog``
    # made it re-map ``ctx`` into ``text``.
    cmd = Command(_CopyCog.cb, name="t")
    cmd.transform_flag_parameters()
    copy = cmd.copy()
    cog = _CopyCog()
    copy.cog = cog
    copy.transform_flag_parameters()  # what Bot.add_command does for a copied command
    await copy.callback(cog, "CTX", "hello", flags="F")
    assert _CopyCog.seen == {"text": "hello", "ctx": "CTX", "flags": "F"}
