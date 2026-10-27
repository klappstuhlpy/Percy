from __future__ import annotations

from app.cogs.dev import Dev, _iter_caches, _parse_value
from app.database.base import Database


def test_parse_value() -> None:
    assert _parse_value("null") is None
    assert _parse_value("None") is None
    assert _parse_value("123") == 123
    assert _parse_value("[1, 2]") == [1, 2]
    assert _parse_value("True") is True
    assert _parse_value("hello world") == "hello world"


def test_iter_caches_finds_class_getters_once() -> None:
    names = [name for name, _ in _iter_caches()]
    assert f"{Database.__module__}.Database.get_guild_config" in names
    assert len(names) == len(set(names))


def test_dev_command_tree() -> None:
    dev = next(c for c in Dev.__cog_commands__ if c.qualified_name == "dev")
    subcommands = {c.qualified_name for c in dev.walk_commands()}  # type: ignore[attr-defined]
    for expected in ("dev cache clear", "dev flags disable", "dev config", "dev timers fire",
                     "dev ai ask", "dev bothealth", "dev bypass", "dev music reconnect", "dev tree sync", "dev sql explain", "dev log"):
        assert expected in subcommands
