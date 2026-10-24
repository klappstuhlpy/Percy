"""Pure work-shift challenges for the economy ``work`` command (no ``discord`` imports).

Each challenge is a quick pick-one-of-four question. The cog times it, and the
result only scales the shift payout (:data:`WORK_SUCCESS_BONUS` /
:data:`WORK_FAIL_FACTOR`); the shift always counts toward the career ladder.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

__all__ = (
    'CHALLENGE_KINDS',
    'WORK_CHALLENGE_TIMEOUT',
    'WORK_FAIL_FACTOR',
    'WORK_SUCCESS_BONUS',
    'Challenge',
    'generate_challenge',
)

#: Payout multiplier for a correct answer.
WORK_SUCCESS_BONUS = 1.25
#: Payout multiplier for a wrong answer or a timeout.
WORK_FAIL_FACTOR = 0.5
#: Seconds the member has to answer.
WORK_CHALLENGE_TIMEOUT = 20.0

CHALLENGE_KINDS: tuple[str, ...] = ('unscramble', 'memory', 'colour')

_WORDS: tuple[str, ...] = (
    'barista', 'cashier', 'cook', 'invoice', 'ledger', 'shift', 'boss', 'office',
    'deadline', 'meeting', 'payroll', 'client', 'wrench', 'laptop', 'scalpel',
    'rocket', 'coffee', 'tickets', 'orders', 'budget', 'report', 'manager', 'uniform',
)

_EMOJIS: tuple[str, ...] = (
    '\N{RED APPLE}', '\N{BANANA}', '\N{GRAPES}', '\N{CHERRIES}',
    '\N{LEMON}', '\N{WATERMELON}', '\N{STRAWBERRY}', '\N{PEACH}',
)

_COLOURS: tuple[tuple[str, str], ...] = (
    ('red', '\N{LARGE RED SQUARE}'),
    ('blue', '\N{LARGE BLUE SQUARE}'),
    ('green', '\N{LARGE GREEN SQUARE}'),
    ('yellow', '\N{LARGE YELLOW SQUARE}'),
    ('orange', '\N{LARGE ORANGE SQUARE}'),
    ('purple', '\N{LARGE PURPLE SQUARE}'),
)

_OPTION_COUNT = 4


@dataclass(frozen=True, slots=True)
class Challenge:
    """One generated work challenge."""

    kind: str
    #: Text shown to the member (for ``memory`` this is the sequence to remember).
    prompt: str
    #: Exactly four distinct button labels, in display order.
    options: tuple[str, ...]
    #: The correct entry of :attr:`options`.
    answer: str


def _scramble(word: str, rng: random.Random) -> str:
    letters = list(word)
    for _ in range(20):
        rng.shuffle(letters)
        if ''.join(letters) != word:
            return ''.join(letters)
    return word[::-1]  # ponytail: only reachable for a one-letter-repeated word; none ship in _WORDS.


def _unscramble(rng: random.Random) -> Challenge:
    word = rng.choice(_WORDS)
    options = [word, *rng.sample([w for w in _WORDS if w != word], _OPTION_COUNT - 1)]
    rng.shuffle(options)
    return Challenge('unscramble', f'Unscramble: **{_scramble(word, rng).upper()}**', tuple(options), word)


def _memory(rng: random.Random) -> Challenge:
    sequence = tuple(rng.choices(_EMOJIS, k=4))
    options = {sequence}
    while len(options) < _OPTION_COUNT:
        options.add(tuple(rng.choices(_EMOJIS, k=4)))
    labels = [''.join(seq) for seq in options]
    rng.shuffle(labels)
    answer = ''.join(sequence)
    return Challenge('memory', answer, tuple(labels), answer)


def _colour(rng: random.Random) -> Challenge:
    word, _ = rng.choice(_COLOURS)
    square = rng.choice(_COLOURS)[1]  # often a different colour than the word: that is the trap
    options = [word, *rng.sample([c for c, _ in _COLOURS if c != word], _OPTION_COUNT - 1)]
    rng.shuffle(options)
    return Challenge('colour', f'What colour does the **word** say?\n{square} **{word.upper()}**', tuple(options), word)


def generate_challenge(rng: random.Random | None = None) -> Challenge:
    """Generate a random challenge of any kind."""
    chooser = rng or random.Random()
    return {'unscramble': _unscramble, 'memory': _memory, 'colour': _colour}[chooser.choice(CHALLENGE_KINDS)](chooser)
