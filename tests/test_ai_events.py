"""Tests for AI poll/giveaway extraction (:mod:`app.services.ai.events`).

Pure logic: payload parsing, duration normalisation, and the extractor's empty/degraded
handling against a fake AIService. No bot, timers, or model.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.ai import EventExtractor, GiveawayRequest, PollRequest
from app.services.ai.events import SchemaError, normalize_duration


class FakeAI:
    def __init__(self, result: Any) -> None:
        self._result = result
        self.calls = 0

    async def parse(self, text: str, *, schema: Any, system: str, tier: Any) -> Any:
        self.calls += 1
        return self._result


# -- normalize_duration ----------------------------------------------------------


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('2d', '2d'),
        ('2 days', '2d'),
        ('12 hours', '12h'),
        ('30 minutes', '30m'),
        ('1 week', '1w'),
        ('3 months', '3mo'),
        ('', '1d'),  # default
        ('soon', '1d'),  # unparseable -> default
        (None, '1d'),
    ],
)
def test_normalize_duration(value: Any, expected: str) -> None:
    assert normalize_duration(value) == expected


# -- PollRequest -----------------------------------------------------------------


def test_poll_from_payload_full() -> None:
    req = PollRequest.from_payload({'question': 'Movie night?', 'options': ['Yes', 'No', 'Maybe'], 'duration': '2 days'})
    assert req.question == 'Movie night?'
    assert req.options == ['Yes', 'No', 'Maybe']
    assert req.duration == '2d'


def test_poll_filters_blank_options_and_caps_at_eight() -> None:
    req = PollRequest.from_payload(
        {'question': 'Q', 'options': ['a', '  ', 'b', *[str(i) for i in range(10)]], 'duration': '1h'}
    )
    assert '' not in req.options and '  ' not in req.options
    assert len(req.options) == 8


def test_poll_rejects_too_few_options() -> None:
    with pytest.raises(SchemaError):
        PollRequest.from_payload({'question': 'Q', 'options': ['only one'], 'duration': '1d'})


def test_poll_rejects_non_list_options() -> None:
    with pytest.raises(SchemaError):
        PollRequest.from_payload({'question': 'Q', 'options': 'yes,no', 'duration': '1d'})


# -- GiveawayRequest -------------------------------------------------------------


def test_giveaway_from_payload_full() -> None:
    req = GiveawayRequest.from_payload({'prize': 'Nitro', 'winners': 3, 'duration': '6h'})
    assert (req.prize, req.winners, req.duration) == ('Nitro', 3, '6h')


def test_giveaway_winner_defaults_and_clamps() -> None:
    assert GiveawayRequest.from_payload({'prize': 'X'}).winners == 1
    assert GiveawayRequest.from_payload({'prize': 'X', 'winners': 0}).winners == 1
    assert GiveawayRequest.from_payload({'prize': 'X', 'winners': 'bad'}).winners == 1


def test_giveaway_rejects_empty_prize() -> None:
    with pytest.raises(SchemaError):
        GiveawayRequest.from_payload({'prize': '   ', 'winners': 1})


# -- EventExtractor --------------------------------------------------------------


async def test_extractor_poll_returns_request() -> None:
    extractor = EventExtractor(FakeAI(PollRequest('Q', ['a', 'b'], '1d')))  # type: ignore[arg-type]
    assert (await extractor.poll('ask something')) is not None


async def test_extractor_giveaway_empty_skips_model() -> None:
    ai = FakeAI(GiveawayRequest('X', 1, '1d'))
    extractor = EventExtractor(ai)  # type: ignore[arg-type]
    assert await extractor.giveaway('   ') is None
    assert ai.calls == 0


async def test_extractor_returns_none_when_model_unavailable() -> None:
    extractor = EventExtractor(FakeAI(None))  # type: ignore[arg-type]
    assert await extractor.poll('ask something') is None
    assert await extractor.giveaway('give away nitro') is None
