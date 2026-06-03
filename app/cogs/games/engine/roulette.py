"""Pure roulette engine.

Holds the wheel data (which numbers belong to which betting space), the payout
multipliers, the spin, and winner resolution with **zero** Discord (or any
presentation) dependencies, mirroring the ``poker`` and ``tictactoe`` engines.

The Discord-facing binding -- the ``Space`` converter enum, ``Bet`` (which carries a
``discord.Member``), the table view, the modal and the embed rendering -- lives in
``app/cogs/games/roulette_ui.py``.
"""

from __future__ import annotations

import enum
import random

#: Numbers coloured red on a European roulette wheel.
RED_NUMBERS = [1, 3, 5, 7, 9, 12, 14, 16, 18, 21, 23, 25, 27, 30, 32, 34, 36]
#: Numbers coloured black on a European roulette wheel.
BLACK_NUMBERS = [2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35]

#: Maps each betting space (keyed by its enum *name*) to the numbers it covers.
SPACE_NUMBERS: dict[str, list[int]] = {f"SINGLE_{n}": [n] for n in range(37)}
SPACE_NUMBERS.update(
    {
        "COLUMN_FIRST": list(range(1, 37, 3)),
        "COLUMN_SECOND": list(range(2, 37, 3)),
        "COLUMN_THIRD": list(range(3, 37, 3)),
        "DOZEN_FIRST": list(range(1, 13)),
        "DOZEN_SECOND": list(range(13, 25)),
        "DOZEN_THIRD": list(range(25, 37)),
        "HALF_FIRST": list(range(1, 19)),
        "HALF_SECOND": list(range(19, 37)),
        "RED": RED_NUMBERS,
        "BLACK": BLACK_NUMBERS,
        "EVEN": list(range(2, 37, 2)),
        "ODD": list(range(1, 37, 2)),
    }
)

#: Spaces that need a trailing placeholder field to keep the embed grid aligned.
PLACEHOLDER_FIELDS: dict[str, int] = {
    "HALF_SECOND": 1,
    "BLACK": 1,
    "ODD": 1,
}


class Payout(enum.Enum):
    """The payout multiplier for each kind of space."""

    SINGLE_NUMBER = 36
    DOZEN = 3
    COLUMN = 3
    HALF = 2
    COLOR = 2
    ODD_EVEN = 2

    def __str__(self) -> str:
        return str(self.value)

    @classmethod
    def by_value(cls, value: object) -> int:
        """Return the payout multiplier for a space's ``value``."""
        if value in ("1st", "2nd", "3rd"):
            return cls.COLUMN.value
        elif value in ("1-12", "13-24", "25-36"):
            return cls.DOZEN.value
        elif value in ("1-18", "19-36"):
            return cls.HALF.value
        elif value in ("Red", "Black"):
            return cls.COLOR.value
        elif value in ("Even", "Odd"):
            return cls.ODD_EVEN.value
        else:
            return cls.SINGLE_NUMBER.value


def spin() -> int:
    """Spin the wheel, returning the landed number (``0``-``36``)."""
    return random.randint(0, 36)


def is_winning(space_name: str, result: int) -> bool:
    """Return ``True`` if the space identified by ``space_name`` covers ``result``."""
    return result in SPACE_NUMBERS[space_name]
