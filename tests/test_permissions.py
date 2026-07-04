"""Tests for the command permission core (`app.core.permissions`).

These lock in the two behaviours that are easy to regress:

* every accepted input form (name, ``discord.Permissions`` flag, a built
  ``discord.Permissions``, a :class:`PermissionTemplate`, and iterables mixing them)
  normalises to the same canonical flag names, and
* permission *aliases* are canonicalised so a gate actually matches what
  :class:`discord.Permissions` reports for a member (the historical alias bug where
  ``manage_emojis`` silently never matched ``manage_expressions``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import discord
import pytest
from discord.ext import commands as dpy_commands

from app.core.command import command as make_command
from app.core.permissions import (
    CommandOverride,
    PermissionSpec,
    PermissionTemplate,
    command_permission_check,
)


def test_template_defaults_and_composition() -> None:
    assert set(PermissionTemplate.mod) == {"ban_members", "manage_messages"}
    assert set(PermissionTemplate.admin) == {"administrator"}
    assert set(PermissionTemplate.roles) == {"manage_roles"}

    combined = PermissionTemplate.roles | PermissionTemplate.channels
    assert set(combined) == {"manage_roles", "manage_channels"}
    # right-hand composition works too (``__ror__``)
    assert set(discord.Permissions.kick_members | PermissionTemplate.ban) == {"kick_members", "ban_members"}


def test_alias_is_canonicalised() -> None:
    # ``manage_emojis`` is an alias; a member's permissions report the canonical name,
    # so the stored gate must be canonical or it would silently never match.
    canonical = {flag for flag, on in discord.Permissions(manage_emojis=True) if on}
    assert set(PermissionTemplate.emojis) == canonical
    assert "manage_emojis" not in PermissionTemplate.emojis


@pytest.mark.parametrize(
    "value",
    [
        "manage_roles",
        discord.Permissions.manage_roles,
        discord.Permissions(manage_roles=True),
        PermissionTemplate.roles,
        [discord.Permissions.manage_roles],
        ["manage_roles"],
    ],
)
def test_update_accepts_every_input_form(value: object) -> None:
    spec = PermissionSpec.new()
    spec.update(value, "user")  # type: ignore[arg-type]
    assert "manage_roles" in spec.user


def test_update_expands_multi_flag_permissions_object() -> None:
    spec = PermissionSpec.new()
    spec.update(discord.Permissions(kick_members=True, ban_members=True), "user")
    assert spec.user == {"kick_members", "ban_members"}


def test_base_bot_set_is_a_fresh_copy_per_command() -> None:
    # Mutating one spec's bot set must not leak into the shared template or other specs.
    a = PermissionSpec.new()
    a.update([discord.Permissions.manage_roles], "bot")
    b = PermissionSpec.new()
    assert "manage_roles" not in b.bot
    assert "manage_roles" not in PermissionTemplate.bot


def test_base_bot_set_does_not_hard_require_external_emojis() -> None:
    # The bot degrades to unicode when it lacks external-emoji permission, so gating
    # every command on it would break that fallback.
    assert "external_emojis" not in PermissionSpec.new().bot


def test_invalid_permission_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid permission"):
        PermissionTemplate("definitely_not_a_permission")


def test_template_is_immutable() -> None:
    with pytest.raises(AttributeError):
        PermissionTemplate.mod.permissions = frozenset()  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Per-guild command permission overrides
# --------------------------------------------------------------------------- #


def test_override_required_permissions() -> None:
    default = {"ban_members"}
    # No explicit permissions -> keep the command's default requirement.
    assert CommandOverride("ban").required_user_permissions(default) == default
    # Explicit bitmask -> replace it.
    ov = CommandOverride("ban", permissions=discord.Permissions(manage_messages=True).value)
    assert ov.required_user_permissions(default) == {"manage_messages"}


def test_override_allows_roles() -> None:
    ov = CommandOverride("ban", allowed_roles=frozenset({10, 20}))
    member = SimpleNamespace(roles=[SimpleNamespace(id=5), SimpleNamespace(id=20)])
    assert ov.allows_roles(member) is True  # type: ignore[arg-type]
    stranger = SimpleNamespace(roles=[SimpleNamespace(id=5)])
    assert ov.allows_roles(stranger) is False  # type: ignore[arg-type]
    assert CommandOverride("ban").allows_roles(member) is False  # type: ignore[arg-type]  # empty allow-list


def _make_command(**perm_kwargs: object) -> Any:
    async def _cb(ctx: object) -> None: ...

    return make_command("ban", **perm_kwargs)(_cb)  # type: ignore[arg-type]


def _make_ctx(
    cmd: Any, *, user_perms: discord.Permissions, overrides: object = None, author_id: int = 5, owner_id: int = 999
) -> SimpleNamespace:
    class _DB:
        async def get_command_overrides(self, _guild_id: int) -> dict:
            return overrides or {}

    bot = SimpleNamespace(bypass_checks=False, owner_id=owner_id, owner_ids=None, db=_DB())
    return SimpleNamespace(
        command=cmd,
        bot=bot,
        guild=SimpleNamespace(id=1),
        author=SimpleNamespace(id=author_id),
        permissions=user_perms,
        bot_permissions=discord.Permissions.all(),
    )


async def test_check_passes_and_fails_against_default_gate() -> None:
    cmd = _make_command(user_permissions=PermissionTemplate.ban)

    ok = _make_ctx(cmd, user_perms=discord.Permissions(ban_members=True))
    assert await command_permission_check(ok) is True  # type: ignore[arg-type]

    lacking = _make_ctx(cmd, user_perms=discord.Permissions(send_messages=True))
    with pytest.raises(dpy_commands.MissingPermissions):
        await command_permission_check(lacking)  # type: ignore[arg-type]


async def test_override_can_loosen_the_requirement() -> None:
    # Command defaults to needing ban_members; guild override drops it to send_messages.
    cmd = _make_command(user_permissions=PermissionTemplate.ban)
    overrides = {"ban": CommandOverride("ban", permissions=discord.Permissions(send_messages=True).value)}
    ctx = _make_ctx(cmd, user_perms=discord.Permissions(send_messages=True), overrides=overrides)
    assert await command_permission_check(ctx) is True  # type: ignore[arg-type]


async def test_override_can_tighten_the_requirement() -> None:
    cmd = _make_command(user_permissions=PermissionTemplate.messages)  # default: manage_messages
    overrides = {"ban": CommandOverride("ban", permissions=discord.Permissions(administrator=True).value)}
    # Member has manage_messages (enough for the default) but not administrator (the override).
    ctx = _make_ctx(cmd, user_perms=discord.Permissions(manage_messages=True), overrides=overrides)
    with pytest.raises(dpy_commands.MissingPermissions):
        await command_permission_check(ctx)  # type: ignore[arg-type]


async def test_owner_bypasses_everything() -> None:
    cmd = _make_command(user_permissions=PermissionTemplate.ban)
    ctx = _make_ctx(cmd, user_perms=discord.Permissions.none(), author_id=999, owner_id=999)
    assert await command_permission_check(ctx) is True  # type: ignore[arg-type]


async def test_native_permissions_only_gate_top_level_hybrids() -> None:
    """``assign_native_permissions`` mirrors user gates onto standalone slash commands only.

    Groups (can't express per-subcommand gates natively), subcommands (inherit the parent),
    prefix-only commands (no slash), and ungated commands are all left untouched.
    """
    from discord.ext import commands as dpy_commands

    from app.core import Cog, command, group
    from app.core.bot import assign_native_permissions

    class _Fixture(Cog):
        @command(hybrid=True, user_permissions=PermissionTemplate.ban)
        async def zap(self, ctx: object) -> None: ...

        @command(user_permissions=PermissionTemplate.mod)  # prefix-only: no app_command
        async def prefixonly(self, ctx: object) -> None: ...

        @command(hybrid=True)  # hybrid but ungated
        async def wide_open(self, ctx: object) -> None: ...

        @group(hybrid=True, user_permissions=PermissionTemplate.manager)
        async def grp(self, ctx: object) -> None: ...

        @grp.command(user_permissions=PermissionTemplate.admin)
        async def sub(self, ctx: object) -> None: ...

    bot = dpy_commands.Bot(command_prefix="!", intents=discord.Intents.none())
    await bot.add_cog(_Fixture(bot=None))  # type: ignore[arg-type]

    gated = assign_native_permissions(bot.walk_commands())
    assert gated == 1  # only ``zap``

    by_name = {c.qualified_name: c for c in bot.walk_commands()}
    zap_perms = by_name["zap"].app_command.default_permissions  # type: ignore[union-attr]
    assert zap_perms == discord.Permissions(ban_members=True)

    # Ungated hybrid keeps Discord's default (visible to everyone).
    assert by_name["wide_open"].app_command.default_permissions is None  # type: ignore[union-attr]
    # Group is left open — enforced at runtime, not natively.
    assert by_name["grp"].app_command.default_permissions is None  # type: ignore[union-attr]
    # Prefix-only command has no slash command to gate.
    assert getattr(by_name["prefixonly"], "app_command", None) is None
