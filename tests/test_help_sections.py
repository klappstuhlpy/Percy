"""Tests for the help menu's per-category sections and command search (:mod:`app.core.help`)."""

from __future__ import annotations

import importlib
import inspect
import sys
from typing import ClassVar

import pytest
from discord.ext import commands

from app.cogs import EXTENSIONS
from app.cogs.economy.cog import Economy
from app.cogs.games.cog import Games
from app.cogs.moderation.cog import Moderation
from app.core.help import GENERAL_SECTION, command_sections, search_commands


async def _noop(_ctx: object) -> None: ...


def _cmd(name: str, description: str = "", aliases: tuple[str, ...] = ()) -> commands.Command:
    return commands.Command(_noop, name=name, description=description, aliases=list(aliases))


def _group(name: str, *subs: str) -> list[commands.Command]:
    group = commands.Group(_noop, name=name)
    return [group, *(group.command(name=sub)(_noop) for sub in subs)]


class _Cog:
    help_sections: ClassVar[dict[str, tuple[str, ...]]] = {"Bans": ("ban", "unban"), "Voice": ("voicekick",)}


def test_declared_then_groups_then_general_first() -> None:
    entries = [_cmd("purge"), _cmd("ban"), _cmd("unban"), _cmd("voicekick"), *_group("lockdown", "start", "end")]
    sections = command_sections(_Cog(), entries)  # type: ignore[arg-type]

    assert list(sections) == [GENERAL_SECTION, "Bans", "Voice", "Lockdown"]
    assert [c.name for c in sections["Bans"]] == ["ban", "unban"]
    assert [c.qualified_name for c in sections["Lockdown"]] == ["lockdown", "lockdown start", "lockdown end"]


def test_lone_group_folds_into_general_and_no_empty_sections() -> None:
    sections = command_sections(_Cog(), [_cmd("purge"), commands.Group(_noop, name="solo")])  # type: ignore[arg-type]
    assert list(sections) == [GENERAL_SECTION]


def test_search_ranks_prefix_then_alias_then_description() -> None:
    entries = [
        _cmd("unban", "Lifts a ban."),
        _cmd("ban", "Bans a member."),
        _cmd("kick", "Removes a member.", aliases=("boot",)),
        _cmd("purge", "Deletes messages, ban-proof."),
    ]
    assert [c.name for c in search_commands(entries, "BAN")] == ["ban", "unban", "purge"]
    assert [c.name for c in search_commands(entries, "boo")] == ["kick"]
    assert search_commands(entries, "   ") == []


def _sectioned_cogs() -> list[type]:
    for ext in EXTENSIONS:
        importlib.import_module(ext)
    found: dict[str, type] = {}
    modules = [m for name, m in list(sys.modules.items()) if name.startswith("app.cogs.")]
    for module in modules:
        for obj in vars(module).values():
            if inspect.isclass(obj) and issubclass(obj, commands.Cog) and "help_sections" in vars(obj):
                found[obj.__qualname__] = obj
    return list(found.values())


@pytest.mark.parametrize("cog", _sectioned_cogs(), ids=lambda c: c.__qualname__)
def test_declared_sections_name_real_commands(cog: type) -> None:
    names = {c.qualified_name for c in cog.__cog_commands__}
    declared = [name for names_ in cog.help_sections.values() for name in names_]
    assert sorted(set(declared) - names) == []
    assert len(declared) == len(set(declared)), "a command is listed in two sections"


def test_sectioned_cogs_include_the_big_ones() -> None:
    assert {Moderation, Economy, Games} <= set(_sectioned_cogs())


def test_qualified_claims_and_single_group_cog() -> None:
    class Single:
        help_sections: ClassVar[dict[str, tuple[str, ...]]] = {"Rules": ("config rule",)}

    config = commands.Group(_noop, name="config")
    rule = config.group(name="rule")(_noop)
    entries = [config, rule, rule.command(name="allow")(_noop), *(config.command(name=n)(_noop) for n in ("explain", "ignore"))]
    sections = command_sections(Single(), entries)  # type: ignore[arg-type]

    assert list(sections) == [GENERAL_SECTION, "Rules"]
    assert [c.qualified_name for c in sections["Rules"]] == ["config rule", "config rule allow"]
    assert [c.qualified_name for c in sections[GENERAL_SECTION]] == ["config", "config explain", "config ignore"]


def test_section_title() -> None:
    from app.core.help import section_title

    assert section_title("economy-config") == "Economy Config"
    assert section_title("dj") == "DJ"


def test_section_index_splits_every_cog_with_positions() -> None:
    from app.core.help import section_index

    cog = _Cog()
    sectioned = [_cmd("purge"), _cmd("ban"), _cmd("voicekick")]
    for c in sectioned:
        c.cog = cog  # type: ignore[assignment]
    cogless = _cmd("ping")  # no cog: one General section, even though no page size forces a split

    index = section_index([*sectioned, cogless])  # type: ignore[arg-type]
    assert index == {
        "purge": (GENERAL_SECTION, 0),
        "ban": ("Bans", 1),
        "voicekick": ("Voice", 2),
        "ping": (GENERAL_SECTION, 0),
    }
