"""Tests for the pure Texas Hold'em engine.

These exercise the betting state machine and player-management logic in
isolation -- no Discord, no database, no rendering -- which is exactly what the
engine extraction (Phase 4) was meant to enable. ``member`` is a plain string
identity token here, demonstrating the engine's independence from discord.py.

The card-ranking simulation (``start``/``switch_player`` -> ``simulate``) is
intentionally not exercised here: it is a heavy Monte-Carlo computation, so these
tests drive the deterministic betting/transition logic directly.
"""

from __future__ import annotations

from app.games.engine.poker import Pot, TableState, TexasHoldem


def make_engine(buy_in: int = 1000) -> TexasHoldem:
    return TexasHoldem(first_buy_in=buy_in)


def test_buy_in_and_blind_bounds() -> None:
    engine = make_engine(1000)
    assert engine.min_buy_in == 500
    assert engine.max_buy_in == 10_000
    assert engine.big_blind == 10  # max(int(1000 * 0.01), 2)
    assert engine.small_blind == 5
    assert engine.state is TableState.STOPPED


def test_add_and_remove_player_returns_leftover_stack() -> None:
    engine = make_engine()
    engine.add_player('alice', 500)
    engine.add_player('bob', 300)
    assert len(engine.players) == 2

    leftover = engine.remove_player('alice')
    assert leftover == 500
    assert len(engine.players) == 1
    assert engine.players[0].member == 'bob'


def test_raise_updates_pot_stack_and_bet() -> None:
    engine = make_engine()
    engine.add_player('a', 500)
    engine.add_player('b', 500)
    engine.player_index = 0

    engine.Raise(100)

    assert engine.pot.amount == 100
    assert engine.players[0].stack == 400
    assert engine.players[0].bet == 100
    assert engine.players[0].checked is True


def test_call_matches_highest_bet() -> None:
    engine = make_engine()
    engine.add_player('a', 500)
    engine.add_player('b', 500)

    engine.player_index = 0
    engine.Raise(100)

    engine.player_index = 1
    engine.Call()

    assert engine.players[1].bet == 100
    assert engine.players[1].stack == 400
    assert engine.pot.amount == 200


def test_fold_excludes_player_from_playing() -> None:
    engine = make_engine()
    engine.add_player('a', 500)
    engine.add_player('b', 500)
    engine.player_index = 0

    engine.Fold()

    assert engine.players[0].folded is True
    assert engine.players[0] not in engine.playing_players
    assert len(engine.playing_players) == 1


def test_all_in_empties_stack_and_sets_flags() -> None:
    engine = make_engine()
    engine.add_player('a', 500)
    engine.add_player('b', 500)
    engine.player_index = 0

    engine.AllIn()

    player = engine.players[0]
    assert player.all_in is True
    assert player.wait_for_allin_call is True
    assert player.stack == 0
    assert player.bet == 500
    assert engine.pot.amount == 500


def test_to_draw_starts_at_flop() -> None:
    engine = make_engine()
    assert engine.to_draw == 3  # empty board -> deal the flop


def test_pot_arithmetic_and_int() -> None:
    pot = Pot(amount=0)
    pot += 50
    pot -= 20
    assert int(pot) == 30
    assert len(pot) == 30


def test_prepare_next_game_removes_broke_players() -> None:
    engine = make_engine()
    engine.add_player('a', 100)
    engine.add_player('b', 0)   # ran out of chips
    engine.add_player('c', 50)
    engine.blind_index = (0, 1)

    removed = engine.prepare_next_game()

    assert [p.member for p in removed] == ['b']
    assert {p.member for p in engine.players} == {'a', 'c'}
    assert engine.state is TableState.PREPARED  # two players remain


def test_prepare_next_game_stops_with_too_few_players() -> None:
    engine = make_engine()
    engine.add_player('a', 100)
    engine.add_player('b', 0)
    engine.blind_index = (0, 1)

    engine.prepare_next_game()

    # Only one funded player remains -> the table stops.
    assert engine.state is TableState.STOPPED


def test_autoplay_turn_folds_when_facing_a_bet() -> None:
    # Three players so that one fold does not end the hand (which would trigger
    # showdown evaluation on undealt cards).
    engine = make_engine()
    engine.add_player('a', 500)
    engine.add_player('b', 500)
    engine.add_player('c', 500)

    # 'a' bets, putting 'b' behind.
    engine.player_index = 0
    engine.Raise(100)

    # Auto-play for 'b' who is now facing a bet: they fold.
    b = engine.players[1]
    acted = engine.autoplay_turn(b)

    assert acted is True
    assert b.folded is True
