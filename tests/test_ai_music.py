"""Tests for the AI music intent service (:mod:`app.services.ai.music`).

Pure logic: parsing a model's ``{query, filter}`` payload and the parser's empty/degraded
handling against a fake AIService. No bot, voice client, or model.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.ai import MusicIntent, MusicIntentParser
from app.services.ai.music import SchemaError


class FakeAI:
    def __init__(self, result: Any) -> None:
        self._result = result
        self.calls = 0

    async def parse(self, text: str, *, schema: Any, system: str, tier: Any) -> Any:
        self.calls += 1
        return self._result


# -- MusicIntent.from_payload ----------------------------------------------------


def test_from_payload_full() -> None:
    intent = MusicIntent.from_payload({'query': 'lofi study beats', 'filter': 'nightcore'})
    assert intent.query == 'lofi study beats'
    assert intent.filter == 'nightcore'


def test_from_payload_unknown_filter_becomes_none() -> None:
    assert MusicIntent.from_payload({'query': 'jazz', 'filter': 'reverb'}).filter == 'none'


def test_from_payload_missing_filter_defaults_none() -> None:
    assert MusicIntent.from_payload({'query': 'jazz'}).filter == 'none'


def test_from_payload_strips_and_lowercases_filter() -> None:
    assert MusicIntent.from_payload({'query': 'x', 'filter': ' NightCore '}).filter == 'nightcore'


def test_from_payload_rejects_empty_query() -> None:
    with pytest.raises(SchemaError):
        MusicIntent.from_payload({'query': '   ', 'filter': 'none'})


def test_from_payload_rejects_missing_query() -> None:
    with pytest.raises(SchemaError):
        MusicIntent.from_payload({'filter': 'bassboost'})


# -- MusicIntentParser.interpret -------------------------------------------------


async def test_interpret_returns_intent() -> None:
    parser = MusicIntentParser(FakeAI(MusicIntent('chill beats', 'none')))  # type: ignore[arg-type]
    intent = await parser.interpret('something chill for studying')
    assert intent is not None
    assert intent.query == 'chill beats'


async def test_interpret_empty_text_skips_model() -> None:
    ai = FakeAI(MusicIntent('x', 'none'))
    parser = MusicIntentParser(ai)  # type: ignore[arg-type]
    assert await parser.interpret('   ') is None
    assert ai.calls == 0


async def test_interpret_returns_none_when_model_unavailable() -> None:
    parser = MusicIntentParser(FakeAI(None))  # type: ignore[arg-type]
    assert await parser.interpret('energetic gym music') is None
