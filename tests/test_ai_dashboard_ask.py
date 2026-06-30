"""Tests for the dashboard AI-ask helpers in :mod:`app.internal_api.routers.guild`.

The endpoint itself needs a live bot/AI engine, but the two pieces that decide *what the
dashboard sees* are pure given a command registry: the catalogue fed to the model and the
suggestion extraction that turns backtick-named commands in an answer into real, invokable
chips (dropping anything the model invented).
"""

from __future__ import annotations

from app.internal_api.routers.guild import _command_catalogue, _extract_suggestions


class _FakeCommand:
    def __init__(self, name: str, *, description: str = "", hidden: bool = False, enabled: bool = True) -> None:
        self.qualified_name = name
        self.description = description
        self.short_doc = ""
        self.hidden = hidden
        self.enabled = enabled


class _FakeBot:
    """Minimal stand-in exposing the two attributes the helpers touch."""

    def __init__(self, commands: list[_FakeCommand]) -> None:
        self.commands = commands
        self._by_name = {c.qualified_name: c for c in commands}

    def get_command(self, name: str) -> _FakeCommand | None:
        return self._by_name.get(name)


def _bot() -> _FakeBot:
    return _FakeBot(
        [
            _FakeCommand("play", description="Play a track"),
            _FakeCommand("level", description="Show your rank"),
            _FakeCommand("secret", description="hidden", hidden=True),
            _FakeCommand("off", description="disabled", enabled=False),
        ]
    )


def test_catalogue_excludes_hidden_and_disabled_and_sorts() -> None:
    catalogue = _command_catalogue(_bot())
    names = [name for name, _desc in catalogue]
    assert names == ["level", "play"]  # sorted, no hidden/disabled
    assert ("play", "Play a track") in catalogue


def test_extract_suggestions_resolves_real_commands() -> None:
    answer = "Run `?play` to start music, then check your rank with `?level`."
    suggestions = _extract_suggestions(_bot(), answer, "?")
    assert suggestions == [
        {"label": "?play", "command": "play"},
        {"label": "?level", "command": "level"},
    ]


def test_extract_suggestions_drops_invented_and_hidden_commands() -> None:
    answer = "Try `?teleport` (not real), `?secret` (hidden) or `?off` (disabled)."
    assert _extract_suggestions(_bot(), answer, "?") == []


def test_extract_suggestions_dedupes_and_strips_arguments() -> None:
    answer = "Use `?play despacito` or just `?play`."
    suggestions = _extract_suggestions(_bot(), answer, "?")
    assert suggestions == [{"label": "?play", "command": "play"}]


def test_extract_suggestions_handles_custom_prefix() -> None:
    answer = "Type `b.level` to see your rank."
    assert _extract_suggestions(_bot(), answer, "b.") == [{"label": "b.level", "command": "level"}]
