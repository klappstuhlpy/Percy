"""Tests for the AI moderation verdict service (:mod:`app.services.ai.moderation`).

Pure logic: verdict schema parsing and the assessor's "worth surfacing" gating (flagged,
not the ``none`` category, confidence threshold) against a fake AIService. No bot/model.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.ai import ModerationAssessor, ModerationVerdict
from app.services.ai.moderation import SchemaError


class FakeAI:
    """Stand-in for AIService: returns a preset parse result, recording the call count."""

    def __init__(self, result: Any) -> None:
        self._result = result
        self.calls = 0

    async def parse(self, text: str, *, schema: Any, system: str, tier: Any) -> Any:
        self.calls += 1
        return self._result


def assessor(result: Any, *, min_confidence: float = 0.7) -> tuple[ModerationAssessor, FakeAI]:
    ai = FakeAI(result)
    return ModerationAssessor(ai, min_confidence=min_confidence), ai  # type: ignore[arg-type]


# -- ModerationVerdict.from_payload ----------------------------------------------


def test_from_payload_parses_full_verdict() -> None:
    v = ModerationVerdict.from_payload(
        {'flagged': True, 'category': 'harassment', 'reason': 'targeted insults', 'confidence': 0.92}
    )
    assert v.flagged is True
    assert v.category == 'harassment'
    assert v.reason == 'targeted insults'
    assert v.confidence == 0.92


def test_from_payload_coerces_string_flagged() -> None:
    assert ModerationVerdict.from_payload({'flagged': 'true', 'confidence': 1}).flagged is True
    assert ModerationVerdict.from_payload({'flagged': 'no', 'confidence': 1}).flagged is False


def test_from_payload_unknown_category_becomes_other() -> None:
    v = ModerationVerdict.from_payload({'flagged': True, 'category': 'banter', 'confidence': 1})
    assert v.category == 'other'


def test_from_payload_clamps_confidence_and_defaults() -> None:
    assert ModerationVerdict.from_payload({'flagged': True, 'confidence': 9}).confidence == 1.0
    assert ModerationVerdict.from_payload({'flagged': True, 'confidence': -1}).confidence == 0.0
    assert ModerationVerdict.from_payload({'flagged': True}).confidence == 0.0
    assert ModerationVerdict.from_payload({'flagged': True, 'confidence': 'x'}).confidence == 0.0


def test_from_payload_rejects_non_bool_non_str_flagged() -> None:
    with pytest.raises(SchemaError):
        ModerationVerdict.from_payload({'flagged': 123, 'confidence': 1})


# -- ModerationAssessor.assess ---------------------------------------------------


async def test_assess_returns_flagged_confident_verdict() -> None:
    a, ai = assessor(ModerationVerdict(True, 'hate', 'slur', 0.9))
    verdict = await a.assess('some nasty message')
    assert verdict is not None
    assert verdict.category == 'hate'
    assert ai.calls == 1


async def test_assess_returns_none_when_not_flagged() -> None:
    a, _ = assessor(ModerationVerdict(False, 'none', '', 0.99))
    assert await a.assess('hello friends') is None


async def test_assess_returns_none_for_none_category() -> None:
    # flagged true but category none -> treat as not actionable.
    a, _ = assessor(ModerationVerdict(True, 'none', '', 0.99))
    assert await a.assess('borderline') is None


async def test_assess_returns_none_below_confidence() -> None:
    a, _ = assessor(ModerationVerdict(True, 'spam', 'maybe', 0.5))
    assert await a.assess('buy now at scam.example') is None


async def test_assess_returns_none_when_model_unavailable() -> None:
    a, _ = assessor(None)  # AIService.parse degraded to None
    assert await a.assess('anything') is None


async def test_assess_returns_none_on_empty_text() -> None:
    a, ai = assessor(ModerationVerdict(True, 'spam', '', 0.99))
    assert await a.assess('   ') is None
    assert ai.calls == 0  # never reached the model
