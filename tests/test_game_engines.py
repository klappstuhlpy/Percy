"""Unit tests for the new game engines (Higher/Lower, Dice, Mines, Wordle, Horse Race, Trivia).

These exercise the pure logic — odds, multipliers, scoring and payouts — without a bot.
"""

from __future__ import annotations

import datetime
import random

import numpy as np
import pytest

from app.cogs.games.engine import crash, dice, fight, heist, higherlower, horserace, mines, scratch
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


# -- Snake Eyes / Gamble ----------------------------------------------------


def test_snake_eyes_multipliers_and_edge() -> None:
    assert dice.snake_eyes_multiplier(1, 1) == dice.SNAKE_BOTH
    assert dice.snake_eyes_multiplier(1, 4) == dice.snake_eyes_multiplier(6, 1) == dice.SNAKE_ONE
    assert dice.snake_eyes_multiplier(2, 3) == 0.0
    ev = sum(dice.snake_eyes_multiplier(a, b) for a in range(1, 7) for b in range(1, 7)) / 36
    assert 0.9 < ev < 1.0


def test_gamble_house_edge() -> None:
    sides = range(1, dice.GAMBLE_SIDES + 1)
    wins = sum(p > h for p in sides for h in sides)
    ev = wins / dice.GAMBLE_SIDES**2 * dice.GAMBLE_PAYOUT
    assert 0.9 < ev < 1.0
    p, h = dice.gamble_roll(random.Random(1))
    assert 1 <= p <= dice.GAMBLE_SIDES and 1 <= h <= dice.GAMBLE_SIDES


# -- Crash ------------------------------------------------------------------


def test_crash_point_distribution_matches_edge() -> None:
    rng = random.Random(7)
    points = [crash.draw_crash_point(rng) for _ in range(40_000)]
    assert min(points) >= 1.0
    for target in (1.5, 2.0, 5.0):
        survive = sum(p > target for p in points) / len(points)
        assert survive == pytest.approx(crash.HOUSE_EDGE / target, abs=0.015)


def test_crash_run_cash_out_and_crash() -> None:
    game = crash.Crash(rng=random.Random(0))
    assert game.cash_out() is None  # nothing to lock in before the first tick
    game.crash_point = 1.5
    game.tick()
    assert game.multiplier == 1.15 and not game.finished
    assert game.cash_out() == 1.15
    game.tick()  # no-op once finished
    assert game.tick_count == 1

    game = crash.Crash(rng=random.Random(0))
    game.crash_point = 1.2
    game.tick()
    game.tick()
    assert game.crashed and game.cash_out() is None


def test_crash_auto_cashout_beats_crash_in_same_tick() -> None:
    game = crash.Crash(auto_cashout=1.2, rng=random.Random(0))
    game.crash_point = 1.25
    game.tick()
    assert not game.finished
    game.tick()  # x1.32 overshoots both, but the auto target came first
    assert game.cashed_at == 1.2 and not game.crashed


def test_crash_caps_at_max_multiplier() -> None:
    game = crash.Crash(rng=random.Random(0))
    game.crash_point = 1_000.0
    while not game.finished:
        game.tick()
    assert game.cashed_at == crash.MAX_MULTIPLIER


# -- Scratch ----------------------------------------------------------------


def test_scratch_card_pays_average_of_picks() -> None:
    card = scratch.ScratchCard(random.Random(3))
    card.tiles = [10.0, 0.0, 2.0, 1.0, 0.0, 0.0, 0.5, 3.0, 0.0]
    card.scratch(0)
    card.scratch(2)
    with pytest.raises(ValueError):
        card.scratch(2)
    card.scratch(3)
    assert card.finished and card.multiplier == round(13 / 3, 2)
    with pytest.raises(RuntimeError):
        card.scratch(4)


def test_scratch_expected_return_below_one() -> None:
    assert 0.9 < scratch.expected_multiplier() < 1.0


# -- Fight ------------------------------------------------------------------


def test_fight_punch_defend_and_win() -> None:
    game = fight.Fight(random.Random(5))
    first = game.act(fight.Action.DEFEND)
    assert first.actor == 0 and game.guarding[0] and game.turn == 1
    blocked = game.act(fight.Action.PUNCH)
    assert blocked.blocked and not game.guarding[0]
    while not game.finished:
        game.act(fight.Action.PUNCH)
    assert min(game.hp) == 0 and game.hp[game.winner] > 0  # type: ignore[index]


def test_fight_flee_forfeits() -> None:
    game = fight.Fight(random.Random(1))
    game.act(fight.Action.FLEE)
    assert game.winner == 1
    with pytest.raises(RuntimeError):
        game.act(fight.Action.PUNCH)


def test_fight_heal_never_exceeds_max() -> None:
    game = fight.Fight(random.Random(2))
    assert game.act(fight.Action.DEFEND).heal == 0
    assert game.hp[0] == fight.MAX_HP


# -- Heist ------------------------------------------------------------------


def test_heist_chance_grows_and_caps() -> None:
    assert heist.success_chance(2) > heist.success_chance(1)
    assert heist.success_chance(heist.MAX_CREW) <= 0.75


def test_heist_split_conserves_coins() -> None:
    rng = random.Random(11)
    for _ in range(500):
        result = heist.resolve_heist(3, 10_000, rng)
        if result.success:
            assert result.loot == result.share * 3 <= 10_000 * 0.35
            assert result.fine_fraction == 0.0
        else:
            assert result.loot == 0 and 0.1 <= result.fine_fraction <= 0.25


def test_blackjack_split_21_loses_to_dealer_blackjack() -> None:
    """A 21 made after a split is not a natural: a dealer blackjack beats it, it is no push."""
    from app.cogs.games.engine.blackjack import BlackjackGame, WinningType

    game = BlackjackGame(10)
    game.dealer = _hand(14, 13)
    player = _hand(14, 13)
    player.splitted = True
    assert game.get_winner(player) is WinningType.DEALER_BLACKJACK
    player.splitted = False
    assert game.get_winner(player) is WinningType.PUSH
