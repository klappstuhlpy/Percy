"""Pure engine for the Wordle game.

Scores a guess against the answer with correct double-letter handling (two passes:
greens first, then yellows constrained by remaining letter counts) and derives the
deterministic per-guild daily word. No Discord imports — fully unit-testable.
"""

from __future__ import annotations

from collections import Counter
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime

__all__ = ('MAX_TRIES', 'WORD_LENGTH', 'LetterState', 'daily_index', 'is_solved', 'score_guess')

WORD_LENGTH: int = 5
MAX_TRIES: int = 6


class LetterState(Enum):
    """Per-letter feedback for a guess."""

    CORRECT = "correct"  # right letter, right spot (green)
    PRESENT = "present"  # right letter, wrong spot (yellow)
    ABSENT = "absent"    # not in the word (grey)

    @property
    def square(self) -> str:
        return _SQUARES[self]


_SQUARES: dict[LetterState, str] = {
    LetterState.CORRECT: "\N{LARGE GREEN SQUARE}",
    LetterState.PRESENT: "\N{LARGE YELLOW SQUARE}",
    LetterState.ABSENT: "\N{BLACK LARGE SQUARE}",
}


def score_guess(guess: str, answer: str) -> list[LetterState]:
    """Scores ``guess`` against ``answer`` (both same length, case-insensitive)."""
    guess = guess.lower()
    answer = answer.lower()
    if len(guess) != len(answer):
        raise ValueError("guess and answer must be the same length")

    result = [LetterState.ABSENT] * len(guess)
    remaining = Counter(answer)

    # First pass: exact-position matches consume a letter from the pool.
    for i, ch in enumerate(guess):
        if ch == answer[i]:
            result[i] = LetterState.CORRECT
            remaining[ch] -= 1

    # Second pass: present-but-misplaced, only while copies remain unconsumed.
    for i, ch in enumerate(guess):
        if result[i] is LetterState.CORRECT:
            continue
        if remaining[ch] > 0:
            result[i] = LetterState.PRESENT
            remaining[ch] -= 1

    return result


def is_solved(states: list[LetterState]) -> bool:
    """Whether every letter is correctly placed."""
    return all(state is LetterState.CORRECT for state in states)


def daily_index(word_count: int, guild_id: int, date: datetime.date) -> int:
    """Deterministic index into a word list for a guild on a given day.

    Stable across process restarts (unlike :func:`hash`), so every member in a
    guild faces the same puzzle for the whole day.
    """
    salt = (guild_id * 2654435761 + date.toordinal() * 40503) % 2147483647
    return salt % word_count
