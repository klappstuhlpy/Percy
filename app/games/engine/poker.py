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
from dataclasses import dataclass
from itertools import chain, combinations
from typing import Any, Literal, NamedTuple, cast

import numpy as np
from joblib import Parallel, delayed
from scipy.special import comb

from app.cogs.games._classes import NAMED_HAND, SUITS, UNAMED, BaseCard, BaseHand, Deck
from app.utils import RevDict, fnumb

__all__ = (
    'Card',
    'CombResult',
    'Hand',
    'HandResult',
    'Player',
    'Pot',
    'Ranker',
    'RankingItem',
    'TableState',
    'TexasHoldem',
    'comb_index',
    'item_by_count',
)


class TableState(enum.Enum):
    """Represents the state of the table"""
    STOPPED = 0
    RUNNING = 1
    FINISHED = 2
    PREPARED = 3


class Card(BaseCard):
    """Represents a card in a deck"""

    @property
    def display_text_short(self) -> str:
        """Returns the display text of the card"""
        return f'{self.name.title()}s'

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
                name=f'High Card, {high_card.display_text}',
                cards=[Card(suit=suit, value=value) for value, suit in self.card_arr],
                # in this case, we just summarized the cards values of the hand
                value=sum(self.card_arr[:, 0])
            )

        best_comb: RankingItem = np.max(combs.ranking)

        name = NAMED_HAND[np.max(best_comb.rank) // 16 ** 5]
        if best_comb.name is not None:
            name += f', {best_comb.name}'

        return HandResult(
            name=name,
            cards=[Card(suit=x[1], value=x[0]) for x in combs.all_combos[0, np.argmax(combs.ranking), :, :]],
            value=best_comb.rank
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
            'np.ndarray[Any, np.dtype[Any]]', Ranker.rank_all_hands(all_combos, return_all=True))
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
class RankingItem:
    """Represents the ranking of a hand of cards"""
    rank: int
    cards: np.ndarray
    name: str | None = None

    def __ge__(self, other: RankingItem) -> bool:
        if not isinstance(other, RankingItem):
            raise TypeError(f'unsupported operand type(s) for >=: {type(self)} and {type(other)}')

        return self.rank >= other.rank

    def __gt__(self, other: RankingItem) -> bool:
        if not isinstance(other, RankingItem):
            raise TypeError(f'unsupported operand type(s) for >: {type(self)} and {type(other)}')

        return self.rank > other.rank

    def __floordiv__(self, other: Any) -> int:
        if not isinstance(other, int):
            raise TypeError(f'unsupported operand type(s) for //: {type(self)} and {type(other)}')

        return self.rank // other

    def __imul__(self, other: Any) -> RankingItem:
        if not isinstance(other, (int, np.ndarray)):
            raise TypeError(f'unsupported operand type(s) for *: {type(self)} and {type(other)}')

        self.rank = int(self.rank * other)
        return self


class Ranker:
    """Represents the ranking of a hand of cards"""

    @classmethod
    def rank_all_hands(cls, hand_combos: np.ndarray, return_all: bool = False) -> RankingItem | np.ndarray[Any, np.dtype[Any]]:
        """Returns the rank of all combinations of cards."""
        rank_res_arr: np.ndarray[Any, np.dtype[Any]] = np.zeros(  # type: ignore
            shape=(hand_combos.shape[1], hand_combos.shape[0]), dtype=RankingItem)

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
            return np.array(
                [x.rank for x in rank_arr]) * (16 ** 5) + np.sum(num_combos * np.power(16, np.arange(0, 5)), axis=1)

        for i, ranking in enumerate(rank_arr):
            ranking = cast('RankingItem', ranking)
            # Example: Implement the logic for each RankingItem
            rank_arr[i] = RankingItem(
                name=ranking.name, cards=ranking.cards,
                rank=ranking.rank * (16 ** 5) + np.sum(ranking.cards * np.power(16, np.arange(0, 5)))
            )

        return rank_arr

    @staticmethod
    def is_straight_arr(num_combos: np.ndarray) -> bool:
        """Returns whether the combination of cards is a straight."""
        straight_check: np.ndarray = np.zeros(len(num_combos), dtype=int)
        for i in range(4):
            if i <= 2:
                straight_check += (num_combos[:, i] == (num_combos[:, i + 1] - 1))
            else:
                straight_check += (num_combos[:, i] == (num_combos[:, i + 1] - 1))
                straight_check += ((num_combos[:, i] == 5) & (num_combos[:, i + 1] == 14))

        return straight_check == 4

    @staticmethod
    def is_suit_arr(suit_combos: np.ndarray) -> bool:
        """Returns whether the combination of cards is a flush."""
        return np.max(suit_combos, axis=1) == np.min(suit_combos, axis=1)

    @staticmethod
    def straight_flush_check(num_combos: np.ndarray, rank_arr: np.ndarray[Any, np.dtype[Any]], straight_arr: bool, suit_arr: bool) -> None:
        """Checks if the combination of cards is a straight flush."""
        ace_low_straight = np.max(num_combos[:, 4]) == 14 and np.min(num_combos[:, 0]) == 2
        straight_label = 'Low' if ace_low_straight else 'High'

        rank_arr[(rank_arr == 0) & (straight_arr & suit_arr)] = RankingItem(
            rank=8, name=f'{UNAMED[np.max(num_combos[:, 4])]} {straight_label}', cards=num_combos[0, :5])

        # Rearrange order of 2345A to A2345
        reorder_idx = (rank_arr == 8) & (num_combos[:, 0] == 2) & (num_combos[:, 4] == 14)
        num_combos[reorder_idx, :] = np.concatenate([num_combos[reorder_idx, 4:], num_combos[reorder_idx, :4]], axis=1)

    @staticmethod
    def four_of_a_kind_check(num_combos: np.ndarray, rank_arr: np.ndarray[Any, np.dtype[Any]]) -> None:
        """Checks if the combination of cards is a four of a kind."""
        small = np.all(num_combos[:, 0:4] == num_combos[:, :1], axis=1)  # 22223
        large = np.all(num_combos[:, 1:] == num_combos[:, 4:], axis=1)  # 24444

        condition = (small | large)
        four_of_a_kind = item_by_count(num_combos[condition], 4)
        rank_arr[(rank_arr == 0) & condition] = RankingItem(
            rank=7, name=f'{four_of_a_kind[0]}s', cards=num_combos[0, :5])

        reorder_idx = (rank_arr == 7) & small
        num_combos[reorder_idx, :] = np.concatenate([num_combos[reorder_idx, 4:], num_combos[reorder_idx, :4]], axis=1)

    @staticmethod
    def full_house_check(num_combos: np.ndarray, rank_arr: np.ndarray[Any, np.dtype[Any]]) -> None:
        """Checks if the combination of cards is a full house."""
        small = np.all(
            (num_combos[:, 0:3] == num_combos[:, :1])
            & (num_combos[:, 3:4] == num_combos[:, 4:5]), axis=1)  # 22233

        large = np.all(
            (num_combos[:, 0:1] == num_combos[:, 1:2])
            & (num_combos[:, 2:5] == num_combos[:, 4:]), axis=1)  # 22444

        condition = (small | large)
        three_pair, two_pair = item_by_count(num_combos[condition], 3), item_by_count(num_combos[condition], 2)
        rank_arr[(rank_arr == 0) & condition] = RankingItem(
            rank=6, name=f'{UNAMED[three_pair[0]]}s over {UNAMED[two_pair[0]]}s',
            cards=num_combos[0, :5])

        reorder_idx = (rank_arr == 6) & small
        num_combos[reorder_idx, :] = np.concatenate([num_combos[reorder_idx, 3:], num_combos[reorder_idx, :3]], axis=1)

    @staticmethod
    def flush_check(num_combos: np.ndarray, suit_combos: np.ndarray, rank_arr: np.ndarray[Any, np.dtype[Any]], suit_arr: bool) -> None:
        """Checks if the combination of cards is a flush."""
        rank_arr[(rank_arr == 0) & suit_arr] = RankingItem(
            rank=5, name=RevDict(SUITS)[np.max(suit_combos, axis=1)[0]].title(), cards=num_combos[0, :5])

    @staticmethod
    def straight_check(num_combos: np.ndarray, rank_arr: np.ndarray[Any, np.dtype[Any]], straight_arr: bool) -> None:
        """Checks if the combination of cards is a straight."""
        ace_low_straight = np.max(num_combos[:, 4]) == 14 and np.min(num_combos[:, 0]) == 2
        straight_label = 'Low' if ace_low_straight else 'High'

        rank_arr[(rank_arr == 0) & straight_arr] = RankingItem(
            rank=4, name=f'{UNAMED[np.max(num_combos[:, 4])]} {straight_label}', cards=num_combos[0, :5])

        # Rearrange order of 2345A to A2345
        reorder_idx = (rank_arr == 4) & (num_combos[:, 0] == 2) & (num_combos[:, 4] == 14)
        num_combos[reorder_idx, :] = np.concatenate([num_combos[reorder_idx, 4:], num_combos[reorder_idx, :4]], axis=1)

    @staticmethod
    def three_of_a_kind_check(num_combos: np.ndarray, rank_arr: np.ndarray[Any, np.dtype[Any]]) -> None:
        """Checks if the combination of cards is a three of a kind."""
        small = np.all((num_combos[:, 0:3] == num_combos[:, :1]), axis=1)  # 22235
        middle = np.all((num_combos[:, 1:4] == num_combos[:, 1:2]), axis=1)  # 23335
        large = np.all((num_combos[:, 2:] == num_combos[:, 2:3]), axis=1)  # 36AAA

        condition = (small | middle | large)
        three_of_a_kind = item_by_count(num_combos[condition], 3)
        rank_arr[(rank_arr == 0) & condition] = RankingItem(
            rank=3, name=f'{UNAMED[three_of_a_kind[0]]}s', cards=num_combos[0, :5])

        reorder_small = (rank_arr == 3) & small
        reorder_middle = (rank_arr == 3) & large

        num_combos[reorder_small, :] = np.concatenate(
            [num_combos[reorder_small, 3:], num_combos[reorder_small, :3]], axis=1)
        num_combos[reorder_middle, :] = np.concatenate([
            num_combos[reorder_middle, :1],
            num_combos[reorder_middle, 4:],
            num_combos[reorder_middle, 1:4]
        ], axis=1)

    @staticmethod
    def two_pairs_check(num_combos: np.ndarray, rank_arr: np.ndarray[Any, np.dtype[Any]]) -> None:
        """Checks if the combination of cards is a two pairs."""
        small = np.all(
            (num_combos[:, 0:2] == num_combos[:, :1]) & (num_combos[:, 2:4] == num_combos[:, 2:3]), axis=1)  # 2233A

        middle = np.all(
            (num_combos[:, 0:2] == num_combos[:, :1]) & (num_combos[:, 3:] == num_combos[:, 4:]), axis=1)  # 223AA

        large = np.all(
            (num_combos[:, 1:3] == num_combos[:, 1:2]) & (num_combos[:, 3:] == num_combos[:, 4:]), axis=1)  # 233AA

        condition = (small | middle | large)
        two_pairs = item_by_count(num_combos[condition], 2)
        if len(two_pairs) == 2:
            rank_arr[(rank_arr == 0) & (small | middle | large)] = RankingItem(
                rank=2, name=f'{UNAMED[two_pairs[0]]}s and {UNAMED[two_pairs[1]]}s',
                cards=num_combos[0, :5])

        reorder_small = (rank_arr == 2) & small
        reorder_middle = (rank_arr == 2) & large

        num_combos[reorder_small, :] = np.concatenate(
            [num_combos[reorder_small, 4:], num_combos[reorder_small, :4]], axis=1)
        num_combos[reorder_middle, :] = np.concatenate([
            num_combos[reorder_middle, 2:3],
            num_combos[reorder_middle, 0:2],
            num_combos[reorder_middle, 3:]
        ], axis=1)

    @staticmethod
    def one_pair_check(num_combos: np.ndarray, rank_arr: np.ndarray[Any, np.dtype[Any]]) -> None:
        """Checks if the combination of cards is a one pair."""
        small = np.all((num_combos[:, 0:2] == num_combos[:, :1]), axis=1)  # 22345
        mid_small = np.all((num_combos[:, 1:3] == num_combos[:, 1:2]), axis=1)  # 23345
        mid_large = np.all((num_combos[:, 2:4] == num_combos[:, 2:3]), axis=1)  # 23445
        large = np.all((num_combos[:, 3:] == num_combos[:, 3:4]), axis=1)  # 23455

        condition = (small | mid_small | mid_large | large)
        one_pair = item_by_count(num_combos[condition], 2)
        rank_arr[(rank_arr == 0) & condition] = RankingItem(
            rank=1, name=f'{UNAMED[one_pair[0]]}s', cards=num_combos[0, :5])

        reorder_small = (rank_arr == 1) & small
        reorder_mid_small = (rank_arr == 1) & mid_small
        reorder_mid_large = (rank_arr == 1) & mid_large

        num_combos[reorder_small, :] = np.concatenate(
            [num_combos[reorder_small, 2:], num_combos[reorder_small, :2]], axis=1)
        num_combos[reorder_mid_small, :] = np.concatenate([
            num_combos[reorder_mid_small, :1],
            num_combos[reorder_mid_small, 3:],
            num_combos[reorder_mid_small, 1:3]
        ], axis=1)
        num_combos[reorder_mid_large, :] = np.concatenate([
            num_combos[reorder_mid_large, :2],
            num_combos[reorder_mid_large, 4:],
            num_combos[reorder_mid_large, 2:4]
        ], axis=1)

    @staticmethod
    def high_card_check(num_combos: np.ndarray, rank_arr: np.ndarray[Any, np.dtype[Any]]) -> None:
        """Checks if the combination of cards is a high card."""
        rank_arr[(rank_arr == 0)] = RankingItem(
            rank=0, name=f'{UNAMED[np.max(num_combos[:, 4])]} High', cards=num_combos[0, :5])

        # Rearrange order of 2345A to A2345
        reorder_idx = (rank_arr == 0) & (num_combos[:, 0] == 2) & (num_combos[:, 4] == 14)
        num_combos[reorder_idx, :] = np.concatenate([num_combos[reorder_idx, 4:], num_combos[reorder_idx, :4]], axis=1)


class Player:
    """Represents a player in a poker game.

    ``member`` is an opaque identity token supplied by the cog (a
    ``discord.Member`` at runtime); the engine only uses it for equality/identity.
    """

    def __init__(self, member: Any, stack: int) -> None:
        self.member: Any = member
        self.hand: Hand = Hand()

        self.stack: int = stack
        self.bet: int = 0

        self.wait_for_allin_call: bool = False
        self.folded: bool = False
        self.all_in: bool = False
        self.checked: bool = False

    def __repr__(self) -> str:
        return (
            f'Player(member={self.member} hand={self.hand} stack={self.stack} '
            f'bet={self.bet} folded={self.folded} all_in={self.all_in})'
        )

    def reset(self) -> None:
        """Resets the player"""
        self.bet = 0
        self.wait_for_allin_call = False
        self.folded = False
        self.all_in = False
        self.checked = False
        self.hand = Hand()


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
        return f'Pot(amount={self.amount}, players={len(self.players)})'

    def __iadd__(self, other: int) -> Pot:
        if not isinstance(other, int):
            raise TypeError(f'unsupported operand type(s) for +: {type(self)} and {type(other)}')

        self.amount += other
        return self

    def __isub__(self, other: int) -> Pot:
        if not isinstance(other, int):
            raise TypeError(f'unsupported operand type(s) for -: {type(self)} and {type(other)}')

        self.amount -= other
        return self

    def __int__(self) -> int:
        return self.amount

    def __len__(self) -> int:
        return self.amount

    def __str__(self) -> str:
        return f'{fnumb(self.amount)}'


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

    def __init__(
            self,
            *,
            first_buy_in: int,
            decks: int = 1,
            max_players: int = 4
    ) -> None:
        self.first_buy_in: int = first_buy_in
        self.deck: Deck = Deck(game='poker', decks=decks)

        self.state: TableState = TableState.STOPPED

        self.community_arr: np.ndarray = np.zeros(shape=(0, 2), dtype=int)
        self.players: list[Player] = []

        # Identity of the hosting member; set by the bridge. Used only for host checks.
        self.host: Any = None
        self.max_players: int = max_players
        self.player_index: int = 0

        # Small Blind and Big Blind
        # Those indexes are used to determine the player that is the small blind and the big blind
        # They are set when the game starts behind the "dealer"
        self.blind_index: tuple[int, int] | None = None
        self.big_blind: int = max(int(self.first_buy_in * 0.01), 2)
        self.small_blind: int = self.big_blind // 2

        # Pots
        self.pot: Pot = Pot(amount=0)
        self.side_pots: list[Pot] = []

        # Game Tracking
        self.ranks: list[tuple[Player, HandResult]] = []
        self.tie = False
        self.winners: list[tuple[list[Player], Pot]] = []
        self.eliminated_players: list[Player] = []

        self.analysis: list[tuple[dict[str, float], dict[int, dict[str, float]]]] = []

    def __repr__(self) -> str:
        return f'TexasHoldem(state={self.state} host={self.host} players={len(self.players)} max_players={self.max_players})'

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
            self.Raise(contribution)

    def Raise(self, amount: int) -> None:
        """Raises the bet for the current player."""
        if self.side_pots:
            # if we have at least one side pot, we will update this one and not the initial pot
            self.side_pots[-1] += amount
        else:
            self.pot += amount

        player = self.players[self.player_index]

        # Calculate the amount the player can contribute to the current pot
        contribution = min(player.stack, amount)

        # Deduct the contribution from the player's stack
        player.stack -= contribution
        player.bet += amount

        self.Check()

    def Check(self) -> None:
        """Checks the street for the current player."""
        player = self.players[self.player_index]
        player.checked = True

    def Fold(self) -> None:
        """Folds the current player."""
        player = self.players[self.player_index]
        player.folded = True

    def reset(self) -> None:
        """Resets the table to its initial state"""
        self.community_arr = np.zeros(shape=(0, 2), dtype=int)
        self.ranks = []
        self.winners = []
        self.side_pots = []
        self.eliminated_players = []
        self.pot = Pot(amount=0)
        self.tie = False

    def start(self) -> None:
        """Starts the game by dealing the cards and setting the sb and bb.

        This function is called when the game starts and deals the cards to the players and sets the sb and bb.
        The caller (bridge) is responsible for (re)starting the autoplay timer afterwards.
        """
        self.state = TableState.RUNNING
        self.deal()

        self.pot.players = self.players

        if self.blind_index is None:
            self.blind_index = (0, 1)

        # Set the player index to the player behind the bb
        self.player_index = (self.blind_index[1] + 1) % len(self.players)

        # Set sb and bb and take their bets
        for index in self.blind_index:
            player = self.players[index]
            player.bet = self.small_blind if index == self.blind_index[0] else self.big_blind
            player.stack -= player.bet

        self.pot.amount = self.small_blind + self.big_blind

        self.analysis.append(cast('tuple[dict[str, float], dict[int, dict[str, float]]]', self.simulate(final_hand=True)))

    def end(self) -> None:
        """Ends the game by calculating the winner(s).

        This function is called when the game is over and calculates the winner(s) of the game.
        """
        self.state = TableState.FINISHED

        for player in self.playing_players:
            result = player.hand.evaluate(self.community_arr)
            self.ranks.append((player, result))

        self.ranks.sort(key=lambda x: x[1].value, reverse=True)

        # Calculate the winner(s)
        for pot in [self.pot, *self.side_pots]:
            pot_winners = []
            for player in pot.players:
                ranked = next((rank for rank in self.ranks if rank[0] == player), [None, None])[1]
                if ranked and ranked.value == max([rank[1].value for rank in self.ranks]):
                    pot_winners.append(player)

            for winner in pot_winners:
                winner.stack += pot.amount // len(pot_winners)

            if all(value == max(self.ranks, key=lambda x: x[1].value) for value in self.ranks):
                self.tie = True

            self.winners.append((pot_winners, pot))

    def add_player(self, member: Any, stack: int) -> None:
        """Adds a player to the table.

        Parameters
        ----------
        member : Any
            The member to add to the table (a ``discord.Member`` at runtime).
        stack : int
            The stack of the player
        """
        self.players.append(Player(member=member, stack=stack))

    def remove_player(self, member: Any) -> int:
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

        assert self.blind_index is not None
        self.blind_index = (
            (self.blind_index[0] + 1) % len(self.players), (self.blind_index[1] + 1) % len(self.players))
        self.player_index = (self.blind_index[1] + 1) % len(self.players)
        return removed

    def __fill_left_community_cards(self) -> None:
        """Fills the left community cards"""
        while len(self.community_arr) < 5:
            if len(self.community_arr) in (3, 4):
                # add analysis data for the flop and turn
                self.analysis.append(cast('tuple[dict[str, float], dict[int, dict[str, float]]]', self.simulate(final_hand=True)))

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
            for player in self.playing_players:
                if not player.all_in:
                    player.checked = False
        else:
            if all(player.checked for player in self.playing_players):
                # Next street
                assert self.blind_index is not None
                self.player_index = (self.blind_index[1] + 1) % len(self.players)

                for player in self.playing_players:
                    if not player.all_in:
                        player.checked = False

                if (all_in_player := next((player for player in self.playing_players if player.wait_for_allin_call), None)) and len(self.playing_players) > 2:
                    # the call round for the all-in player is over,
                    # all further bets will be added to the side pot
                    all_in_player.wait_for_allin_call = False
                    self.side_pots.append(Pot(amount=0, players=[p for p in self.playing_players if not p.all_in]))

                for _ in range(self.to_draw):
                    self.community_arr = np.concatenate([self.community_arr, self.deck.draw()], axis=0)

                if len(self.community_arr) != 5:
                    # Only calculate the odds if the game is not over
                    self.analysis.append(cast('tuple[dict[str, float], dict[int, dict[str, float]]]', self.simulate(final_hand=True)))

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
        TO_DRAW_MAP = {
            0: 3,
            3: 1,
            4: 1,
            5: 0
        }
        return TO_DRAW_MAP[len(self.community_arr)]

    def deal(self) -> None:
        """Deals the cards to the players"""
        for _ in range(2):
            for player in self.players:
                player.hand.add(self.deck.draw())

    # Simulation

    def _simulation_preparation(self, num_scenarios: int | Literal['all']) -> tuple[np.ndarray | None, np.ndarray]:
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
        if num_scenarios != 'all':
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
            hand_type, hand_freq = np.unique((res_arr // 16 ** 5)[:, player], return_counts=True)  # type: ignore[operator]
            final_hand_dict[player + 1] = dict(
                zip(np.vectorize(NAMED_HAND.get)(hand_type),
                    np.round(hand_freq / hand_freq.sum() * 100, 2).astype(float)))
        return final_hand_dict

    def _simulation_analysis(self, odds_type: Literal['win_any', 'tie_win', 'precise'], res_arr: np.ndarray[Any, np.dtype[Any]]) -> dict:
        outcome_arr = (res_arr == np.expand_dims(np.max(res_arr, axis=1), axis=1))
        num_outcomes = len(outcome_arr)  # type: ignore # lying
        outcome_dict = {}

        # Any Tied Win counts as a Win
        if odds_type == 'win_any':
            tie_indices = np.all(outcome_arr, axis=1)  # multi-way tie
            outcome_dict['Tie'] = np.round(np.mean(tie_indices) * 100, 2)

            for player in range(len(self.players)):
                outcome_dict['Player ' + str(player + 1)] = np.round(
                    np.sum(outcome_arr[~tie_indices, player]) / num_outcomes * 100, 2)  # type: ignore # lying

        # Any Multi-way Tie/Tied Win counts as a Tie, Win must be exclusive
        elif odds_type == 'tie_win':
            for player in range(len(self.players)):
                tie_win_scenarios = outcome_arr[outcome_arr[:, player] == 1].sum(axis=1)  # type: ignore # lying
                outcome_dict['Player ' + str(player + 1) + ' Win'] = np.round(
                    np.sum(tie_win_scenarios == 1) / num_outcomes * 100, 2)
                outcome_dict['Player ' + str(player + 1) + ' Tie'] = np.round(
                    np.sum(tie_win_scenarios > 1) / num_outcomes * 100, 2)

        # Every possible outcome
        elif odds_type == 'precise':
            for num_player in range(1, len(self.players) + 1):
                for player_arr in comb_index(len(self.players), num_player):
                    temp_arr = np.ones(shape=(outcome_arr.shape[0]), dtype=bool)  # type: ignore # lying
                    for player in player_arr:
                        temp_arr = (temp_arr & (outcome_arr[:, player] == 1))
                    for non_player in [player for player in range(len(self.players)) if player not in player_arr]:
                        temp_arr = (temp_arr & (outcome_arr[:, non_player] == 0))

                    if len(player_arr) == 1:
                        outcome_key = f'Player {player_arr[0] + 1} Win'
                    else:
                        outcome_key = f'Player {','.join([str(player + 1) for player in player_arr])} Tie'

                    outcome_dict[outcome_key] = np.round(temp_arr.sum() / num_outcomes * 100, 2)
        return outcome_dict

    def _simulate_calculation(self, community_cards: np.ndarray | None, undrawn_combos: np.ndarray) -> np.ndarray[Any, np.dtype[Any]]:
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
            shape=(len(undrawn_combos), len(self.players)), dtype=RankingItem)

        if len(self.players) >= 2:
            (Parallel(n_jobs=multiprocessing.cpu_count(), backend="threading")
             (delayed(self.gen_single_hand)(community_cards, player, undrawn_combos, res_arr) for player in
              range(len(self.players))))
        else:
            for player in range(len(self.players)):
                self.gen_single_hand(community_cards, player, undrawn_combos, res_arr)
        return res_arr

    def gen_single_hand(
            self,
            community_cards: np.ndarray | None,
            player: int,
            undrawn_combos: np.ndarray,
            res_arr: np.ndarray[Any, np.dtype[Any]]
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
                [
                    np.repeat([self.players[player].hand.card_arr], len(undrawn_combos), axis=0),
                    undrawn_combos
                ], axis=1)
        else:
            cur_player_cards = np.concatenate(
                [
                    np.repeat([self.players[player].hand.card_arr], len(undrawn_combos), axis=0),
                    community_cards,
                    undrawn_combos
                ], axis=1)
        res_arr[:, player] = Ranker.rank_all_hands(cur_player_cards[:, comb_index(7, 5), :])

    def simulate(
            self,
            num_scenarios: int | Literal['all'] = 150000,
            odds_type: Literal['win_any', 'tie_win', 'precise'] = 'tie_win',
            final_hand: bool = False
    ) -> tuple[dict, dict] | dict:
        """Simulates the game

        Parameters
        ----------
        num_scenarios: int
            The number of scenarios to simulate, if 'all' then all scenarios will be simulated.
        odds_type: str
            The type of odds to calculate.
        final_hand: bool
            Whether to return the final hand strength of each player.

        Returns
        -------
        tuple[dict, dict] | dict
            The outcome dictionary.
        """
        community_cards, undrawn_combos = self._simulation_preparation(num_scenarios)
        res_arr = self._simulate_calculation(community_cards, undrawn_combos)
        outcome_dict = self._simulation_analysis(odds_type, res_arr)

        if final_hand:
            final_hand_dict = self._hand_strength_analysis(res_arr)
            return outcome_dict, final_hand_dict

        return outcome_dict
