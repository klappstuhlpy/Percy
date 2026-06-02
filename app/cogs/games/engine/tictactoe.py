"""Pure tic-tac-toe engine.

Holds the board representation and win/draw detection with **zero** Discord (or any
presentation) dependencies, mirroring the ``poker`` engine. The Discord-facing binding
(players, buttons, embeds, economy) lives in ``app/cogs/games/tictactoe_ui.py``.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass


class BoardKind(enum.Enum):
    Empty = 0
    X = -1
    O = 1


@dataclass()
class BoardState:
    kind: BoardKind

    @classmethod
    def empty(cls) -> BoardState:
        return BoardState(kind=BoardKind.Empty)


class Board:
    """A pure 3x3 tic-tac-toe board with win/draw detection."""

    SIZE = 3

    def __init__(self) -> None:
        self.cells: list[list[BoardState]] = [
            [BoardState.empty() for _ in range(self.SIZE)] for _ in range(self.SIZE)
        ]

    def get(self, x: int, y: int) -> BoardState:
        """Return the :class:`BoardState` at column ``x``, row ``y``."""
        return self.cells[y][x]

    def place(self, x: int, y: int, kind: BoardKind) -> None:
        """Mark the cell at column ``x``, row ``y`` with ``kind``."""
        self.cells[y][x].kind = kind

    def count(self, kind: BoardKind) -> int:
        """Return how many cells currently hold ``kind``."""
        return sum(1 for row in self.cells for cell in row if cell.kind is kind)

    def is_full(self) -> bool:
        """Return ``True`` if every cell is occupied."""
        return all(cell.kind is not BoardKind.Empty for row in self.cells for cell in row)

    def winner(self) -> BoardKind | None:
        """Return the winning :class:`BoardKind`, :attr:`BoardKind.Empty` for a draw, or ``None`` if the game continues."""
        board = self.cells

        for across in board:
            value = sum(p.kind.value for p in across)
            if value == 3:
                return BoardKind.O
            elif value == -3:
                return BoardKind.X

        for line in range(self.SIZE):
            value = board[0][line].kind.value + board[1][line].kind.value + board[2][line].kind.value
            if value == 3:
                return BoardKind.O
            elif value == -3:
                return BoardKind.X

        diag = board[0][2].kind.value + board[1][1].kind.value + board[2][0].kind.value
        if diag == 3:
            return BoardKind.O
        elif diag == -3:
            return BoardKind.X

        diag = board[0][0].kind.value + board[1][1].kind.value + board[2][2].kind.value
        if diag == 3:
            return BoardKind.O
        elif diag == -3:
            return BoardKind.X

        if self.is_full():
            return BoardKind.Empty

        return None
