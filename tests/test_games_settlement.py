"""Timeout settlement, free-reward caps, slots RTP, blackjack split rules."""

from __future__ import annotations

import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from app.cogs.games import higherlower_ui, models, slot_ui, tower_ui
from app.cogs.games.engine.blackjack import MAX_HANDS, BlackjackGame
from app.cogs.games.engine.cards import BaseCard
from app.cogs.games.slot_ui import Fruits, grid_multiplier


def _player() -> tuple[MagicMock, MagicMock]:
    balance = MagicMock(add=AsyncMock())
    db = MagicMock(get_user_balance=AsyncMock(return_value=balance))
    player = MagicMock(id=1)
    player.guild.id = 2
    player._state._get_client.return_value = SimpleNamespace(db=db)
    return player, balance


async def test_higherlower_timeout_pays_once() -> None:
    player, balance = _player()
    game = higherlower_ui.HigherLowerGame(player, 100)
    await game.on_timeout()
    await game.on_timeout()
    balance.add.assert_awaited_once_with(cash=100)


async def test_higherlower_timeout_after_cashout_pays_nothing() -> None:
    player, balance = _player()
    game = higherlower_ui.HigherLowerGame(player, 100)
    game.finished = True
    await game.on_timeout()
    balance.add.assert_not_awaited()


async def test_tower_timeout_cashes_out_current_multiplier_once() -> None:
    player, balance = _player()
    game = tower_ui.Tower(player, 100)
    game.add()  # one stack: x1.5
    await game.on_timeout()
    await game.on_timeout()
    balance.add.assert_awaited_once_with(cash=150)


async def test_slots_timeout_refunds_unspun_stake_only() -> None:
    player, balance = _player()
    game = slot_ui.SlotMachine(player, 50)
    await game.on_timeout()
    await game.on_timeout()
    balance.add.assert_awaited_once_with(cash=50)

    player, balance = _player()
    spun = slot_ui.SlotMachine(player, 50)
    spun._start.disabled = True  # spinning / already paid
    await spun.on_timeout()
    balance.add.assert_not_awaited()


def test_free_reward_daily_cap() -> None:
    models._free_rewards.clear()
    assert all(models.claim_free_reward(1, 1, "trivia", limit=3) for _ in range(3))
    assert not models.claim_free_reward(1, 1, "trivia", limit=3)
    assert models.claim_free_reward(1, 1, "minesweeper", limit=3)  # per game
    assert models.claim_free_reward(1, 2, "trivia", limit=3)  # per user


def test_slots_wild_and_every_line_pays() -> None:
    f, c = Fruits.APPLE, Fruits.COOL
    x = Fruits.PEAR
    # top row apple/apple/wild -> one line; everything else mixed
    assert grid_multiplier([[f, f, c], [x, f, Fruits.GRAPE], [Fruits.LEMON, Fruits.MELON, x]]) == 3
    # two full rows win separately
    assert grid_multiplier([[f, f, f], [x, x, x], [f, x, Fruits.GRAPE]]) == 6
    # all-wild row pays the wild jackpot
    assert grid_multiplier([[c, c, c], [f, x, f], [x, f, x]]) == 5 + 3 + 3  # row + both diagonals (wild + x + x)


def test_slots_rtp_in_target_range() -> None:
    rng = random.Random(7)
    symbols = list(Fruits)
    n = 200_000
    total = 0
    for _ in range(n):
        grid = [[rng.choice(symbols) for _ in range(3)] for _ in range(3)]
        total += grid_multiplier(grid)
    assert 0.90 <= total / n <= 0.96


def _pair(game: BlackjackGame, rank: int) -> None:
    game.active_hand.card_arr = np.array([[rank, 0], [rank, 1]])


def test_split_aces_get_one_card_and_auto_stand() -> None:
    game = BlackjackGame(10)
    _pair(game, 14)
    new = game.split()
    assert len(game.player_hands) == 2
    assert all(h.finished and len(h) == 2 for h in game.player_hands)
    assert new.finished
    assert not game.is_running  # dealer already played


def test_resplit_limited_to_three_hands() -> None:
    game = BlackjackGame(10)
    _pair(game, 8)
    game.split()
    assert game.can_split is False or len(game.player_hands) == 2
    # force another pair on the active hand and split again
    _pair(game, 8)
    game.active_hand.finished = False
    assert game.can_split
    game.split()
    assert len(game.player_hands) == MAX_HANDS
    _pair(game, 8)
    game.active_hand.finished = False
    assert not game.can_split
    with pytest.raises(ValueError):
        game.split()


async def test_open_bets_roundtrip_sql_shape() -> None:
    from app.database.repositories.stats import GameStatsRepository

    repo = GameStatsRepository.__new__(GameStatsRepository)
    repo.execute = AsyncMock()  # type: ignore[method-assign]
    repo.fetch = AsyncMock(return_value=[])  # type: ignore[method-assign]
    await repo.open_bet_add("roulette", 1, 2, 3, 50)
    await repo.open_bets_clear("roulette", 2)
    assert await repo.open_bets_take_all() == []
    assert await repo.open_bets_clear("roulette", 2) == 0
    assert repo.execute.await_count == 1


async def test_settle_bets_loses_the_claim_to_a_reload_refund() -> None:
    from app.cogs.games.cog import Games

    cog = Games.__new__(Games)
    cog.bot = MagicMock()
    clear = AsyncMock(return_value=0)  # cog_load's take-all already deleted the rows
    cog.bot.db.game_stats.open_bets_clear = clear
    assert await cog._settle_bets("horserace", 1) is False
    clear.return_value = 2
    assert await cog._settle_bets("horserace", 1) is True
    clear.side_effect = RuntimeError("db down")
    assert await cog._settle_bets("horserace", 1) is True
