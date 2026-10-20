"""Tests for the shared moderation hierarchy rule and the ``MemberID`` converter."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord.ext import commands

from app.core.converter import MemberID
from app.utils.checks import hierarchy_error

OWNER_ID = 1
BOT_ID = 2


def _member(member_id: int, top_role: int) -> SimpleNamespace:
    return SimpleNamespace(id=member_id, top_role=top_role)


def _me(top_role: int = 50) -> SimpleNamespace:
    return SimpleNamespace(id=BOT_ID, top_role=top_role, guild=SimpleNamespace(owner_id=OWNER_ID))


def _error(actor: object, target: object, me: object = None, **kwargs: bool) -> str | None:
    return hierarchy_error(actor, target, me or _me(), action="ban", **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("actor", "target", "allowed"),
    [
        (_member(10, 30), _member(11, 20), True),  # actor above target, Percy above target
        (_member(10, 30), _member(10, 30), False),  # self
        (_member(10, 30), _member(OWNER_ID, 5), False),  # server owner
        (_member(10, 30), _member(BOT_ID, 50), False),  # Percy itself
        (_member(10, 30), _member(11, 30), False),  # equal top role
        (_member(10, 30), _member(11, 40), False),  # target above actor
        (_member(OWNER_ID, 5), _member(11, 40), True),  # owner bypasses the actor half
        (_member(10, 60), _member(11, 50), False),  # target equal to Percy
        (_member(10, 60), _member(11, 55), False),  # target above Percy
        (_member(10, 30), SimpleNamespace(id=99), True),  # a bare id (ban by id): identity checks only
        (_member(10, 30), SimpleNamespace(id=OWNER_ID), False),  # ...which still refuse the owner
    ],
)
def test_hierarchy_matrix(actor, target, allowed: bool) -> None:
    assert (_error(actor, target) is None) is allowed


def test_errors_name_who_is_blocked() -> None:
    assert _error(_member(10, 30), _member(11, 40)) == (
        "You cannot ban a member with a role equal to or higher than yours."
    )
    assert _error(_member(10, 90), _member(11, 60)) == "I cannot ban a member with a role equal to or higher than mine."
    assert _error(_member(10, 30), _member(BOT_ID, 1)) == "I cannot ban myself."


def test_check_bot_off_only_drops_percys_half() -> None:
    above_percy = _member(11, 60)
    assert _error(_member(10, 90), above_percy, check_bot=False) is None
    assert _error(_member(10, 30), above_percy, check_bot=False) is not None


def test_automatic_action_only_checks_percy() -> None:
    assert _error(None, _member(11, 20)) is None
    assert _error(None, _member(11, 50)) is not None
    assert _error(None, _member(OWNER_ID, 1)) is not None


async def test_member_id_transform_passes_the_resolved_user_through() -> None:
    user = object()
    assert await MemberID().transform(MagicMock(), user) is user


async def test_member_id_bans_by_id_without_touching_hierarchy(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = MagicMock()
    ctx.bot.get_or_fetch_member = AsyncMock(return_value=None)
    # MemberConverter fails for a raw id of someone not in the server.
    monkeypatch.setattr(commands.MemberConverter, "convert", AsyncMock(side_effect=commands.BadArgument("nope")))

    target = await MemberID().convert(ctx, "123456789012345678")

    assert target.id == 123456789012345678
    assert not hasattr(target, "top_role")


def test_mute_hierarchy_ignores_percys_rank_but_not_the_moderators() -> None:
    from app.cogs.moderation.infractions import check_member_hierarchy

    # Percy (top role 50) sits below the target (60): a mute only needs Percy above the mute role.
    ctx = SimpleNamespace(author=_member(10, 90), guild=SimpleNamespace(me=_me(50)))
    assert check_member_hierarchy(ctx, _member(11, 60), action="mute", check_bot=False) is None  # type: ignore[arg-type]

    ctx.author = _member(10, 60)
    assert check_member_hierarchy(ctx, _member(11, 60), action="mute", check_bot=False) is not None  # type: ignore[arg-type]
    assert check_member_hierarchy(ctx, ctx.author, action="unmute", check_bot=False) is not None  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["_mute", "_unmute", "tempmute"])
def test_mute_commands_skip_percys_rank_check(name: str) -> None:
    import inspect

    from app.cogs.moderation.cog import Moderation

    source = inspect.getsource(getattr(Moderation, name).callback)
    assert "check_member_hierarchy(" in source
    assert "check_bot=False" in source
