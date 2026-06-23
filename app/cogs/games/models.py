from __future__ import annotations

from enum import StrEnum

__all__ = ('Game', 'GameResult')

from config import Emojis


class GameResult(StrEnum):
    """The outcome of a single round from the playing member's perspective.

    ``PUSH`` covers draws/ties (blackjack push, a drawn TicTacToe) — it counts
    toward neither wins nor losses and never breaks a win/loss streak.
    """

    WIN = 'win'
    LOSS = 'loss'
    PUSH = 'push'


class Game(StrEnum):
    """The catalogue of games whose rounds are tracked in ``game_stats``.

    The string value is the stable key stored in the database; ``label`` and
    ``icon`` are presentation-only and used by the ``/stats games`` views.
    """

    POKER = 'poker'
    BLACKJACK = 'blackjack'
    SLOTS = 'slots'
    ROULETTE = 'roulette'
    TOWER = 'tower'
    TICTACTOE = 'tictactoe'
    MINESWEEPER = 'minesweeper'
    HANGMAN = 'hangman'
    HIGHERLOWER = 'higherlower'
    DICE = 'dice'
    MINES = 'mines'
    TRIVIA = 'trivia'
    WORDLE = 'wordle'
    RUSSIAN_ROULETTE = 'russianroulette'
    HORSERACE = 'horserace'

    @property
    def label(self) -> str:
        """:class:`str`: A human-friendly display name."""
        return _LABELS[self]

    @property
    def icon(self) -> str:
        """:class:`str`: A unicode emoji used as the game's badge."""
        return _ICONS[self]


_LABELS: dict[Game, str] = {
    Game.POKER: "Poker",
    Game.BLACKJACK: "Blackjack",
    Game.SLOTS: "Slots",
    Game.ROULETTE: "Roulette",
    Game.TOWER: "Tower",
    Game.TICTACTOE: "TicTacToe",
    Game.MINESWEEPER: "Minesweeper",
    Game.HANGMAN: "Hangman",
    Game.HIGHERLOWER: "Higher or Lower",
    Game.DICE: "Dice",
    Game.MINES: "Mines",
    Game.TRIVIA: "Trivia",
    Game.WORDLE: "Wordle",
    Game.RUSSIAN_ROULETTE: "Russian Roulette",
    Game.HORSERACE: "Horse Race",
}

_ICONS: dict[Game, str] = {
    Game.POKER: "\N{PLAYING CARD BLACK JOKER}",
    Game.BLACKJACK: Emojis.blackjack,
    Game.SLOTS: Emojis.lotteryslots,
    Game.ROULETTE: "\N{GAME DIE}",
    Game.TOWER: "\N{HOUSE WITH GARDEN}",
    Game.TICTACTOE: "\N{HEAVY MULTIPLICATION X}",
    Game.MINESWEEPER: "\N{BOMB}",
    Game.HANGMAN: "\N{INPUT SYMBOL FOR LATIN LETTERS}",
    Game.HIGHERLOWER: Emojis.higherlower,
    Game.DICE: "\N{DIRECT HIT}",
    Game.MINES: "\N{GEM STONE}",
    Game.TRIVIA: "\N{BLACK QUESTION MARK ORNAMENT}",
    Game.WORDLE: "\N{LARGE GREEN SQUARE}",
    Game.RUSSIAN_ROULETTE: "\N{PISTOL}",
    Game.HORSERACE: "\N{HORSE}",
}
