"""Pure Texas Hold'em poker engine.

This module contains the complete game logic and state machine for Texas
Hold'em: card ranking, hand evaluation, pots/side-pots, the betting actions and
the Monte-Carlo odds simulation. It has **no** ``discord`` imports and performs
no IO. ``Player.member`` is an opaque identity token (the cog passes a
``discord.Member``); the engine never calls Discord APIs on it.

Rendering (embeds), the autoplay timer and the economy refund live in the
``app.cogs.games._poker`` bridge, which drives this engine.
"""

from __future__ import annotations

import enum
import multiprocessing
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import chain, combinations
from typing import Any, Literal, NamedTuple, cast

import discord
import numpy as np
from joblib import Parallel, delayed
from scipy.special import comb

from app.cogs.games.engine.cards import NAMED_HAND, SUITS, UNAMED, BaseCard, BaseHand, Deck
from app.utils import RevDict, fnumb

__all__ = (
    "Card",
    "CombResult",
    "Hand",
    "HandResult",
    "OddsMode",
    "Player",
    "Pot",
    "Ranker",
    "RankingItem",
    "TableState",
    "TexasHoldem",
    "comb_index",
    "item_by_count",
)


class TableState(enum.Enum):
    """Represents the state of the table"""

    STOPPED = 0
    RUNNING = 1
    FINISHED = 2
    PREPARED = 3


class OddsMode(enum.Enum):
    """Represents the odds calculation mode for analysis."""

    NONE = "none"  # No odds calculation
    LIVE = "live"  # Only active players (realistic game state)
    FULL = "full"  # All players including folded (hypothetical)


class Card(BaseCard):
    """Represents a card in a deck"""

    @property
    def display_text_short(self) -> str:
        """Returns the display text of the card"""
        return f"{self.name.title()}s"

    @classmethod
    def from_arr(cls, card_arr: tuple) -> Card:
        """Returns a card from a string"""
        return cls(value=card_arr[0], suit=int(card_arr[1]))


def item_by_count(array: np.ndarray, n: int) -> np.ndarray[Any, np.dtype[Any]]:
    """Returns the `n` common element from an iterable."""
    counts = np.bincount(array.flatten())
    if n not in counts:
        # placeholder
        return np.zeros(10, dtype=int)  # type: ignore[return-value]
    return np.argwhere(counts == n).flatten()  # type: ignore[return-value]


def comb_index(n: int, k: int) -> np.ndarray:
    """Returns the index of all combinations of k elements from n elements."""
    count = comb(n, k, exact=True)
    index = np.fromiter(chain.from_iterable(combinations(range(n), k)), dtype=int, count=int(count * k))  # type: ignore[call-overload]
    return index.reshape(-1, k) if k > 1 else index[:, np.newaxis]


class Hand(BaseHand[Card]):
    """Represents a hand of cards for a poker game"""

    def evaluate(self, community_arr: np.ndarray) -> HandResult:
        """Returns the value of the player's hand.

        The value is calculated by combining the hand of the player with the community cards and
        calculating the value of each combination. The combination with the highest value is returned.

        Returns
        -------
        str
            The value of the player's hand.
        """
        combs: CombResult | None = self.hand_value(community_arr)

        if combs is None:
            # Pre-flop
            high_card = Card.from_arr(np.max(self.card_arr, axis=0))
            return HandResult(
                name=f"High Card, {high_card.display_text}",
                cards=[Card(suit=suit, value=value) for value, suit in self.card_arr],
                # in this case, we just summarized the cards values of the hand
                value=sum(self.card_arr[:, 0]),
            )

        best_comb: RankingItem = np.max(combs.ranking)

        name = NAMED_HAND[np.max(best_comb.rank) // 16**5]
        if best_comb.name is not None:
            name += f", {best_comb.name}"

        return HandResult(
            name=name,
            cards=[Card(suit=x[1], value=x[0]) for x in combs.all_combos[0, np.argmax(combs.ranking), :, :]],
            value=best_comb.rank,
        )

    def hand_value(self, community_arr: np.ndarray) -> CombResult | None:
        """Returns the value of the player's hand.

        The value is calculated by combining the hand of the player with the community cards and
        calculating the value of each combination. The combination with the highest value is returned.

        Returns
        -------
        CombResult | None
            The value of the player's hand.
        """
        if len(community_arr) < 3:
            return None

        player_valid_hand = np.concatenate([self.card_arr, community_arr], axis=0)
        all_combos = np.expand_dims(player_valid_hand, axis=0)[:, comb_index(len(player_valid_hand), 5), :]
        ranking: np.ndarray[Any, np.dtype[Any]] = cast(
            "np.ndarray[Any, np.dtype[Any]]", Ranker.rank_all_hands(all_combos, return_all=True)
        )
        return CombResult(all_combos=all_combos, ranking=ranking)


class CombResult(NamedTuple):
    """Represents the result of a combination of cards

    Parameters
    ----------
    all_combos : np.ndarray
        An array of all combinations of cards.
    ranking : np.ndarray[Any, np.dtype[Any]]
        An array of the ranking of each combination of cards.
    """

    all_combos: np.ndarray
    ranking: np.ndarray[Any, np.dtype[Any]]


class HandResult(NamedTuple):
    """Represents the result of a hand of cards"""

    name: str
    cards: list[Card]
    value: int


@dataclass
class HandHistoryEntry:
    """Records a single hand's history for review."""

    hand_number: int
    timestamp: str  # ISO format string (not datetime to avoid Date issues)
    players: list[str]  # Player display names at start
    hole_cards: dict[str, list[tuple[int, int]]]  # player_name -> [(value, suit), ...]
    community_cards: list[tuple[int, int]]
    actions: list[str]  # ["Player 1 raises 100", "Player 2 calls", ...]
    pot_total: int
    winners: list[str]
    winning_hand: str | None = None
    blinds: tuple[int, int] = (0, 0)  # (small, big)


@dataclass
class RankingItem:
    """Represents the ranking of a hand of cards"""

    rank: int
    cards: np.ndarray
    name: str | None = None

    def __ge__(self, other: RankingItem) -> bool:
        if not isinstance(other, RankingItem):
            raise TypeError(f"unsupported operand type(s) for >=: {type(self)} and {type(other)}")

        return self.rank >= other.rank

    def __gt__(self, other: RankingItem) -> bool:
        if not isinstance(other, RankingItem):
            raise TypeError(f"unsupported operand type(s) for >: {type(self)} and {type(other)}")

        return self.rank > other.rank

    def __floordiv__(self, other: Any) -> int:
        if not isinstance(other, int):
            raise TypeError(f"unsupported operand type(s) for //: {type(self)} and {type(other)}")

        return self.rank // other

    def __imul__(self, other: Any) -> RankingItem:
        if not isinstance(other, (int, np.ndarray)):
            raise TypeError(f"unsupported operand type(s) for *: {type(self)} and {type(other)}")

        self.rank = int(self.rank * other)
        return self


class Ranker:
    """Represents the ranking of a hand of cards"""

    @classmethod
    def rank_all_hands(
        cls, hand_combos: np.ndarray, return_all: bool = False
    ) -> RankingItem | np.ndarray[Any, np.dtype[Any]]:
        """Returns the rank of all combinations of cards."""
        rank_res_arr: np.ndarray[Any, np.dtype[Any]] = np.zeros(  # type: ignore
            shape=(hand_combos.shape[1], hand_combos.shape[0]), dtype=RankingItem
        )

        for scenario in range(hand_combos.shape[1]):
            rank_res_arr[scenario, :] = cls.rank_one_hand(hand_combos[:, scenario, :, :], group_by=not return_all)

        if return_all:
            return rank_res_arr
        else:
            return np.max(rank_res_arr, axis=0)

    @classmethod
    def rank_one_hand(cls, hand_combos: np.ndarray, group_by: bool = False) -> np.ndarray[Any, np.dtype[Any]]:
        """Returns the rank of a combination of cards.

        The rank is calculated by checking the combination of cards for a straight flush, four of a kind, full house,
        flush, straight, three of a kind, two pairs, one pair, and high card.

        Parameters
        ----------
        hand_combos : np.ndarray
            The combination of cards.
        group_by : bool
            Whether to group the results by rank.

        Returns
        -------
        np.ndarray[Any, np.dtype[Any]]
            The rank of the combination of cards.
        """
        num_combos = hand_combos[:, :, 0]
        num_combos.sort(axis=1)

        suit_combos = hand_combos[:, :, 1]

        is_suit_arr = cls.is_suit_arr(suit_combos)
        is_straight_arr = cls.is_straight_arr(num_combos)

        rank_arr: np.ndarray[Any, np.dtype[Any]] = np.zeros(num_combos.shape[0], dtype=RankingItem)  # type: ignore

        cls.straight_flush_check(num_combos, rank_arr, is_straight_arr, is_suit_arr)
        cls.four_of_a_kind_check(num_combos, rank_arr)
        cls.full_house_check(num_combos, rank_arr)
        cls.flush_check(num_combos, suit_combos, rank_arr, is_suit_arr)
        cls.straight_check(num_combos, rank_arr, is_straight_arr)
        cls.three_of_a_kind_check(num_combos, rank_arr)
        cls.two_pairs_check(num_combos, rank_arr)
        cls.one_pair_check(num_combos, rank_arr)
        cls.high_card_check(num_combos, rank_arr)

        if group_by:
            return np.array([x.rank for x in rank_arr]) * (16**5) + np.sum(
                num_combos * np.power(16, np.arange(0, 5)), axis=1
            )

        for i, ranking in enumerate(rank_arr):
            ranking = cast("RankingItem", ranking)
            # Example: Implement the logic for each RankingItem
            rank_arr[i] = RankingItem(
                name=ranking.name,
                cards=ranking.cards,
                rank=ranking.rank * (16**5) + np.sum(ranking.cards * np.power(16, np.arange(0, 5))),
            )

        return rank_arr

    @staticmethod
    def is_straight_arr(num_combos: np.ndarray) -> bool:
        """Returns whether the combination of cards is a straight."""
        straight_check: np.ndarray = np.zeros(len(num_combos), dtype=int)
        for i in range(4):
            if i <= 2:
                straight_check += num_combos[:, i] == (num_combos[:, i + 1] - 1)
            else:
                straight_check += num_combos[:, i] == (num_combos[:, i + 1] - 1)
                straight_check += (num_combos[:, i] == 5) & (num_combos[:, i + 1] == 14)

        return straight_check == 4

    @staticmethod
    def is_suit_arr(suit_combos: np.ndarray) -> bool:
        """Returns whether the combination of cards is a flush."""
        return np.max(suit_combos, axis=1) == np.min(suit_combos, axis=1)

    @staticmethod
    def straight_flush_check(
        num_combos: np.ndarray, rank_arr: np.ndarray[Any, np.dtype[Any]], straight_arr: bool, suit_arr: bool
    ) -> None:
        """Checks if the combination of cards is a straight flush."""
        ace_low_straight = np.max(num_combos[:, 4]) == 14 and np.min(num_combos[:, 0]) == 2
        straight_label = "Low" if ace_low_straight else "High"

        rank_arr[(rank_arr == 0) & (straight_arr & suit_arr)] = RankingItem(
            rank=8, name=f"{UNAMED[np.max(num_combos[:, 4])]} {straight_label}", cards=num_combos[0, :5]
        )

        # Rearrange order of 2345A to A2345
        reorder_idx = (rank_arr == 8) & (num_combos[:, 0] == 2) & (num_combos[:, 4] == 14)
        num_combos[reorder_idx, :] = np.concatenate([num_combos[reorder_idx, 4:], num_combos[reorder_idx, :4]], axis=1)

    @staticmethod
    def four_of_a_kind_check(num_combos: np.ndarray, rank_arr: np.ndarray[Any, np.dtype[Any]]) -> None:
        """Checks if the combination of cards is a four of a kind."""
        small = np.all(num_combos[:, 0:4] == num_combos[:, :1], axis=1)  # 22223
        large = np.all(num_combos[:, 1:] == num_combos[:, 4:], axis=1)  # 24444

        condition = small | large
        four_of_a_kind = item_by_count(num_combos[condition], 4)
        rank_arr[(rank_arr == 0) & condition] = RankingItem(rank=7, name=f"{four_of_a_kind[0]}s", cards=num_combos[0, :5])

        reorder_idx = (rank_arr == 7) & small
        num_combos[reorder_idx, :] = np.concatenate([num_combos[reorder_idx, 4:], num_combos[reorder_idx, :4]], axis=1)

    @staticmethod
    def full_house_check(num_combos: np.ndarray, rank_arr: np.ndarray[Any, np.dtype[Any]]) -> None:
        """Checks if the combination of cards is a full house."""
        small = np.all(
            (num_combos[:, 0:3] == num_combos[:, :1]) & (num_combos[:, 3:4] == num_combos[:, 4:5]), axis=1
        )  # 22233

        large = np.all(
            (num_combos[:, 0:1] == num_combos[:, 1:2]) & (num_combos[:, 2:5] == num_combos[:, 4:]), axis=1
        )  # 22444

        condition = small | large
        three_pair, two_pair = item_by_count(num_combos[condition], 3), item_by_count(num_combos[condition], 2)
        rank_arr[(rank_arr == 0) & condition] = RankingItem(
            rank=6, name=f"{UNAMED[three_pair[0]]}s over {UNAMED[two_pair[0]]}s", cards=num_combos[0, :5]
        )

        reorder_idx = (rank_arr == 6) & small
        num_combos[reorder_idx, :] = np.concatenate([num_combos[reorder_idx, 3:], num_combos[reorder_idx, :3]], axis=1)

    @staticmethod
    def flush_check(
        num_combos: np.ndarray, suit_combos: np.ndarray, rank_arr: np.ndarray[Any, np.dtype[Any]], suit_arr: bool
    ) -> None:
        """Checks if the combination of cards is a flush."""
        rank_arr[(rank_arr == 0) & suit_arr] = RankingItem(
            rank=5, name=RevDict(SUITS)[np.max(suit_combos, axis=1)[0]].title(), cards=num_combos[0, :5]
        )

    @staticmethod
    def straight_check(num_combos: np.ndarray, rank_arr: np.ndarray[Any, np.dtype[Any]], straight_arr: bool) -> None:
        """Checks if the combination of cards is a straight."""
        ace_low_straight = np.max(num_combos[:, 4]) == 14 and np.min(num_combos[:, 0]) == 2
        straight_label = "Low" if ace_low_straight else "High"

        rank_arr[(rank_arr == 0) & straight_arr] = RankingItem(
            rank=4, name=f"{UNAMED[np.max(num_combos[:, 4])]} {straight_label}", cards=num_combos[0, :5]
        )

        # Rearrange order of 2345A to A2345
        reorder_idx = (rank_arr == 4) & (num_combos[:, 0] == 2) & (num_combos[:, 4] == 14)
        num_combos[reorder_idx, :] = np.concatenate([num_combos[reorder_idx, 4:], num_combos[reorder_idx, :4]], axis=1)

    @staticmethod
    def three_of_a_kind_check(num_combos: np.ndarray, rank_arr: np.ndarray[Any, np.dtype[Any]]) -> None:
        """Checks if the combination of cards is a three of a kind."""
        small = np.all((num_combos[:, 0:3] == num_combos[:, :1]), axis=1)  # 22235
        middle = np.all((num_combos[:, 1:4] == num_combos[:, 1:2]), axis=1)  # 23335
        large = np.all((num_combos[:, 2:] == num_combos[:, 2:3]), axis=1)  # 36AAA

        condition = small | middle | large
        three_of_a_kind = item_by_count(num_combos[condition], 3)
        rank_arr[(rank_arr == 0) & condition] = RankingItem(
            rank=3, name=f"{UNAMED[three_of_a_kind[0]]}s", cards=num_combos[0, :5]
        )

        reorder_small = (rank_arr == 3) & small
        reorder_middle = (rank_arr == 3) & large

        num_combos[reorder_small, :] = np.concatenate([num_combos[reorder_small, 3:], num_combos[reorder_small, :3]], axis=1)
        num_combos[reorder_middle, :] = np.concatenate(
            [num_combos[reorder_middle, :1], num_combos[reorder_middle, 4:], num_combos[reorder_middle, 1:4]], axis=1
        )

    @staticmethod
    def two_pairs_check(num_combos: np.ndarray, rank_arr: np.ndarray[Any, np.dtype[Any]]) -> None:
        """Checks if the combination of cards is a two pairs."""
        small = np.all(
            (num_combos[:, 0:2] == num_combos[:, :1]) & (num_combos[:, 2:4] == num_combos[:, 2:3]), axis=1
        )  # 2233A

        middle = np.all(
            (num_combos[:, 0:2] == num_combos[:, :1]) & (num_combos[:, 3:] == num_combos[:, 4:]), axis=1
        )  # 223AA

        large = np.all(
            (num_combos[:, 1:3] == num_combos[:, 1:2]) & (num_combos[:, 3:] == num_combos[:, 4:]), axis=1
        )  # 233AA

        condition = small | middle | large
        two_pairs = item_by_count(num_combos[condition], 2)
        if len(two_pairs) == 2:
            rank_arr[(rank_arr == 0) & (small | middle | large)] = RankingItem(
                rank=2, name=f"{UNAMED[two_pairs[0]]}s and {UNAMED[two_pairs[1]]}s", cards=num_combos[0, :5]
            )

        reorder_small = (rank_arr == 2) & small
        reorder_middle = (rank_arr == 2) & large

        num_combos[reorder_small, :] = np.concatenate([num_combos[reorder_small, 4:], num_combos[reorder_small, :4]], axis=1)
        num_combos[reorder_middle, :] = np.concatenate(
            [num_combos[reorder_middle, 2:3], num_combos[reorder_middle, 0:2], num_combos[reorder_middle, 3:]], axis=1
        )

    @staticmethod
    def one_pair_check(num_combos: np.ndarray, rank_arr: np.ndarray[Any, np.dtype[Any]]) -> None:
        """Checks if the combination of cards is a one pair."""
        small = np.all((num_combos[:, 0:2] == num_combos[:, :1]), axis=1)  # 22345
        mid_small = np.all((num_combos[:, 1:3] == num_combos[:, 1:2]), axis=1)  # 23345
        mid_large = np.all((num_combos[:, 2:4] == num_combos[:, 2:3]), axis=1)  # 23445
        large = np.all((num_combos[:, 3:] == num_combos[:, 3:4]), axis=1)  # 23455

        condition = small | mid_small | mid_large | large
        one_pair = item_by_count(num_combos[condition], 2)
        rank_arr[(rank_arr == 0) & condition] = RankingItem(rank=1, name=f"{UNAMED[one_pair[0]]}s", cards=num_combos[0, :5])

        reorder_small = (rank_arr == 1) & small
        reorder_mid_small = (rank_arr == 1) & mid_small
        reorder_mid_large = (rank_arr == 1) & mid_large

        num_combos[reorder_small, :] = np.concatenate([num_combos[reorder_small, 2:], num_combos[reorder_small, :2]], axis=1)
        num_combos[reorder_mid_small, :] = np.concatenate(
            [num_combos[reorder_mid_small, :1], num_combos[reorder_mid_small, 3:], num_combos[reorder_mid_small, 1:3]],
            axis=1,
        )
        num_combos[reorder_mid_large, :] = np.concatenate(
            [num_combos[reorder_mid_large, :2], num_combos[reorder_mid_large, 4:], num_combos[reorder_mid_large, 2:4]],
            axis=1,
        )

    @staticmethod
    def high_card_check(num_combos: np.ndarray, rank_arr: np.ndarray[Any, np.dtype[Any]]) -> None:
        """Checks if the combination of cards is a high card."""
        rank_arr[(rank_arr == 0)] = RankingItem(
            rank=0, name=f"{UNAMED[np.max(num_combos[:, 4])]} High", cards=num_combos[0, :5]
        )

        # Rearrange order of 2345A to A2345
        reorder_idx = (rank_arr == 0) & (num_combos[:, 0] == 2) & (num_combos[:, 4] == 14)
        num_combos[reorder_idx, :] = np.concatenate([num_combos[reorder_idx, 4:], num_combos[reorder_idx, :4]], axis=1)


class Player:
    """Represents a player in a poker game.

    ``member`` is an opaque identity token supplied by the cog (a
    ``discord.Member`` at runtime); the engine only uses it for equality/identity.
    """

    def __init__(self, member: discord.Member, stack: int) -> None:
        self.member: discord.Member = member
        self.hand: Hand = Hand()

        self.stack: int = stack
        self.bet: int = 0

        self.wait_for_allin_call: bool = False
        self.folded: bool = False
        self.all_in: bool = False
        self.checked: bool = False
        # Tracks which street the player folded on (0=pre-flop, 1=flop, 2=turn, 3=river, None=still in)
        self.folded_on_street: int | None = None
        # Sit out: player is away and will auto-fold until they return
        self.sitting_out: bool = False
        # Straddle: voluntary pre-flop blind (2x BB) from UTG position
        self.has_straddled: bool = False
        # Muck: whether this player chose to muck (not show) their losing hand
        self.mucked: bool = False

    def __repr__(self) -> str:
        return (
            f"Player(member={self.member} hand={self.hand} stack={self.stack} "
            f"bet={self.bet} folded={self.folded} all_in={self.all_in})"
        )

    def reset(self) -> None:
        """Resets the player"""
        self.bet = 0
        self.wait_for_allin_call = False
        self.folded = False
        self.all_in = False
        self.checked = False
        self.folded_on_street = None
        self.hand = Hand()
        self.has_straddled = False
        self.mucked = False
        # Note: sitting_out persists across hands until player returns


class Pot:
    """Represents a pot in a poker game

    The amount displays the total amount of chips in the pot.
    The players list displays the players that have contributed to the pot and are able to win this pot,
    this is useful for all-in situations where a player can only win the pot they have contributed to.

    A pot can be split into multiple side pots if a player goes all-in and cannot contribute to the current pot.
    """

    def __init__(self, amount: int, players: list[Player] | None = None) -> None:
        self.amount: int = amount
        self.players: list[Player] = players or []

    def __repr__(self) -> str:
        return f"Pot(amount={self.amount}, players={len(self.players)})"

    def __iadd__(self, other: int) -> Pot:
        if not isinstance(other, int):
            raise TypeError(f"unsupported operand type(s) for +: {type(self)} and {type(other)}")

        self.amount += other
        return self

    def __isub__(self, other: int) -> Pot:
        if not isinstance(other, int):
            raise TypeError(f"unsupported operand type(s) for -: {type(self)} and {type(other)}")

        self.amount -= other
        return self

    def __int__(self) -> int:
        return self.amount

    def __len__(self) -> int:
        return self.amount

    def __str__(self) -> str:
        return f"{fnumb(self.amount)}"


class TexasHoldem:
    """The pure Texas Hold'em game logic and state machine.

    Holds the deck, players, pots, blinds and game state, and exposes the betting
    actions, dealing, state transitions and the odds simulation. It performs no IO
    and has no knowledge of Discord; the bridge (``app.cogs.games._poker``) owns
    the message, view, autoplay timer and chip refunds.

    Parameters
    ----------
    first_buy_in : int
        The first buy-in for the game.
    decks : int
        The number of decks to use.
    max_players : int
        The maximum number of players allowed in the game.
    """

    def __init__(self, *, first_buy_in: int, decks: int = 1, max_players: int = 4) -> None:
        self.first_buy_in: int = first_buy_in
        self.deck: Deck = Deck(game="poker", decks=decks)

        self.state: TableState = TableState.STOPPED

        self.community_arr: np.ndarray = np.zeros(shape=(0, 2), dtype=int)
        self.players: list[Player] = []

        # Identity of the hosting member; set by the bridge. Used only for host checks.
        self.host: Any = None
        self.max_players: int = max_players
        self.player_index: int = 0

        # Dealer button and blinds
        # dealer_index: position of the dealer button (rotates each hand)
        # blind_index: (small_blind_pos, big_blind_pos) - derived from dealer
        self.dealer_index: int | None = None
        self.blind_index: tuple[int, int] | None = None
        self.big_blind: int = max(int(self.first_buy_in * 0.01), 2)
        self.small_blind: int = self.big_blind // 2

        # Blind escalation (tournament mode)
        # escalation_enabled: whether blinds increase over time
        # escalation_hands: number of hands between blind increases
        # escalation_multiplier: how much blinds increase each level (e.g., 1.5 = 50% increase)
        # hands_at_level: hands played at current blind level
        self.escalation_enabled: bool = False
        self.escalation_hands: int = 10  # Increase blinds every 10 hands
        self.escalation_multiplier: float = 1.5  # 50% increase
        self.hands_at_level: int = 0
        self.blind_level: int = 1  # Current blind level (for display)

        # Straddle tracking
        self.straddle_enabled: bool = True  # Allow straddles by default
        self.straddle_index: int | None = None  # Index of player who straddled (UTG)
        self.straddle_amount: int = 0  # Current straddle amount

        # Run it twice tracking
        self.run_it_twice_enabled: bool = True  # Allow run it twice by default
        self.run_it_twice_offered: bool = False  # Whether offer is pending
        self.run_it_twice_accepted: bool = False  # Whether both players agreed
        self.run_it_twice_boards: list[list[tuple[int, int]]] = []  # Multiple board runouts

        # Hand history
        self.hand_history: list[HandHistoryEntry] = []
        self.hand_number: int = 0
        self._current_hand_actions: list[str] = []

        # Showdown order tracking (last aggressor shows first)
        self.last_aggressor_index: int | None = None

        # Pots
        self.pot: Pot = Pot(amount=0)
        self.side_pots: list[Pot] = []

        # Game Tracking
        self.ranks: list[tuple[Player, HandResult]] = []
        self.tie = False
        self.winners: list[tuple[list[Player], Pot]] = []
        self.eliminated_players: list[Player] = []

        # Analysis data: list of (live_analysis, full_analysis, hand_strength) per street
        # live_analysis: odds among active players only
        # full_analysis: odds including folded players (hypothetical)
        # hand_strength: hand type distribution per player
        self.analysis: list[tuple[dict[str, float], dict[str, float], dict[int, dict[str, float]]]] = []
        self.odds_mode: OddsMode = OddsMode.LIVE  # Default to live odds

    def __repr__(self) -> str:
        return f"TexasHoldem(state={self.state} host={self.host} players={len(self.players)} max_players={self.max_players})"

    @property
    def min_buy_in(self) -> int:
        """int: Returns the minimum buy-in for the game."""
        return self.first_buy_in // 2

    @property
    def max_buy_in(self) -> int:
        """int: Returns the maximum buy-in for the game."""
        return self.first_buy_in * 10

    @property
    def playing_players(self) -> list[Player]:
        """list[:class:`Player`]: Returns a list of players that are still playing"""
        return [player for player in self.players if not player.folded and player not in self.eliminated_players]

    @property
    def current_street(self) -> int:
        """Returns the current street (0=pre-flop, 1=flop, 2=turn, 3=river)."""
        community_len = len(self.community_arr)
        if community_len == 0:
            return 0  # Pre-flop
        elif community_len == 3:
            return 1  # Flop
        elif community_len == 4:
            return 2  # Turn
        else:
            return 3  # River

    @property
    def showdown_order(self) -> list[Player]:
        """Returns players in showdown order (last aggressor first, then clockwise)."""
        active = [p for p in self.playing_players if not p.folded]
        if not active:
            return []

        # If there was an aggressor, they show first
        if self.last_aggressor_index is not None:
            aggressor = self.players[self.last_aggressor_index]
            if aggressor in active:
                # Start from aggressor, then clockwise
                start_idx = active.index(aggressor)
                return active[start_idx:] + active[:start_idx]

        # Otherwise, start from first active player after dealer
        if self.dealer_index is not None:
            for i in range(len(self.players)):
                idx = (self.dealer_index + 1 + i) % len(self.players)
                if self.players[idx] in active:
                    start_idx = active.index(self.players[idx])
                    return active[start_idx:] + active[:start_idx]

        return active

    @property
    def current_player(self) -> Player:
        """:class:`Player`: The player whose turn it currently is."""
        return self.players[self.player_index]

    def AllIn(self) -> None:
        """Sets the current player All In by betting their stack.

        Implements a side-pot logic that creates a new pot if a player goes all-in and cannot contribute to the current pot.
        """
        player = self.players[self.player_index]
        player.all_in = True
        player.wait_for_allin_call = True

        amount: int = player.stack
        self._log_action(f"{self._get_player_name(player)} goes ALL-IN ({amount} chips)")
        self.Raise(amount)

    def Call(self) -> None:
        """Calls the bet for the current player."""
        call_amount = max([player.bet for player in self.players]) - self.players[self.player_index].bet

        player = self.players[self.player_index]
        contribution = min(player.stack, call_amount)

        if call_amount > contribution:
            # This should never happen due to the button being
            # locked if the player can't afford the call, but we'll never know
            # if discord is going to be bugcord or not
            self.AllIn()
        else:
            self._log_action(f"{self._get_player_name(player)} calls {contribution}")
            self.Raise(contribution)

    def Raise(self, amount: int) -> None:
        """Raises the bet for the current player by adding `amount` chips to their current bet."""
        player = self.players[self.player_index]

        # Calculate the amount the player can contribute (capped by their stack)
        contribution = min(player.stack, amount)

        if self.side_pots:
            # if we have at least one side pot, we will update this one and not the initial pot
            self.side_pots[-1] += contribution
        else:
            self.pot += contribution

        # Deduct the contribution from the player's stack
        player.stack -= contribution
        player.bet += contribution

        # Track last aggressor for showdown order
        self.last_aggressor_index = self.player_index

        self.Check()

    def Check(self) -> None:
        """Checks the street for the current player."""
        player = self.players[self.player_index]
        player.checked = True
        self._log_action(f"{self._get_player_name(player)} checks")

    def Fold(self) -> None:
        """Folds the current player."""
        player = self.players[self.player_index]
        player.folded = True
        # Track which street the player folded on
        player.folded_on_street = self.current_street
        self._log_action(f"{self._get_player_name(player)} folds")

    def reset(self) -> None:
        """Resets the table to its initial state"""
        self.community_arr = np.zeros(shape=(0, 2), dtype=int)
        self.ranks = []
        self.winners = []
        self.side_pots = []
        self.eliminated_players = []
        self.pot = Pot(amount=0)
        self.tie = False
        self.analysis = []
        self.straddle_index = None
        self.straddle_amount = 0
        self.run_it_twice_offered = False
        self.run_it_twice_accepted = False
        self.run_it_twice_boards = []
        self._current_hand_actions = []
        self.last_aggressor_index = None
        # Note: odds_mode, straddle_enabled, run_it_twice_enabled, hand_history persist across rounds

    def start(self) -> None:
        """Starts the game by dealing the cards and setting the sb and bb.

        This function is called when the game starts and deals the cards to the players and sets the sb and bb.
        The caller (bridge) is responsible for (re)starting the autoplay timer afterwards.
        """
        self.state = TableState.RUNNING
        self.hand_number += 1
        self._current_hand_actions = []

        # Check for blind escalation before dealing
        self._check_blind_escalation()

        self.deal()

        self.pot.players = self.players

        # Initialize dealer button on first hand
        if self.dealer_index is None:
            self.dealer_index = 0

        # Derive blind positions from dealer
        # Heads-up (2 players): dealer is SB and acts first pre-flop
        # 3+ players: SB is left of dealer, BB is left of SB
        num_players = len(self.players)
        if num_players == 2:
            # Heads-up special rules: dealer posts SB
            sb_index = self.dealer_index
            bb_index = (self.dealer_index + 1) % num_players
        else:
            sb_index = (self.dealer_index + 1) % num_players
            bb_index = (self.dealer_index + 2) % num_players

        self.blind_index = (sb_index, bb_index)

        # Set the player index to the player after BB (UTG)
        # In heads-up, dealer/SB acts first pre-flop
        if num_players == 2:
            self.player_index = self.dealer_index  # Dealer/SB acts first pre-flop
        else:
            self.player_index = (bb_index + 1) % num_players

        # Set sb and bb and take their bets
        for index in self.blind_index:
            player = self.players[index]
            player.bet = self.small_blind if index == self.blind_index[0] else self.big_blind
            player.stack -= player.bet

        self.pot.amount = self.small_blind + self.big_blind

        # Auto-fold players who are sitting out
        for player in self.players:
            if player.sitting_out:
                player.folded = True
                player.folded_on_street = 0  # Folded pre-flop

        if self.odds_mode != OddsMode.NONE:
            self.analysis.append(cast("tuple[dict[str, float], dict[str, float], dict[int, dict[str, float]]]", self.simulate(final_hand=True)))

    def end(self) -> None:
        """Ends the game by calculating the winner(s).

        This function is called when the game is over and calculates the winner(s) of the game.
        """
        self.state = TableState.FINISHED

        # Special case: only one player remains (everyone else folded)
        # Award all pots to the remaining player without revealing cards
        if len(self.playing_players) == 1:
            winner = self.playing_players[0]
            total_won = 0
            for pot in [self.pot, *self.side_pots]:
                winner.stack += pot.amount
                total_won += pot.amount
                self.winners.append(([winner], pot))
            self._log_action(f"{self._get_player_name(winner)} wins {total_won} (others folded)")
            self._save_hand_history()
            return

        # Handle run it twice if accepted
        if self.run_it_twice_accepted and self.run_it_twice_boards:
            self._evaluate_run_it_twice()
            self._save_hand_history()
            return

        # Normal showdown: evaluate all remaining players' hands in showdown order
        for player in self.showdown_order:
            result = player.hand.evaluate(self.community_arr)
            self.ranks.append((player, result))

        self.ranks.sort(key=lambda x: x[1].value, reverse=True)

        # Calculate the winner(s) for each pot
        for pot in [self.pot, *self.side_pots]:
            # Only consider players who contributed to this pot
            pot_player_ranks = [(p, r) for p, r in self.ranks if p in pot.players]

            if not pot_player_ranks:
                continue

            # Find the best hand value among players in this pot
            best_value = max(r.value for _, r in pot_player_ranks)

            # Find all players with the best hand in this pot
            pot_winners = [p for p, r in pot_player_ranks if r.value == best_value]

            # Distribute the pot among winners
            for winner in pot_winners:
                winner.stack += pot.amount // len(pot_winners)

            # Check if this pot resulted in a tie
            if len(pot_winners) > 1:
                self.tie = True

            self.winners.append((pot_winners, pot))

        # Save hand to history after normal showdown
        self._save_hand_history()

    def add_player(self, member: discord.Member, stack: int) -> None:
        """Adds a player to the table.

        Parameters
        ----------
        member : Any
            The member to add to the table (a ``discord.Member`` at runtime).
        stack : int
            The stack of the player
        """
        self.players.append(Player(member=member, stack=stack))

    def rebuy(self, member: discord.Member, amount: int) -> bool:
        """Adds chips to a player's stack (rebuy/add-on).

        Can only be done between hands (not during active play).

        Parameters
        ----------
        member : discord.Member
            The member to rebuy for.
        amount : int
            The amount of chips to add.

        Returns
        -------
        bool
            True if successful, False if player not found or game is running.
        """
        if self.state == TableState.RUNNING:
            return False

        player = next((p for p in self.players if p.member == member), None)
        if player is None:
            return False

        # Enforce max buy-in limit
        if player.stack + amount > self.max_buy_in:
            return False

        player.stack += amount
        return True

    def sit_out(self, member: discord.Member) -> bool:
        """Marks a player as sitting out (will auto-fold each hand).

        Parameters
        ----------
        member : discord.Member
            The member to sit out.

        Returns
        -------
        bool
            True if successful.
        """
        player = next((p for p in self.players if p.member == member), None)
        if player is None:
            return False

        player.sitting_out = True
        return True

    def sit_in(self, member: discord.Member) -> bool:
        """Marks a player as back in (will participate in next hand).

        Parameters
        ----------
        member : discord.Member
            The member returning to play.

        Returns
        -------
        bool
            True if successful.
        """
        player = next((p for p in self.players if p.member == member), None)
        if player is None:
            return False

        player.sitting_out = False
        return True

    def set_escalation(self, enabled: bool, hands: int = 10, multiplier: float = 1.5) -> None:
        """Configures blind escalation (tournament mode).

        Parameters
        ----------
        enabled : bool
            Whether to enable blind escalation.
        hands : int
            Number of hands between blind increases.
        multiplier : float
            How much blinds increase each level (e.g., 1.5 = 50% increase).
        """
        self.escalation_enabled = enabled
        self.escalation_hands = max(1, hands)
        self.escalation_multiplier = max(1.1, min(3.0, multiplier))  # Clamp between 1.1x and 3x

    def _check_blind_escalation(self) -> bool:
        """Checks and applies blind escalation if needed.

        Called at the start of each hand. Returns True if blinds increased.
        """
        if not self.escalation_enabled:
            return False

        self.hands_at_level += 1

        if self.hands_at_level >= self.escalation_hands:
            self.hands_at_level = 0
            self.blind_level += 1
            self.big_blind = int(self.big_blind * self.escalation_multiplier)
            self.small_blind = self.big_blind // 2
            return True

        return False

    def can_straddle(self, member: discord.Member) -> bool:
        """Checks if a player can post a straddle.

        Straddle is only allowed for the UTG player (left of BB) before they act,
        only in 3+ player games, and only if straddles are enabled.
        """
        if not self.straddle_enabled:
            return False
        if self.state != TableState.RUNNING:
            return False
        if len(self.players) < 3:  # No straddle in heads-up
            return False
        if self.straddle_index is not None:  # Already straddled this hand
            return False

        player = next((p for p in self.players if p.member == member), None)
        if player is None:
            return False

        # UTG is the player after BB
        assert self.blind_index is not None
        utg_index = (self.blind_index[1] + 1) % len(self.players)

        # Must be UTG and not have acted yet
        player_index = self.players.index(player)
        return player_index == utg_index and not player.checked and player.bet == 0

    def post_straddle(self, member: discord.Member) -> bool:
        """Posts a straddle (voluntary blind of 2x BB from UTG position).

        Parameters
        ----------
        member : discord.Member
            The member posting the straddle.

        Returns
        -------
        bool
            True if successful.
        """
        if not self.can_straddle(member):
            return False

        player = next((p for p in self.players if p.member == member), None)
        assert player is not None

        straddle_amount = self.big_blind * 2
        if player.stack < straddle_amount:
            return False

        player_index = self.players.index(player)
        self.straddle_index = player_index
        self.straddle_amount = straddle_amount

        player.bet = straddle_amount
        player.stack -= straddle_amount
        player.has_straddled = True
        self.pot += straddle_amount

        # Action moves to player after straddler
        self.player_index = (player_index + 1) % len(self.players)

        return True

    def can_run_it_twice(self) -> bool:
        """Checks if run it twice is available.

        Run it twice requires:
        - Feature enabled
        - Exactly 2 players remaining (all-in situation)
        - At least one all-in player
        - Community cards not yet fully dealt
        """
        if not self.run_it_twice_enabled:
            return False
        if self.run_it_twice_offered:
            return False  # Already offered/accepted
        if len(self.playing_players) != 2:
            return False
        if not any(p.all_in for p in self.playing_players):
            return False
        if len(self.community_arr) >= 5:
            return False  # Board is complete
        return True

    def offer_run_it_twice(self) -> bool:
        """Offers run it twice to players.

        Returns True if offer is now pending.
        """
        if not self.can_run_it_twice():
            return False

        self.run_it_twice_offered = True
        return True

    def accept_run_it_twice(self, times: int = 2) -> bool:
        """Accepts running it multiple times.

        Parameters
        ----------
        times : int
            Number of times to run out the board (2 or 3).

        Returns
        -------
        bool
            True if accepted and boards generated.
        """
        if not self.run_it_twice_offered:
            return False

        times = min(3, max(2, times))  # Clamp to 2-3

        # Save current community cards as base
        base_community = list(map(tuple, self.community_arr))
        cards_needed = 5 - len(base_community)

        if cards_needed <= 0:
            return False

        self.run_it_twice_boards = []

        for _ in range(times):
            # Draw remaining cards for this runout
            board = list(base_community)
            for _ in range(cards_needed):
                card = self.deck.draw()
                board.append((int(card[0, 0]), int(card[0, 1])))
            self.run_it_twice_boards.append(board)

        self.run_it_twice_accepted = True
        return True

    def decline_run_it_twice(self) -> None:
        """Declines the run it twice offer."""
        self.run_it_twice_offered = False

    def can_muck(self, member: discord.Member) -> bool:
        """Checks if a player can muck their hand.

        Players can muck only if:
        - The game is finished
        - They didn't win (no need to show)
        - They haven't already mucked
        """
        if self.state != TableState.FINISHED:
            return False

        player = next((p for p in self.players if p.member == member), None)
        if player is None or player.mucked:
            return False

        # Check if player is a winner - winners must show
        for winners, _ in self.winners:
            if player in winners:
                return False

        return True

    def muck_hand(self, member: discord.Member) -> bool:
        """Mucks a player's hand (hides it from view).

        Parameters
        ----------
        member : discord.Member
            The member wanting to muck.

        Returns
        -------
        bool
            True if successfully mucked.
        """
        if not self.can_muck(member):
            return False

        player = next((p for p in self.players if p.member == member), None)
        if player:
            player.mucked = True
            return True
        return False

    def _log_action(self, action: str) -> None:
        """Logs an action for hand history."""
        self._current_hand_actions.append(action)

    def _get_player_name(self, player: Player) -> str:
        """Gets a player's display name safely (works with both real Members and test strings)."""
        if hasattr(player.member, 'display_name'):
            return player.member.display_name
        return str(player.member)

    def _save_hand_history(self) -> None:
        """Saves the completed hand to history."""
        if not self.ranks and not self.winners:
            return  # No showdown data

        hole_cards: dict[str, list[tuple[int, int]]] = {}
        for player in self.players:
            hole_cards[self._get_player_name(player)] = [
                (int(c[0]), int(c[1])) for c in player.hand.card_arr
            ]

        community = [(int(c[0]), int(c[1])) for c in self.community_arr]

        winner_names = []
        winning_hand = None
        for winners, _ in self.winners:
            for w in winners:
                name = self._get_player_name(w)
                if name not in winner_names:
                    winner_names.append(name)
        if self.ranks:
            winning_hand = self.ranks[0][1].name

        entry = HandHistoryEntry(
            hand_number=self.hand_number,
            timestamp=datetime.now(timezone.utc).isoformat(),
            players=[self._get_player_name(p) for p in self.players],
            hole_cards=hole_cards,
            community_cards=community,
            actions=list(self._current_hand_actions),
            pot_total=self.pot.amount + sum(sp.amount for sp in self.side_pots),
            winners=winner_names,
            winning_hand=winning_hand,
            blinds=(self.small_blind, self.big_blind),
        )

        self.hand_history.append(entry)
        # Keep only last 20 hands
        if len(self.hand_history) > 20:
            self.hand_history = self.hand_history[-20:]

    def _evaluate_run_it_twice(self) -> None:
        """Evaluates multiple boards and splits the pot accordingly."""
        num_boards = len(self.run_it_twice_boards)
        if num_boards == 0:
            return

        # Track wins per player across all boards
        board_winners: list[list[Player]] = []

        for board in self.run_it_twice_boards:
            # Convert board to numpy array for evaluation
            board_arr = np.array(board)

            # Evaluate each player's hand against this board
            board_ranks: list[tuple[Player, HandResult]] = []
            for player in self.playing_players:
                result = player.hand.evaluate(board_arr)
                board_ranks.append((player, result))

            board_ranks.sort(key=lambda x: x[1].value, reverse=True)

            # Find winner(s) for this board
            best_value = board_ranks[0][1].value
            winners = [p for p, r in board_ranks if r.value == best_value]
            board_winners.append(winners)

            # Store ranks for the first board (for display)
            if not self.ranks:
                self.ranks = board_ranks

        # Distribute pots based on board wins
        for pot in [self.pot, *self.side_pots]:
            pot_amount_per_board = pot.amount // num_boards

            for winners in board_winners:
                # Filter to players in this pot
                pot_winners = [w for w in winners if w in pot.players]
                if not pot_winners:
                    continue

                # Split this board's share among winners
                share = pot_amount_per_board // len(pot_winners)
                for winner in pot_winners:
                    winner.stack += share

                if len(pot_winners) > 1:
                    self.tie = True

            # Record overall winners (unique winners across all boards)
            all_winners = list({w for winners in board_winners for w in winners if w in pot.players})
            self.winners.append((all_winners, pot))

    def remove_player(self, member: discord.Member) -> int:
        """Removes a player from the table and returns their leftover stack.

        The caller (bridge) is responsible for refunding the returned amount.

        Parameters
        ----------
        member : Any
            The member to remove from the table.

        Returns
        -------
        int
            The stack the removed player had left over.
        """
        player = next((p for p in self.players if p.member == member), None)
        assert player is not None
        self.players.remove(player)
        return player.stack

    def prepare_next_game(self) -> list[Player]:
        """Prepares the next round.

        Players who ran out of chips are removed and returned so the caller can
        announce them; everyone else is reset.

        Returns
        -------
        list[:class:`Player`]
            The players that were removed because they ran out of chips.
        """
        self.state = TableState.PREPARED

        # Reset players and remove players with no chips
        removed: list[Player] = []
        for player in list(self.players):
            if player.stack <= 0:
                self.players.remove(player)
                removed.append(player)
            else:
                player.reset()

        if len(self.players) < 2:
            self.state = TableState.STOPPED

        self.reset()

        # Rotate dealer button (blinds will be derived in start())
        if self.dealer_index is not None and len(self.players) > 0:
            self.dealer_index = (self.dealer_index + 1) % len(self.players)
        # Clear blind_index so start() recalculates from new dealer position
        self.blind_index = None
        return removed

    def __fill_left_community_cards(self) -> None:
        """Fills the left community cards"""
        while len(self.community_arr) < 5:
            if len(self.community_arr) in (3, 4) and self.odds_mode != OddsMode.NONE:
                # add analysis data for the flop and turn
                self.analysis.append(
                    cast("tuple[dict[str, float], dict[str, float], dict[int, dict[str, float]]]", self.simulate(final_hand=True))
                )

            self.community_arr = np.concatenate([self.community_arr, self.deck.draw()], axis=0)

    def __update_pots(self) -> None:
        """Remove folded players from all pots so they cannot win them."""
        for pot in [self.pot, *self.side_pots]:
            pot.players = [player for player in pot.players if not player.folded]

    def switch_player(self, by_raise: bool = False) -> None:
        """Switches to the next player.

        The caller (bridge) is responsible for (re)starting the autoplay timer for
        the new current player when the game is still running.
        """
        self.__update_pots()

        self.player_index = (self.player_index + 1) % len(self.players)

        current_player = self.players[self.player_index]
        if current_player.folded or current_player.all_in:
            self.switch_player(by_raise=by_raise)
            return

        if self.game_is_over:
            if len(self.playing_players) != 1:
                self.__fill_left_community_cards()

            self.end()
            return

        if by_raise:
            # A raise reopens betting - all players except the raiser and all-in players must act again
            for player in self.playing_players:
                if not player.all_in and player != self.players[self.player_index]:
                    player.checked = False
        else:
            # Check if the betting round is complete (all active players have acted)
            active_players = [p for p in self.playing_players if not p.all_in]
            if all(player.checked for player in active_players) if active_players else True:
                # Next street - reset all check flags and bets for the new round
                self._start_new_street()

    def _start_new_street(self) -> None:
        """Starts a new betting street: resets flags, handles all-in side pots, deals community cards."""
        assert self.blind_index is not None
        assert self.dealer_index is not None

        # Reset check flags for the new street
        for player in self.playing_players:
            player.checked = False
            player.bet = 0  # Reset bets for the new street

        # Post-flop action starts left of dealer (SB in 3+ players, BB in heads-up)
        # In heads-up, BB acts first post-flop (dealer/SB acts last)
        num_players = len(self.players)
        if num_players == 2:
            # Heads-up: BB (non-dealer) acts first post-flop
            start_index = (self.dealer_index + 1) % num_players
        else:
            # 3+ players: SB acts first (left of dealer)
            start_index = (self.dealer_index + 1) % num_players

        self.player_index = start_index
        # Skip to first non-folded, non-all-in player
        while self.players[self.player_index].folded or self.players[self.player_index].all_in:
            self.player_index = (self.player_index + 1) % len(self.players)
            if self.player_index == start_index:
                break  # Avoid infinite loop

        # Handle all-in player side pot creation
        if all_in_player := next((player for player in self.playing_players if player.wait_for_allin_call), None):
            all_in_player.wait_for_allin_call = False
            remaining_players = [p for p in self.playing_players if not p.all_in]
            if remaining_players:
                self.side_pots.append(Pot(amount=0, players=remaining_players))

        # Deal community cards
        for _ in range(self.to_draw):
            self.community_arr = np.concatenate([self.community_arr, self.deck.draw()], axis=0)

        # Calculate odds if game is not over and odds mode is enabled
        if len(self.community_arr) != 5 and self.odds_mode != OddsMode.NONE:
            self.analysis.append(
                cast("tuple[dict[str, float], dict[str, float], dict[int, dict[str, float]]]", self.simulate(final_hand=True))
            )

    def autoplay_turn(self, player: Player) -> bool:
        """Applies the automatic action for a player who took too long.

        The player checks if they can, otherwise folds, and the turn advances.
        Returns whether an action was taken (``False`` if the player was already
        all-in or folded).

        Parameters
        ----------
        player : Player
            The player to auto-play for.
        """
        if player.all_in or player.folded:
            return False

        player.checked = True

        max_bet = max([p.bet for p in self.playing_players])
        if player.bet != max_bet:
            player.folded = True

        self.switch_player()
        return True

    @property
    def game_is_over(self) -> bool:
        """Returns whether the game is over (last round, all 5 community cards dealt, and every player has checked)"""
        return (
            self.state == TableState.FINISHED
            or (len(self.community_arr) == 5 and all(player.checked or player.all_in for player in self.playing_players))
            or (len(self.playing_players) == 1)
            or (len(self.playing_players) == 2 and any(player.all_in for player in self.playing_players))
        )

    @property
    def to_draw(self) -> int:
        """Returns how much community cards need to be dealt"""
        TO_DRAW_MAP = {0: 3, 3: 1, 4: 1, 5: 0}
        return TO_DRAW_MAP[len(self.community_arr)]

    def deal(self) -> None:
        """Deals the cards to the players"""
        for _ in range(2):
            for player in self.players:
                player.hand.add(self.deck.draw())

    # Simulation

    def _simulation_preparation(self, num_scenarios: int | Literal["all"]) -> tuple[np.ndarray | None, np.ndarray]:
        """Prepares the simulation by generating the community cards and undrawn cards

        Parameters
        ----------
        num_scenarios: int
            The number of scenarios to simulate, if 'all' then all scenarios will be simulated.

        Returns
        -------
        tuple[np.ndarray, np.ndarray]
            The community cards and undrawn cards.
        """
        total_idx = comb_index(len(self.deck.cards), 5 - len(self.community_arr))
        undrawn_combos = self.deck.cards[total_idx]
        if num_scenarios != "all":
            assert isinstance(num_scenarios, int), "'num_scenarios' must be an integer"
            if len(undrawn_combos) > num_scenarios:
                undrawn_combos = undrawn_combos[np.array(random.sample(range(len(undrawn_combos)), num_scenarios))]

        if len(self.community_arr) > 0:
            community_cards = np.repeat([self.community_arr], len(undrawn_combos), axis=0)
        else:
            community_cards = None
        return community_cards, undrawn_combos

    def _hand_strength_analysis(self, res_arr: np.ndarray) -> dict:  # type: ignore[type-var]
        """Returns the hand strength of each player

        Parameters
        ----------
        res_arr: np.ndarray
            The result array from the simulation.

        Returns
        -------
        dict
            The hand strength of each player.
        """
        final_hand_dict = {}
        for player in range(len(self.players)):
            hand_type, hand_freq = np.unique((res_arr // 16**5)[:, player], return_counts=True)  # type: ignore[operator]
            final_hand_dict[player + 1] = dict(
                zip(np.vectorize(NAMED_HAND.get)(hand_type), np.round(hand_freq / hand_freq.sum() * 100, 2).astype(float))
            )
        return final_hand_dict

    def _simulation_analysis(
        self,
        odds_type: Literal["win_any", "tie_win", "precise"],
        res_arr: np.ndarray[Any, np.dtype[Any]],
        active_indices: list[int] | None = None,
    ) -> dict:
        """Analyze simulation results to compute odds.

        Parameters
        ----------
        odds_type : Literal["win_any", "tie_win", "precise"]
            The type of odds calculation.
        res_arr : np.ndarray
            The simulation result array (scenarios x players).
        active_indices : list[int] | None
            If provided, only consider these player indices for odds calculation.
            Players not in this list will have 0% odds (as if they folded).
        """
        if active_indices is None:
            active_indices = list(range(len(self.players)))

        # For live odds, we only consider active players when determining the "best hand"
        # Create a masked result array where folded players have minimum values
        if len(active_indices) < len(self.players):
            masked_res = res_arr.copy()
            for i in range(len(self.players)):
                if i not in active_indices:
                    masked_res[:, i] = -1  # Set to -1 so they can never "win"
            outcome_arr = masked_res == np.expand_dims(np.max(masked_res, axis=1), axis=1)
        else:
            outcome_arr = res_arr == np.expand_dims(np.max(res_arr, axis=1), axis=1)

        num_outcomes = len(outcome_arr)  # type: ignore # lying
        outcome_dict: dict[str, float] = {}

        # Any Tied Win counts as a Win
        if odds_type == "win_any":
            tie_indices = np.all(outcome_arr, axis=1)  # multi-way tie
            outcome_dict["Tie"] = float(np.round(np.mean(tie_indices) * 100, 2))

            for player in range(len(self.players)):
                if player in active_indices:
                    outcome_dict["Player " + str(player + 1)] = float(np.round(
                        np.sum(outcome_arr[~tie_indices, player]) / num_outcomes * 100, 2
                    ))  # type: ignore # lying
                else:
                    outcome_dict["Player " + str(player + 1)] = 0.0

        # Any Multi-way Tie/Tied Win counts as a Tie, Win must be exclusive
        elif odds_type == "tie_win":
            for player in range(len(self.players)):
                if player in active_indices:
                    tie_win_scenarios = outcome_arr[outcome_arr[:, player] == 1].sum(axis=1)  # type: ignore # lying
                    outcome_dict["Player " + str(player + 1) + " Win"] = float(np.round(
                        np.sum(tie_win_scenarios == 1) / num_outcomes * 100, 2
                    ))
                    outcome_dict["Player " + str(player + 1) + " Tie"] = float(np.round(
                        np.sum(tie_win_scenarios > 1) / num_outcomes * 100, 2
                    ))
                else:
                    outcome_dict["Player " + str(player + 1) + " Win"] = 0.0
                    outcome_dict["Player " + str(player + 1) + " Tie"] = 0.0

        # Every possible outcome
        elif odds_type == "precise":
            for num_player in range(1, len(self.players) + 1):
                for player_arr in comb_index(len(self.players), num_player):
                    temp_arr = np.ones(shape=(outcome_arr.shape[0]), dtype=bool)  # type: ignore # lying
                    for player in player_arr:
                        temp_arr = temp_arr & (outcome_arr[:, player] == 1)
                    for non_player in [player for player in range(len(self.players)) if player not in player_arr]:
                        temp_arr = temp_arr & (outcome_arr[:, non_player] == 0)

                    if len(player_arr) == 1:
                        outcome_key = f"Player {player_arr[0] + 1} Win"
                    else:
                        outcome_key = f"Player {','.join([str(player + 1) for player in player_arr])} Tie"

                    outcome_dict[outcome_key] = float(np.round(temp_arr.sum() / num_outcomes * 100, 2))
        return outcome_dict

    def _simulate_calculation(
        self, community_cards: np.ndarray | None, undrawn_combos: np.ndarray
    ) -> np.ndarray[Any, np.dtype[Any]]:
        """Simulates the game by calculating the hand strength of each player

        Parameters
        ----------
        community_cards: np.ndarray
            The community cards.
        undrawn_combos: np.ndarray
            The undrawn cards.

        Returns
        -------
        np.ndarray
            The result array.
        """
        res_arr: np.ndarray[Any, np.dtype[Any]] = np.zeros(  # type: ignore
            shape=(len(undrawn_combos), len(self.players)), dtype=RankingItem
        )

        if len(self.players) >= 2:
            (
                Parallel(n_jobs=multiprocessing.cpu_count(), backend="threading")(
                    delayed(self.gen_single_hand)(community_cards, player, undrawn_combos, res_arr)
                    for player in range(len(self.players))
                )
            )
        else:
            for player in range(len(self.players)):
                self.gen_single_hand(community_cards, player, undrawn_combos, res_arr)
        return res_arr

    def gen_single_hand(
        self,
        community_cards: np.ndarray | None,
        player: int,
        undrawn_combos: np.ndarray,
        res_arr: np.ndarray[Any, np.dtype[Any]],
    ) -> None:
        """Generates a single hand

        Parameters
        ----------
        community_cards: np.ndarray
            The community cards.
        player: int
            The player index.
        undrawn_combos: np.ndarray
            The undrawn cards.
        res_arr: np.ndarray
            The result array.
        """
        if community_cards is None:
            cur_player_cards = np.concatenate(
                [np.repeat([self.players[player].hand.card_arr], len(undrawn_combos), axis=0), undrawn_combos], axis=1
            )
        else:
            cur_player_cards = np.concatenate(
                [
                    np.repeat([self.players[player].hand.card_arr], len(undrawn_combos), axis=0),
                    community_cards,
                    undrawn_combos,
                ],
                axis=1,
            )
        res_arr[:, player] = Ranker.rank_all_hands(cur_player_cards[:, comb_index(7, 5), :])

    def simulate(
        self,
        num_scenarios: int | Literal["all"] = 150000,
        odds_type: Literal["win_any", "tie_win", "precise"] = "tie_win",
        final_hand: bool = False,
    ) -> tuple[dict[str, float], dict[str, float], dict[int, dict[str, float]]] | dict[str, float]:
        """Simulates the game and returns both live and full odds.

        Parameters
        ----------
        num_scenarios: int
            The number of scenarios to simulate, if 'all' then all scenarios will be simulated.
        odds_type: str
            The type of odds to calculate.
        final_hand: bool
            Whether to return the final hand strength of each player.
            If True, returns (live_odds, full_odds, hand_strength).

        Returns
        -------
        tuple[dict, dict, dict] | dict
            If final_hand=True: (live_odds, full_odds, hand_strength)
            Otherwise: full_odds only (for backward compat)
        """
        community_cards, undrawn_combos = self._simulation_preparation(num_scenarios)
        res_arr = self._simulate_calculation(community_cards, undrawn_combos)

        # Full odds: all players (hypothetical "what if everyone stayed in")
        full_odds = self._simulation_analysis(odds_type, res_arr)

        if final_hand:
            # Live odds: only players who haven't folded yet
            active_indices = [i for i, p in enumerate(self.players) if not p.folded]
            live_odds = self._simulation_analysis(odds_type, res_arr, active_indices)
            final_hand_dict = self._hand_strength_analysis(res_arr)
            return live_odds, full_odds, final_hand_dict

        return full_odds
