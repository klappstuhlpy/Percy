"""Tests for the natural-language command router (:mod:`app.services.ai.router`).

Pure logic: schema parsing, prompt assembly, and the route gating (confidence threshold,
unknown/hallucinated command rejection) against a fake AIService — no bot or model needed.
"""

from __future__ import annotations

from typing import Any

from app.services.ai import RouteCommand, RouteDecision, build_route_system_prompt
from app.services.ai.router import CommandRouter

CATALOGUE = [
    RouteCommand(name='remind', description='Set a reminder'),
    RouteCommand(name='tag get', description='Show a tag'),
    RouteCommand(name='ban', description='Ban a member'),
]


class FakeAI:
    """Stand-in for AIService: returns a preset parse result, recording the call."""

    def __init__(self, result: Any) -> None:
        self._result = result
        self.last_system: str | None = None
        self.calls = 0

    async def parse(self, text: str, *, schema: Any, system: str, tier: Any) -> Any:
        self.calls += 1
        self.last_system = system
        return self._result


def router(result: Any, *, min_confidence: float = 0.6) -> tuple[CommandRouter, FakeAI]:
    ai = FakeAI(result)
    return CommandRouter(ai, min_confidence=min_confidence), ai  # type: ignore[arg-type]


# -- RouteDecision.from_payload ---------------------------------------------------


def test_from_payload_parses_full_decision() -> None:
    d = RouteDecision.from_payload({'command': 'remind', 'args': 'me in 5m to eat', 'confidence': 0.9})
    assert d.command == 'remind'
    assert d.args == 'me in 5m to eat'
    assert d.confidence == 0.9


def test_from_payload_null_command_tokens_become_none() -> None:
    for token in ('none', 'null', '', '  ', 'unknown'):
        assert RouteDecision.from_payload({'command': token, 'confidence': 0.0}).command is None
    assert RouteDecision.from_payload({'command': None}).command is None


def test_from_payload_clamps_confidence_and_defaults() -> None:
    assert RouteDecision.from_payload({'command': 'x', 'confidence': 5}).confidence == 1.0
    assert RouteDecision.from_payload({'command': 'x', 'confidence': -2}).confidence == 0.0
    assert RouteDecision.from_payload({'command': 'x'}).confidence == 0.0  # missing -> 0
    assert RouteDecision.from_payload({'command': 'x', 'confidence': 'bad'}).confidence == 0.0


def test_from_payload_non_string_args_becomes_empty() -> None:
    assert RouteDecision.from_payload({'command': 'x', 'args': 123, 'confidence': 1}).args == ''


# -- prompt ----------------------------------------------------------------------


def test_prompt_lists_commands_and_descriptions() -> None:
    prompt = build_route_system_prompt(CATALOGUE)
    assert '- remind: Set a reminder' in prompt
    assert '- tag get: Show a tag' in prompt
    assert 'JSON' in prompt


# -- CommandRouter.route ---------------------------------------------------------


async def test_route_returns_confident_in_catalogue_decision() -> None:
    r, ai = router(RouteDecision('remind', 'me in 5m', 0.9))
    decision = await r.route('remind me in 5 minutes', CATALOGUE)
    assert decision is not None
    assert decision.command == 'remind'
    assert ai.calls == 1


async def test_route_rejects_low_confidence() -> None:
    r, _ = router(RouteDecision('remind', '', 0.3))
    assert await r.route('something vague', CATALOGUE) is None


async def test_route_rejects_unknown_command() -> None:
    # Model hallucinated a command not in the catalogue.
    r, _ = router(RouteDecision('selfdestruct', '', 0.99))
    assert await r.route('blow up', CATALOGUE) is None


async def test_route_rejects_null_command() -> None:
    r, _ = router(RouteDecision(None, '', 0.0))
    assert await r.route('hello there', CATALOGUE) is None


async def test_route_returns_none_when_model_unavailable() -> None:
    r, _ = router(None)  # AIService.parse degraded to None
    assert await r.route('remind me', CATALOGUE) is None


async def test_route_short_circuits_on_empty_text_or_catalogue() -> None:
    r, ai = router(RouteDecision('remind', '', 0.9))
    assert await r.route('   ', CATALOGUE) is None
    assert await r.route('remind me', []) is None
    assert ai.calls == 0  # never reached the model
