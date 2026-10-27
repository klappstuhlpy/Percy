"""Regression tests for bugs found in the non-poker games (each failed before its fix)."""

from __future__ import annotations

import random
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from app.cogs.games.engine import higherlower, horserace
from app.cogs.games.engine import minesweeper as ms
from app.cogs.games.engine.blackjack import BlackjackGame, WinningType
from app.cogs.games.engine.cards import BaseCard
from app.cogs.games.models import Game


def test_higherlower_multiplier_priced_from_the_card_the_call_was_made_on() -> None:
    game = higherlower.HigherLower()
    game.current, game.next = BaseCard(2, 0), BaseCard(14, 0)
    _, correct = game.guess(True)
    assert correct
    # Was priced from the *new* card (an Ace: 0 ways higher) -> multiplier 0 after a correct call.
    assert game.multiplier == pytest.approx(13 / 12 * higherlower.HOUSE_EDGE)


def _board(monkeypatch: pytest.MonkeyPatch, mines: set[tuple[int, int]]) -> ms.Board:
    monkeypatch.setattr(ms.random, "sample", lambda pop, k: [f for f in pop if (f.x, f.y) in mines])
    return ms.Board(len(mines))


def test_minesweeper_counts_are_not_transposed(monkeypatch: pytest.MonkeyPatch) -> None:
    board = _board(monkeypatch, {(0, 2)})
    assert board.board[2][1].value == 1  # (x=1, y=2) touches the mine
    assert board.board[1][2].value == 0  # (x=2, y=1) does not


def test_minesweeper_flood_fill_respects_a_wall(monkeypatch: pytest.MonkeyPatch) -> None:
    board = _board(monkeypatch, {(2, y) for y in range(5)})
    assert board.mark(board.board[0][0])
    for row in board.board:
        for f in row:
            assert f.revealed == (f.x < 2)


def test_minesweeper_first_click_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    board = _board(monkeypatch, {(0, 0), (4, 4), (3, 3)})
    monkeypatch.undo()
    assert board.mark(board.board[0][0])
    assert sum(f.mine for row in board.board for f in row) == 3
    for row in board.board:
        for f in row:
            if not f.mine:
                assert f.value == sum(board.board[y][x].mine for x, y in board.get_neighbours(f))


def test_horserace_dead_heats_do_not_favour_lane_one() -> None:
    rng = random.Random(7)
    wins = Counter(horserace.simulate_race(rng)[0] for _ in range(6000))
    assert wins[0] < 1300  # fair share is 1000; first-index tie-break gave lane 1 far more


def _bj(player: list[int], dealer: list[int]) -> BlackjackGame:
    game = BlackjackGame(bet=100)
    game.player_hands[0].card_arr = np.array([[v, 0] for v in player])
    game.dealer.card_arr = np.array([[v, 0] for v in dealer])
    game.dealer.set_card_hidden(1, False)
    return game


def test_blackjack_surrender_is_void_against_dealer_blackjack() -> None:
    game = _bj([10, 6], [14, 13])
    game.surrender()
    assert game.get_winner(game.player_hands[0]) is WinningType.DEALER_BLACKJACK
    game = _bj([10, 6], [10, 7])
    game.surrender()
    assert game.get_winner(game.player_hands[0]) is WinningType.SURRENDER


async def test_blackjack_natural_pays_three_to_two() -> None:
    from app.cogs.games import blackjack_bridge

    table = blackjack_bridge.Blackjack(MagicMock(), bet=100)
    table.engine.player_hands[0].card_arr = np.array([[14, 0], [13, 0]])
    table.engine.dealer.card_arr = np.array([[10, 0], [9, 0]])
    table.engine.dealer.set_card_hidden(1, False)
    balance = MagicMock(add=AsyncMock())
    interaction = MagicMock()
    interaction.client.db.get_user_balance = AsyncMock(return_value=balance)
    interaction.client.db.game_stats.record_result = AsyncMock()
    await table.view.finish_game(interaction)
    balance.add.assert_awaited_once_with(cash=250)  # stake 100 back + 150; it paid 150 total (a net loss)


def test_tower_has_no_positive_expectation_strategy() -> None:
    from app.cogs.games.tower_ui import Tower

    survive = 1 - Tower.CRASH_CHANCE
    for stacks in range(1, 30):
        assert survive**stacks * (1 + 0.5 * stacks) < 1


@pytest.mark.parametrize("amount", ["-500", "1000"])
async def test_roulette_modal_bet_must_be_paid(monkeypatch: pytest.MonkeyPatch, amount: str) -> None:
    from app.cogs.games import roulette_ui

    table = roulette_ui.Table(MagicMock())
    table.message = MagicMock(edit=AsyncMock())
    inter = MagicMock(guild_id=5)
    inter.response.send_message = AsyncMock()
    inter.client.db.get_user_balance = AsyncMock()
    inter.client.db.economy.get_settings = AsyncMock(return_value=None)
    inter.client.db.economy.debit_cash = AsyncMock(return_value=None)  # cannot afford

    class FakeModal:
        def __init__(self) -> None:
            self.interaction = inter
            self.bet_amount = SimpleNamespace(value=amount)
            self.space = SimpleNamespace(value="Red")

        async def wait(self) -> None:
            return None

    monkeypatch.setattr(roulette_ui, "PlaceBetModal", FakeModal)
    outer = MagicMock()
    outer.response.send_modal = AsyncMock()
    await table.view._on_place_bet(outer)
    assert table.bets == []  # was placed for free (and negative amounts were accepted)


async def test_russian_roulette_cylinder_reloads_after_a_bang() -> None:
    from app.cogs.games import russianroulette_ui as rr

    game = rr.RussianRoulette(MagicMock(), MagicMock(), 100)
    players = [rr.RRPlayer(MagicMock(id=i)) for i in range(3)]
    game.players = players
    game.phase, game.order, game.turn = rr.Phase.PLAYING, list(players), 0
    game.chambers = [1, 0, 0, 0, 0, 0]
    inter = MagicMock()
    inter.user.id = 0
    inter.response.edit_message = AsyncMock()
    await game._on_pull(inter)
    assert sorted(game.chambers) == [0, 0, 0, 0, 0, 1]  # a spent cylinder ran dry -> IndexError, pot stranded


@pytest.mark.parametrize("game", list(Game))
def test_every_game_has_an_icon(game: Game) -> None:
    assert game.icon
