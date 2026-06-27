"""Tests for AI semantic tag retrieval (:mod:`app.services.ai.tags`).

Pure logic: payload parsing and the finder's empty/degraded/validation handling against a
fake AIService. No bot, DB, or model.
"""

from __future__ import annotations

from typing import Any

from app.services.ai import TagFinder, TagMatch, build_tag_find_prompt


class FakeAI:
    def __init__(self, result: Any) -> None:
        self._result = result
        self.calls = 0
        self.last_system: str | None = None

    async def parse(self, text: str, *, schema: Any, system: str, tier: Any) -> Any:
        self.calls += 1
        self.last_system = system
        return self._result


# -- TagMatch --------------------------------------------------------------------


def test_tagmatch_parses_name_and_confidence() -> None:
    m = TagMatch.from_payload({'tag': '  Rules ', 'confidence': 0.9})
    assert m.name == 'Rules'
    assert m.confidence == 0.9


def test_tagmatch_null_tokens_become_none() -> None:
    for token in ('', 'none', 'NULL', 'unknown', 'n/a'):
        assert TagMatch.from_payload({'tag': token, 'confidence': 1.0}).name is None
    assert TagMatch.from_payload({'confidence': 1.0}).name is None


def test_tagmatch_clamps_and_defaults_confidence() -> None:
    assert TagMatch.from_payload({'tag': 'x', 'confidence': 5}).confidence == 1.0
    assert TagMatch.from_payload({'tag': 'x', 'confidence': -1}).confidence == 0.0
    assert TagMatch.from_payload({'tag': 'x', 'confidence': 'bad'}).confidence == 0.0


def test_prompt_lists_every_tag() -> None:
    prompt = build_tag_find_prompt(['rules', 'faq', 'setup'])
    assert '- rules' in prompt and '- faq' in prompt and '- setup' in prompt


# -- TagFinder -------------------------------------------------------------------


async def test_find_returns_real_name_case_insensitively() -> None:
    finder = TagFinder(FakeAI(TagMatch(name='RULES', confidence=0.9)))  # type: ignore[arg-type]
    # candidate is lowercase 'rules' — the canonical spelling is returned, not the model's casing.
    assert await finder.find('what are the guidelines', ['rules', 'faq']) == 'rules'


async def test_find_empty_query_or_no_tags_skips_model() -> None:
    ai = FakeAI(TagMatch(name='rules', confidence=1.0))
    finder = TagFinder(ai)  # type: ignore[arg-type]
    assert await finder.find('   ', ['rules']) is None
    assert await finder.find('anything', []) is None
    assert ai.calls == 0


async def test_find_below_confidence_returns_none() -> None:
    finder = TagFinder(FakeAI(TagMatch(name='rules', confidence=0.2)), min_confidence=0.5)  # type: ignore[arg-type]
    assert await finder.find('q', ['rules']) is None


async def test_find_rejects_hallucinated_name() -> None:
    finder = TagFinder(FakeAI(TagMatch(name='not-a-real-tag', confidence=1.0)))  # type: ignore[arg-type]
    assert await finder.find('q', ['rules', 'faq']) is None


async def test_find_none_when_model_unavailable() -> None:
    finder = TagFinder(FakeAI(None))  # type: ignore[arg-type]
    assert await finder.find('q', ['rules']) is None
