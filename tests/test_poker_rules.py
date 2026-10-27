"""Rule-level regression tests for the Texas Hold'em engine.

Hand ranking is checked against a tiny brute-force reference evaluator; the
betting state machine is driven through whole hands with rigged cards; and a
random-play property test asserts that chips are never created or destroyed.
Odds are disabled (``OddsMode.NONE``) everywhere except the explicit equity
test, because the Monte-Carlo run is the expensive part of the engine.
"""

from __future__ import annotations

import random
from collections import Counter
from itertools import combinations

import numpy as np
import pytest

from app.cogs.games.engine.poker import Hand, OddsMode, Ranker, TableState, TexasHoldem, comb_index

SUITS = {'d': 0, 'c': 1, 's': 2, 'h': 3}
VALUES = {str(i): i for i in range(2, 10)} | {'T': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14}
FULL_DECK = [(v, s) for v in range(2, 15) for s in range(4)]


def cards(text: str) -> list[tuple[int, int]]:
    return [(VALUES[c[0]], SUITS[c[1]]) for c in text.split()]


def hand(text: str) -> Hand:
    h = Hand()
    for card in cards(text):
        h.add(np.array([card]))
    return h


def value(hole: str, board: str) -> int:
    return hand(hole).evaluate(np.array(cards(board))).value


# -- Reference evaluator ----------------------------------------------------


def ref5(five: list[tuple[int, int]]) -> tuple:
    values = sorted((v for v, _ in five), reverse=True)
    flush = len({s for _, s in five}) == 1
    if values == [14, 5, 4, 3, 2]:
        values = [5, 4, 3, 2, 1]
    straight = len(set(values)) == 5 and values[0] - values[4] == 4
    groups = sorted(Counter(values).items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    shape = [c for _, c in groups]
    kickers = [v for v, _ in groups]
    if straight and flush:
        return (8, values)
    if shape[0] == 4:
        return (7, kickers)
    if shape == [3, 2]:
        return (6, kickers)
    if flush:
        return (5, values)
    if straight:
        return (4, values)
    if shape[0] == 3:
        return (3, kickers)
    if shape[:2] == [2, 2]:
        return (2, kickers)
    if shape[0] == 2:
        return (1, kickers)
    return (0, values)


def ref7(seven: list[tuple[int, int]]) -> tuple:
    return max(ref5(list(c)) for c in combinations(seven, 5))


def sign(x: float) -> int:
    return (x > 0) - (x < 0)


# -- Hand evaluation --------------------------------------------------------


@pytest.mark.parametrize(
    ('better', 'worse', 'board'),
    [
        # wheel is the lowest straight
        ('6h 2s', 'Ah 2c', '3d 4c 5s 9h Kd'),
        # pair of 2s with an ace kicker loses to a pair of kings
        ('Kh Kc', '2h 2c', 'Ad 7s 4d 9c Jh'),
        # kicker decides equal pairs
        ('Ah Kc', 'As Qc', 'Ad 7c 4s 2h 9d'),
        # ace-high (holding a deuce) beats king-high
        ('Ah 2c', 'Kh 3c', '4d 7s 9c Jh 6s'),
        # straight flush beats a higher flush
        ('9h Ac', 'Ah Kh', '5h 6h 7h 8h 2c'),
        # full house: trips rank decides before the pair
        ('3h 3c', '2h Kc', '3d 2s 2d Ks 9c'),
        # two pair: higher top pair beats more kickers
        ('Kh 2c', 'Qh Ac', 'Kd Qd 2s 7c 8h'),
        # quads kicker plays from the board
        ('Ah 3c', 'Kh 3d', '9d 9c 9s 9h 2d'),
    ],
)
def test_hand_ordering_regressions(better: str, worse: str, board: str) -> None:
    assert value(better, board) > value(worse, board)


@pytest.mark.parametrize(
    ('a', 'b', 'board'),
    [
        ('2h 3c', '4h 5c', 'As Ks Qs Js Ts'),  # board plays: royal flush split
        ('2h 3c', '2d 3d', 'Ah Kc 9s 8d 7c'),  # same kickers from the board
    ],
)
def test_hand_ties(a: str, b: str, board: str) -> None:
    assert value(a, board) == value(b, board)


def test_evaluate_matches_reference_on_random_hands() -> None:
    rng = random.Random(1234)
    for _ in range(400):
        dealt = rng.sample(FULL_DECK, 9)
        h1, h2, board = dealt[:2], dealt[2:4], dealt[4:]
        board_arr = np.array(board)
        e1 = Hand()
        e1.add(np.array(h1))
        e2 = Hand()
        e2.add(np.array(h2))
        got = sign(e1.evaluate(board_arr).value - e2.evaluate(board_arr).value)
        r1, r2 = ref7(h1 + board), ref7(h2 + board)
        want = sign((r1 > r2) - (r1 < r2))
        assert got == want, (h1, h2, board, r1, r2)


def test_vectorised_ranker_matches_reference() -> None:
    """The batch path the Monte-Carlo simulation uses must order hands like the reference."""
    rng = random.Random(99)
    sevens = np.array([rng.sample(FULL_DECK, 7) for _ in range(300)])
    scores = Ranker.rank_all_hands(sevens[:, comb_index(7, 5), :])
    refs = [ref7([tuple(c) for c in s.tolist()]) for s in sevens]
    for i in range(0, 300, 2):
        got = sign(int(scores[i]) - int(scores[i + 1]))  # type: ignore[index]
        want = sign((refs[i] > refs[i + 1]) - (refs[i] < refs[i + 1]))
        assert got == want, (sevens[i].tolist(), sevens[i + 1].tolist())


def test_evaluate_keeps_card_suits_intact() -> None:
    result = hand('Ah Kh').evaluate(np.array(cards('Qh Jh Th 2c 3d')))
    assert sorted((c.value, c.suit) for c in result.cards) == sorted(cards('Ah Kh Qh Jh Th'))


# -- Helpers for the betting state machine ----------------------------------


def table(*stacks: int, buy_in: int = 1000) -> TexasHoldem:
    engine = TexasHoldem(first_buy_in=buy_in)
    engine.odds_mode = OddsMode.NONE
    for i, stack in enumerate(stacks):
        engine.add_player(f'p{i}', stack)  # type: ignore[arg-type]
    return engine


def rig(engine: TexasHoldem, holes: list[str], board: str) -> None:
    """Replace the dealt hole cards and stack the deck so the board comes out as given."""
    used = set()
    for player, hole in zip(engine.players, holes, strict=True):
        player.hand = hand(hole)
        used.update(cards(hole))
    top = cards(board)
    used.update(top)
    rest = [c for c in FULL_DECK if c not in used]
    engine.deck.cards = np.array(top + rest)


def total_chips(engine: TexasHoldem) -> int:
    on_table = sum(p.stack for p in engine.players)
    if engine.state is TableState.RUNNING:
        on_table += engine.pot.amount + sum(p.amount for p in engine.side_pots)
    return on_table


def act(engine: TexasHoldem, action: str, amount: int = 0) -> None:
    """Run one action for the current player exactly like the UI does."""
    if action == 'fold':
        engine.Fold()
        engine.switch_player()
    elif action == 'check':
        engine.Check()
        engine.switch_player()
    elif action == 'call':
        engine.Call()
        engine.switch_player()
    elif action == 'raise':
        engine.Raise(amount)
        engine.switch_player(by_raise=True)
    elif action == 'allin':
        engine.AllIn()
        engine.switch_player(by_raise=True)


# -- Betting rounds ---------------------------------------------------------


def test_heads_up_blinds_and_action_order() -> None:
    e = table(1000, 1000)
    e.start()
    assert e.blind_index == (0, 1)  # dealer posts the small blind
    assert e.player_index == 0  # and acts first pre-flop
    act(e, 'call')
    assert e.player_index == 1 and len(e.community_arr) == 0  # BB option
    act(e, 'check')
    assert len(e.community_arr) == 3
    assert e.player_index == 1  # non-dealer acts first after the flop


def test_big_blind_gets_option_and_street_closes_after_it() -> None:
    e = table(1000, 1000, 1000)
    e.start()  # dealer 0, SB 1, BB 2, UTG 0
    act(e, 'call')
    act(e, 'call')
    assert e.player_index == 2 and len(e.community_arr) == 0
    act(e, 'check')
    assert len(e.community_arr) == 3


def test_raiser_does_not_act_again_once_everyone_calls() -> None:
    e = table(1000, 1000, 1000)
    e.start()
    act(e, 'raise', 30)
    act(e, 'call')
    act(e, 'call')
    assert len(e.community_arr) == 3  # straight to the flop, no extra option for the raiser


def test_folded_players_bet_does_not_carry_into_next_street() -> None:
    e = table(1000, 1000, 1000)
    e.start()
    act(e, 'call')  # UTG
    act(e, 'fold')  # SB folds with 5 in front of them
    act(e, 'check')  # BB
    assert len(e.community_arr) == 3
    assert max(p.bet for p in e.players) == 0
    first = e.current_player
    assert first is e.players[2]  # SB folded, so the BB opens the flop
    before = first.stack
    act(e, 'call')  # nothing to call on a fresh street
    assert first.stack == before


def test_min_raise_tracks_last_raise_size() -> None:
    e = table(1000, 1000, 1000)
    e.start()
    assert e.min_raise_to == 20  # BB 10 -> min raise to 20
    act(e, 'raise', 30)  # UTG raises to 30 (a 20 raise)
    assert e.min_raise_to == 50


def test_short_all_in_does_not_reopen_raising() -> None:
    e = table(1000, 150, 1000)
    e.start()  # UTG p0, SB p1 (150), BB p2
    act(e, 'raise', 100)  # p0 to 100 (raise of 90)
    act(e, 'allin')  # p1 to 150: only 50 more, not a full raise
    assert e.current_player is e.players[2]
    assert e.can_raise(e.players[2])  # BB has not acted since the full raise
    act(e, 'call')
    assert e.current_player is e.players[0]  # p0 must still respond to the extra 50
    assert not e.can_raise(e.players[0])


def test_heads_up_all_in_still_needs_a_call() -> None:
    e = table(1000, 1000)
    e.start()
    act(e, 'allin')
    assert e.state is TableState.RUNNING
    assert e.current_player is e.players[1]  # BB may fold or call; nothing is auto-called


def test_everyone_all_in_runs_out_the_board() -> None:
    e = table(300, 500, 700)
    e.start()
    total = total_chips(e)
    act(e, 'allin')
    act(e, 'allin')
    act(e, 'allin')
    assert e.state is TableState.FINISHED
    assert len(e.community_arr) == 5
    assert total_chips(e) == total


def test_short_blind_goes_all_in_instead_of_negative() -> None:
    e = table(1000, 1000, 3)
    e.start()  # p2 is BB with only 3 chips
    assert e.players[2].stack == 0
    assert e.players[2].all_in
    assert total_chips(e) == 2003


# -- Pots -------------------------------------------------------------------


def test_side_pots_with_three_different_all_ins() -> None:
    e = table(100, 300, 500)
    e.start()
    rig(e, ['Ah Ad', 'Kh Kd', 'Qh Qd'], '2c 7d 9h Js 3c')
    act(e, 'allin')  # p0: 100
    act(e, 'allin')  # p1: 300
    act(e, 'allin')  # p2: 500 (200 of it uncalled)
    assert e.state is TableState.FINISHED
    assert [p.stack for p in e.players] == [300, 400, 200]


def test_split_pot_odd_chip_goes_left_of_button() -> None:
    e = table(1000, 1000, 1000)
    e.start()  # dealer p0, SB p1, BB p2
    rig(e, ['2h 3c', '4h 5c', '6h 7c'], 'As Ks Qs Js Ts')
    act(e, 'call')  # p0 10
    act(e, 'fold')  # p1 leaves 5 dead
    act(e, 'check')  # p2
    for _ in range(3):
        act(e, 'check')
        act(e, 'check')
    assert e.state is TableState.FINISHED
    assert sum(p.stack for p in e.players) == 3000
    assert e.players[2].stack == 1003  # 13 of the 25: first winner left of the button
    assert e.players[0].stack == 1002


def test_fold_to_one_player_awards_everything() -> None:
    e = table(1000, 1000, 1000)
    e.start()
    act(e, 'raise', 50)
    act(e, 'fold')
    act(e, 'fold')
    assert e.state is TableState.FINISHED
    assert [p.stack for p in e.players] == [1015, 995, 990]


def test_finished_hand_cannot_be_settled_twice() -> None:
    e = table(1000, 1000)
    e.start()
    act(e, 'fold')
    stacks = [p.stack for p in e.players]
    e.switch_player()  # a stale button press after the hand ended
    assert [p.stack for p in e.players] == stacks


def test_abort_hand_refunds_contributions() -> None:
    e = table(1000, 1000, 1000)
    e.start()
    act(e, 'raise', 100)
    e.abort_hand()
    assert [p.stack for p in e.players] == [1000, 1000, 1000]


# -- Table lifecycle --------------------------------------------------------


def test_deck_is_fresh_every_hand() -> None:
    e = table(1000, 1000, 1000, 1000)
    for _ in range(8):
        e.start()
        assert len(e.deck) == 52 - 8
        while e.state is TableState.RUNNING:
            act(e, 'call' if e.current_player.bet < max(p.bet for p in e.players) else 'check')
        e.prepare_next_game()


def test_button_rotation_skips_busted_seat_correctly() -> None:
    e = table(1000, 1000, 1000, 1000)
    e.dealer_index = 2  # p2 had the button
    e.state = TableState.FINISHED
    e.players[1].stack = 0  # p1 busts
    e.prepare_next_game()
    assert [p.member for p in e.players] == ['p0', 'p2', 'p3']
    assert e.players[e.dealer_index].member == 'p3'  # type: ignore[index]


def test_sitting_out_first_actor_is_skipped() -> None:
    e = table(1000, 1000, 1000, 1000)
    e.players[3].sitting_out = True  # p3 would be UTG
    e.start()
    assert e.current_player is e.players[0]


def test_deck_shuffle_is_a_permutation() -> None:
    e = table(1000, 1000)
    e.start()
    seen = {tuple(c) for c in e.deck.cards.tolist()}
    seen |= {tuple(c) for p in e.players for c in p.hand.card_arr.tolist()}
    assert len(seen) == 52


# -- Property: chip conservation over random play ---------------------------


def test_random_play_conserves_chips() -> None:
    rng = random.Random(7)
    for _ in range(40):
        stacks = [rng.choice([3, 40, 150, 600, 1000]) for _ in range(rng.randint(2, 4))]
        e = table(*stacks)
        total = sum(stacks)
        for _hand in range(6):
            if e.state is TableState.STOPPED and e.hand_number:
                break
            e.start()
            steps = 0
            while e.state is TableState.RUNNING:
                p = e.current_player
                assert not p.folded and not p.all_in
                to_call = max(x.bet for x in e.players) - p.bet
                options = ['fold', 'allin', 'call' if to_call else 'check']
                if e.can_raise(p) and p.stack > e.min_raise_to - p.bet:
                    options.append('raise')
                choice = rng.choice(options)
                amount = e.min_raise_to - p.bet if choice == 'raise' else 0
                act(e, choice, amount)
                assert all(x.stack >= 0 for x in e.players)
                assert total_chips(e) == total
                steps += 1
                assert steps < 200
            assert sum(x.stack for x in e.players) == total
            e.prepare_next_game()
            total = sum(x.stack for x in e.players)  # busted players leave with 0


# -- Odds -------------------------------------------------------------------


def test_preflop_equity_aces_vs_kings() -> None:
    e = table(1000, 1000)
    e.deal()
    e.players[0].hand = hand('Ah Ad')
    e.players[1].hand = hand('Kh Kd')
    used = cards('Ah Ad Kh Kd')
    e.deck.cards = np.array([c for c in FULL_DECK if c not in used])
    odds = e.simulate(num_scenarios=20000, odds_type='tie_win')
    assert isinstance(odds, dict)
    assert 79.0 < odds['Player 1 Win'] < 85.0
