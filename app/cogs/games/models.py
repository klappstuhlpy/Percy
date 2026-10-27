from __future__ import annotations

import datetime
from enum import StrEnum

__all__ = ('FREE_REWARDS_PER_DAY', 'Game', 'GameResult', 'claim_free_reward')

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
    COINFLIP = 'coinflip'
    CRASH = 'crash'
    PLINKO = 'plinko'
    KENO = 'keno'
    SCRATCH = 'scratch'
    SNAKE_EYES = 'snakeeyes'
    GAMBLE = 'gamble'
    FIGHT = 'fight'
    HEIST = 'heist'

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
    Game.COINFLIP: "Coinflip",
    Game.CRASH: "Crash",
    Game.PLINKO: "Plinko",
    Game.KENO: "Keno",
    Game.SCRATCH: "Scratch Card",
    Game.SNAKE_EYES: "Snake Eyes",
    Game.GAMBLE: "Gamble",
    Game.FIGHT: "Fight",
    Game.HEIST: "Heist",
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
    Game.COINFLIP: "\N{COIN}",
    Game.CRASH: "\N{CHART WITH UPWARDS TREND}",
    Game.PLINKO: "\N{LARGE ORANGE CIRCLE}",
    Game.KENO: "\N{INPUT SYMBOL FOR NUMBERS}",
    Game.SCRATCH: "\N{TICKET}",
    Game.SNAKE_EYES: "\N{SNAKE}",
    Game.GAMBLE: "\N{GAME DIE}",
    Game.FIGHT: "\N{BOXING GLOVE}",
    Game.HEIST: "\N{BANK}",
}


#: Rewarded wins per member per game per UTC day for games that pay without a stake.
FREE_REWARDS_PER_DAY: int = 10

# ponytail: in-memory, a restart resets the count; move to a table if farming across restarts matters.
_free_rewards: dict[tuple[int, int, str], tuple[int, int]] = {}


def claim_free_reward(guild_id: int, user_id: int, game: str, *, limit: int = FREE_REWARDS_PER_DAY) -> bool:
    """Counts one free-reward win; ``False`` once the member has used up today's ``limit`` for ``game``."""
    today = datetime.datetime.now(datetime.UTC).date().toordinal()
    key = (guild_id, user_id, str(game))
    day, used = _free_rewards.get(key, (today, 0))
    if day != today:
        used = 0
    if used >= limit:
        return False
    _free_rewards[key] = (today, used + 1)
    return True
