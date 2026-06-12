"""Pure helpers for the Trivia game.

Loading the question bank (file IO) stays in the cog; this module only turns a raw
question dict into a presentation-ready round with shuffled answers, which is the
part worth unit-testing.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TypedDict

__all__ = ('RawQuestion', 'TriviaRound', 'build_round')


class RawQuestion(TypedDict):
    category: str
    question: str
    correct: str
    incorrect: list[str]


@dataclass(frozen=True)
class TriviaRound:
    """A question with its answers shuffled into display order."""

    category: str
    question: str
    options: list[str]
    correct_index: int

    @property
    def correct(self) -> str:
        return self.options[self.correct_index]


def build_round(raw: RawQuestion, rng: random.Random | None = None) -> TriviaRound:
    """Shuffles a raw question's answers and records where the correct one landed."""
    source = rng or random
    options = [raw['correct'], *raw['incorrect']]
    source.shuffle(options)
    return TriviaRound(
        category=raw['category'],
        question=raw['question'],
        options=options,
        correct_index=options.index(raw['correct']),
    )
