"""Pure minesweeper engine.

Owns the board, mine placement, the iterative flood-fill reveal and win detection with
**zero** Discord dependencies. The Discord-facing binding (the view, the per-cell buttons
and the embeds) lives in ``app/cogs/games/minesweeper_ui.py``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from itertools import chain
from typing import Final

NEIGHBOURS: Final[list[tuple[int, int]]] = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


@dataclass
class MSField:
    x: int
    y: int
    value: int = 0
    revealed: bool = False
    mine: bool = False

    def __eq__(self, other: MSField) -> bool:
        return self.x == other.x and self.y == other.y


class Board:
    """A pure minesweeper board: mine placement, flood-fill reveal and win detection."""

    SIZE: Final[int] = 5

    def __init__(self, mines: int) -> None:
        self.mines: int = mines
        self.board: list[list[MSField]] = [[MSField(x=x, y=y) for x in range(self.SIZE)] for y in range(self.SIZE)]
        self.place_mines()

    def place_mines(self) -> None:
        """Place the mines on the board.

        This is done by setting the `mine` attribute of a MSField to `true`
        and incrementing the value of the surrounding cells.
        """
        for field in random.sample(list(chain.from_iterable(self.board)), self.mines):
            field.mine = True

            for j, i in self.get_neighbours(field):
                if not self.board[j][i].mine:
                    self.board[j][i].value += 1

    @staticmethod
    def get_neighbours(field: MSField) -> list[tuple[int, int]]:
        """Get the neighbours of a cell"""
        return [
            (field.x + i, field.y + j)
            for i, j in NEIGHBOURS
            if (0 <= field.x + i < Board.SIZE) and (0 <= field.y + j < Board.SIZE)
        ]

    def mark(self, field: MSField) -> bool:
        """Mark a cell as selected, iterative flood-fill to avoid RecursionError.

        Returns ``False`` if a mine was hit, ``True`` otherwise.
        """
        stack = [field]

        while stack:
            current = stack.pop()
            if current.revealed:
                continue
            current.revealed = True

            if current.mine:
                return False  # hit a mine

            if current.value == 0:
                for i, j in self.get_neighbours(current):
                    neighbour = self.board[i][j]
                    if not neighbour.revealed:
                        stack.append(neighbour)

        return True

    @property
    def is_won(self) -> bool:
        """Whether every non-mine cell has been revealed."""
        return all(all(kind.revealed for kind in row if not kind.mine) for row in self.board)
