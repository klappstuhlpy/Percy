"""Unit tests for the new game engines (Higher/Lower, Dice, Mines, Wordle, Horse Race, Trivia).

These exercise the pure logic — odds, multipliers, scoring and payouts — without a bot.
"""

from __future__ import annotations

import datetime
import random

import numpy as np
import pytest

from app.cogs.games.engine import dice, higherlower, horserace, mines
from app.cogs.games.engine.blackjack import Hand
from app.cogs.games.engine.cards import BaseCard
from app.cogs.games.engine.trivia import build_round
from app.cogs.games.engine.wordle import (
    MAX_TRIES,
    WORD_LENGTH,
    LetterState,
    daily_index,
    is_solved,
    score_guess,
)

# -- Blackjack hand value (soft-ace adjustment) -----------------------------


def _hand(*values: int) -> Hand:
    """Build a hand from card values (14=Ace, 11-13=face, else pip); suit is irrelevant."""
    hand = Hand(bet=0)
    hand.card_arr = np.array([[v, 0] for v in values], dtype=int)
    return hand


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((14, 10), 21),       # natural blackjack: ace counts as 11
        ((14, 5), 16),        # soft 16
        ((14, 14, 10), 12),   # two aces + ten -> one ace demoted (not 32)
        ((14, 5, 10), 16),    # ace demoted to avoid busting
        ((14, 14, 14, 8), 21),  # three aces + 8 -> 11+1+1+8
        ((10, 10, 5), 25),    # hard bust, no aces to soften
        ((13, 12), 20),       # two face cards
    ],
)
def test_blackjack_hand_value_softens_aces(values: tuple[int, ...], expected: int) -> None:
    assert _hand(*values).value == expected


# -- Higher/Lower -----------------------------------------------------------


def test_higherlower_odds_counts() -> None:
    game = higherlower.HigherLower()
    game.current = BaseCard(5, 0)
    assert game.odds(True).favorable == 9   # 6..14
    assert game.odds(False).favorable == 3  # 2,3,4
    assert game.odds(True).total == 13


def test_higherlower_extremes_have_zero_step() -> None:
    game = higherlower.HigherLower()
    game.current = BaseCard(14, 0)
    assert game.odds(True).favorable == 0
    assert game.step_multiplier(True) == 0.0


def test_higherlower_correct_guess_grows_multiplier() -> None:
    game = higherlower.HigherLower()
    game.current = BaseCard(5, 0)
    game.next = BaseCard(10, 0)
    _, correct = game.guess(True)
    assert correct is True
    assert game.multiplier > 1.0
    assert game.busted is False


def test_higherlower_wrong_guess_busts() -> None:
    game = higherlower.HigherLower()
    game.current = BaseCard(5, 0)
    game.next = BaseCard(3, 0)
    _, correct = game.guess(True)
    assert correct is False
    assert game.busted is True
    with pytest.raises(RuntimeError):
        game.guess(True)


# -- Dice -------------------------------------------------------------------


def test_dice_ways_total_36() -> None:
    assert sum(dice.WAYS.values()) == 36


def test_dice_payout_rarer_pays_more() -> None:
    assert dice.payout_multiplier(7) == 5.4
    assert dice.payout_multiplier(2) == 32.4
    assert dice.payout_multiplier(2) > dice.payout_multiplier(7)


def test_dice_payout_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        dice.payout_multiplier(13)


def test_dice_roll_in_range() -> None:
    for _ in range(50):
        d1, d2 = dice.roll(random.Random(_))
        assert 1 <= d1 <= 6 and 1 <= d2 <= 6


# -- Mines ------------------------------------------------------------------


def test_mines_places_requested_mines() -> None:
    game = mines.Mines(3, random.Random(0))
    assert len(game.mine_positions) == 3
    assert game.multiplier == 1.0
    assert game.safe_total == mines.TILES - 3


def test_mines_reveal_gem_then_mine() -> None:
    game = mines.Mines(3, random.Random(0))
    safe = next(i for i in range(mines.TILES) if i not in game.mine_positions)
    assert game.reveal(safe) is True
    assert game.multiplier > 1.0
    assert game.next_multiplier() > game.multiplier

    a_mine = next(iter(game.mine_positions))
    assert game.reveal(a_mine) is False
    assert game.busted is True
    with pytest.raises(RuntimeError):
        game.reveal(safe)


def test_mines_clear_board() -> None:
    game = mines.Mines(mines.TILES - 1, random.Random(1))  # exactly one gem
    safe = next(i for i in range(mines.TILES) if i not in game.mine_positions)
    assert game.reveal(safe) is True
    assert game.cleared is True


# -- Wordle -----------------------------------------------------------------


def test_wordle_all_correct() -> None:
    assert is_solved(score_guess("crane", "crane"))


def test_wordle_present_and_absent() -> None:
    states = score_guess("pearl", "apple")
    assert states == [
        LetterState.PRESENT,
        LetterState.PRESENT,
        LetterState.PRESENT,
        LetterState.ABSENT,
        LetterState.PRESENT,
    ]


def test_wordle_double_letter_handling() -> None:
    # answer "alley" has two L's; guess "hello" should green one L and yellow the other.
    states = score_guess("hello", "alley")
    assert states == [
        LetterState.ABSENT,
        LetterState.PRESENT,
        LetterState.CORRECT,
        LetterState.PRESENT,
        LetterState.ABSENT,
    ]


def test_wordle_daily_index_is_deterministic() -> None:
    today = datetime.date(2026, 6, 12)
    a = daily_index(500, 123456789, today)
    b = daily_index(500, 123456789, today)
    assert a == b and 0 <= a < 500
    # Different day → (almost certainly) different index, always in range.
    other = daily_index(500, 123456789, datetime.date(2026, 6, 13))
    assert 0 <= other < 500


def test_wordle_constants() -> None:
    assert WORD_LENGTH == 5 and MAX_TRIES == 6


# -- Horse Race -------------------------------------------------------------


def test_horserace_produces_a_winner() -> None:
    winner, frames = horserace.simulate_race(random.Random(7))
    assert 0 <= winner < horserace.NUM_HORSES
    assert frames
    assert max(frames[-1]) >= horserace.TRACK_LENGTH


def test_horserace_parimutuel_split() -> None:
    # pool 300, 100 on the winner, 10% edge -> 2.7x per winning coin.
    assert horserace.parimutuel_multiplier(300, 100) == pytest.approx(2.7)
    # nobody on the winner -> house keeps the pool.
    assert horserace.parimutuel_multiplier(300, 0) == 0.0


# -- Trivia -----------------------------------------------------------------


def test_trivia_build_round_tracks_correct_answer() -> None:
    raw = {"category": "Test", "question": "q?", "correct": "right", "incorrect": ["a", "b", "c"]}
    rnd = build_round(raw, random.Random(3))  # type: ignore[arg-type]
    assert len(rnd.options) == 4
    assert set(rnd.options) == {"right", "a", "b", "c"}
    assert rnd.options[rnd.correct_index] == "right"
    assert rnd.correct == "right"
