from __future__ import annotations

import asyncio
import datetime
import enum
import multiprocessing
import random
from contextlib import suppress
from dataclasses import dataclass
from itertools import chain, combinations, zip_longest
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

import discord
import numpy as np
from joblib import Parallel, delayed
from numpy import ndarray
from scipy.special import comb

from app.cogs.games._classes import NAMED_HAND, SUITS, UNAMED, BaseCard, BaseHand, Deck, MinimumBet
from app.core.views import View
from app.rendering import BarChart
from app.utils import RevDict, helpers, number_suffix, fnumb
from config import Emojis

if TYPE_CHECKING:
    from PIL import Image

    from app.cogs.economy import Economy
    from app.cogs.games import Games
    from app.core import Context
    from app.database.base import Balance


# Poker - Texas Hold'em


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


def item_by_count(array: np.ndarray, n: int) -> ndarray[int]:
    """Returns the `n` common element from an iterable."""
    counts = np.bincount(array.flatten())
    if n not in counts:
        # placeholder
        return np.zeros(10, dtype=int)  # type: ignore
    return np.argwhere(counts == n).flatten()  # type: ignore


def comb_index(n: int, k: int) -> np.ndarray:
    """Returns the index of all combinations of k elements from n elements."""
    count = comb(n, k, exact=True)
    index = np.fromiter(chain.from_iterable(combinations(range(n), k)), int, count=count * k)
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
        ranking: np.ndarray[RankingItem] = Ranker.rank_all_hands(all_combos, return_all=True)
        return CombResult(all_combos=all_combos, ranking=ranking)


class CombResult(NamedTuple):
    """Represents the result of a combination of cards

    Parameters
    ----------
    all_combos : np.ndarray
        An array of all combinations of cards.
    ranking : np.ndarray[RankingItem]
        An array of the ranking of each combination of cards.
    """

    all_combos: np.ndarray
    ranking: np.ndarray[RankingItem]


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
    name: str = None

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

        self.rank *= other
        return self


class Ranker:
    """Represents the ranking of a hand of cards"""

    @classmethod
    def rank_all_hands(cls, hand_combos: np.ndarray, return_all: bool = False) -> RankingItem | np.ndarray[RankingItem]:
        """Returns the rank of all combinations of cards."""
        rank_res_arr: np.ndarray[RankingItem] = np.zeros(  # type: ignore
            shape=(hand_combos.shape[1], hand_combos.shape[0]), dtype=RankingItem)

        for scenario in range(hand_combos.shape[1]):
            rank_res_arr[scenario, :] = cls.rank_one_hand(hand_combos[:, scenario, :, :], group_by=not return_all)

        if return_all:
            return rank_res_arr
        else:
            return np.max(rank_res_arr, axis=0)

    @classmethod
    def rank_one_hand(cls, hand_combos: np.ndarray, group_by: bool = False) -> np.ndarray[RankingItem]:
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
        np.ndarray[RankingItem]
            The rank of the combination of cards.
        """
        num_combos = hand_combos[:, :, 0]
        num_combos.sort(axis=1)

        suit_combos = hand_combos[:, :, 1]

        is_suit_arr = cls.is_suit_arr(suit_combos)
        is_straight_arr = cls.is_straight_arr(num_combos)

        rank_arr: np.ndarray[RankingItem] = np.zeros(num_combos.shape[0], dtype=RankingItem)  # type: ignore

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

        for i, ranking in enumerate(rank_arr):  # type: int, RankingItem
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
    def straight_flush_check(num_combos: np.ndarray, rank_arr: np.ndarray, straight_arr: bool, suit_arr: bool) -> None:
        """Checks if the combination of cards is a straight flush."""
        ace_low_straight = np.max(num_combos[:, 4]) == 14 and np.min(num_combos[:, 0]) == 2
        straight_label = 'Low' if ace_low_straight else 'High'

        rank_arr[(rank_arr == 0) & (straight_arr & suit_arr)] = RankingItem(
            rank=8, name=f'{UNAMED[np.max(num_combos[:, 4])]} {straight_label}', cards=num_combos[0, :5])

        # Rearrange order of 2345A to A2345
        reorder_idx = (rank_arr == 8) & (num_combos[:, 0] == 2) & (num_combos[:, 4] == 14)
        num_combos[reorder_idx, :] = np.concatenate([num_combos[reorder_idx, 4:], num_combos[reorder_idx, :4]], axis=1)

    @staticmethod
    def four_of_a_kind_check(num_combos: np.ndarray, rank_arr: np.ndarray) -> None:
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
    def full_house_check(num_combos: np.ndarray, rank_arr: np.ndarray) -> None:
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
    def flush_check(num_combos: np.ndarray, suit_combos: np.ndarray, rank_arr: np.ndarray, suit_arr: bool) -> None:
        """Checks if the combination of cards is a flush."""
        rank_arr[(rank_arr == 0) & suit_arr] = RankingItem(
            rank=5, name=RevDict(SUITS)[np.max(suit_combos, axis=1)[0]].title(), cards=num_combos[0, :5])

    @staticmethod
    def straight_check(num_combos: np.ndarray, rank_arr: np.ndarray, straight_arr: bool) -> None:
        """Checks if the combination of cards is a straight."""
        ace_low_straight = np.max(num_combos[:, 4]) == 14 and np.min(num_combos[:, 0]) == 2
        straight_label = 'Low' if ace_low_straight else 'High'

        rank_arr[(rank_arr == 0) & straight_arr] = RankingItem(
            rank=4, name=f'{UNAMED[np.max(num_combos[:, 4])]} {straight_label}', cards=num_combos[0, :5])

        # Rearrange order of 2345A to A2345
        reorder_idx = (rank_arr == 4) & (num_combos[:, 0] == 2) & (num_combos[:, 4] == 14)
        num_combos[reorder_idx, :] = np.concatenate([num_combos[reorder_idx, 4:], num_combos[reorder_idx, :4]], axis=1)

    @staticmethod
    def three_of_a_kind_check(num_combos: np.ndarray, rank_arr: np.ndarray) -> None:
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
    def two_pairs_check(num_combos: np.ndarray, rank_arr: np.ndarray) -> None:
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
    def one_pair_check(num_combos: np.ndarray, rank_arr: np.ndarray) -> None:
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
    def high_card_check(num_combos: np.ndarray, rank_arr: np.ndarray) -> None:
        """Checks if the combination of cards is a high card."""
        rank_arr[(rank_arr == 0)] = RankingItem(
            rank=0, name=f'{UNAMED[np.max(num_combos[:, 4])]} High', cards=num_combos[0, :5])

        # Rearrange order of 2345A to A2345
        reorder_idx = (rank_arr == 0) & (num_combos[:, 0] == 2) & (num_combos[:, 4] == 14)
        num_combos[reorder_idx, :] = np.concatenate([num_combos[reorder_idx, 4:], num_combos[reorder_idx, :4]], axis=1)


class Player:
    """Represents a player in a poker game"""

    def __init__(self, member: discord.Member, stack: int) -> None:
        self.member: discord.Member = member
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
    """Represents a game with players and the poker logic.

    This supports a Simulation Logic that can calculate the odds of winning for each player before the the turn
    has been played.

    This also supports an Auto Play Logic that can play the game automatically for you after 120 Minutes if
    a player has not responded to the game.

    Parameters
    ----------
    cog : Games
        The Games cog.
    ctx : Context
        The Context.
    decks : int
        The number of decks to use.
    first_buy_in : int
        The first buy-in for the game.
    max_players : int
        The maximum number of players allowed in the game.
    """

    def __init__(
            self,
            cog: Games,
            ctx: Context,
            *,
            first_buy_in: int,
            decks: int = 1,
            max_players: int = 4
    ) -> None:
        # Initialize basic parameters
        self.cog: Games = cog
        self.ctx: Context = ctx
        self.first_buy_in: int = first_buy_in
        self.deck: Deck = Deck(game='poker', decks=decks)

        self.state: TableState = TableState.STOPPED

        # Utils
        self.economy: Economy | None = ctx.bot.get_cog('Economy')
        self.message: discord.Message | None = None

        self.community_arr: np.ndarray = np.zeros(shape=(0, 2), dtype=int)
        self.players: list[Player] = []

        # Initialize game settings
        self.host: discord.Member = self.players[0].member if self.players else ctx.author
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

        self.view: TableView = TableView(table=self)

        # Event Loop
        self.loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        self.running_autoplay_loop: asyncio.Task | None = None

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

        self.analysis.append(self.simulate(final_hand=True))

        self.running_autoplay_loop = self.loop.create_task(self.start_timer(self.players[self.player_index]))

    def end(self) -> None:
        """Ends the game by calculating the winner(s).

        This function is called when the game is over and calculates the winner(s) of the game.
        """
        self.state = TableState.FINISHED

        if self.running_autoplay_loop is not None:
            self.running_autoplay_loop.cancel()

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

    def add_player(self, member: discord.Member, stack: int) -> None:
        """Adds a player to the table.

        Parameters
        ----------
        member : discord.Member
            The member to add to the table
        stack : int
            The stack of the player
        """
        self.players.append(Player(member=member, stack=stack))

    async def remove_player(self, member: discord.Member) -> None:
        """|coro|

        Removes a player from the table

        Parameters
        ----------
        member : discord.Member
            The member to remove from the table
        """
        player = discord.utils.get(self.players, member=member)
        self.players.remove(player)
        stack_left = player.stack
        if stack_left > 0:
            query = "UPDATE economy SET cash = cash + $1 WHERE user_id = $2 AND guild_id = $3;"
            await self.ctx.bot.db.execute(query, stack_left, member.id, self.ctx.guild.id)

    async def prepare_next_game(self) -> None:
        """Prepares the next round"""
        self.state = TableState.PREPARED

        # Reset players and remove players with no chips
        for player in self.players:
            if player.stack <= 0:
                await self.remove_player(player.member)
                await self.message.reply(
                    f'\N{LEAF FLUTTERING IN WIND} {player.member.mention} has been removed from the game because they ran out of chips.')
            else:
                player.reset()

        if len(self.players) < 2:
            self.state = TableState.STOPPED

        self.reset()

        self.blind_index = (
            (self.blind_index[0] + 1) % len(self.players), (self.blind_index[1] + 1) % len(self.players))
        self.player_index = (self.blind_index[1] + 1) % len(self.players)

    def __fill_left_community_cards(self) -> None:
        """Fills the left community cards"""
        while len(self.community_arr) < 5:
            if len(self.community_arr) in (3, 4):
                # add analysis data for the flop and turn
                self.analysis.append(self.simulate(final_hand=True))

            self.community_arr = np.concatenate([self.community_arr, self.deck.draw()], axis=0)

    def __update_pots(self) -> None:
        """Remove folded players from all pots so they cannot win them."""
        for pot in [self.pot, *self.side_pots]:
            pot.players = [player for player in pot.players if not player.folded]

    def switch_player(self, by_raise: bool = False) -> None:
        """Switches to the next player"""
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
                    self.analysis.append(self.simulate(final_hand=True))

        self.running_autoplay_loop = self.loop.create_task(self.start_timer(current_player))

    async def autoplay(self, player: Player) -> None:
        """|coro|

        Automatically plays for a player if they take too long for their turn.

        Parameters
        ----------
        player : Player
            The player to auto-play for.
        """
        if player.all_in or player.folded:
            return

        player.checked = True

        max_bet = max([p.bet for p in self.playing_players])
        if player.bet != max_bet:
            player.folded = True

        self.switch_player()
        self.view.update_buttons()
        await self.message.edit(embed=self.build_embed(), view=self.view)

    async def start_timer(self, player: Player) -> None:
        """A timer that runs out if the current player takes too long. (130 seconds)"""
        timer: int = 0
        while timer < 120:
            if self.state != TableState.RUNNING:
                return

            await asyncio.sleep(1)
            timer += 1

            if self.players[self.player_index] != player:
                return

            if timer == 100:
                await self.message.edit(embed=self.build_embed(with_autoplay=True))

        await self.autoplay(player)

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

    def _hand_strength_analysis(self, res_arr: np.ndarray[RankingItem]) -> dict:
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
            hand_type, hand_freq = np.unique((res_arr // 16 ** 5)[:, player], return_counts=True)
            final_hand_dict[player + 1] = dict(
                zip(np.vectorize(NAMED_HAND.get)(hand_type),
                    np.round(hand_freq / hand_freq.sum() * 100, 2).astype(float)))
        return final_hand_dict

    def _simulation_analysis(self, odds_type: Literal['win_any', 'tiw_win', 'precise'], res_arr: np.ndarray) -> dict:
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

    def _simulate_calculation(self, community_cards: np.ndarray, undrawn_combos: np.ndarray) -> np.ndarray[RankingItem]:
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
        res_arr: np.ndarray[RankingItem] = np.zeros(  # type: ignore
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
            community_cards: np.ndarray,
            player: int,
            undrawn_combos: np.ndarray,
            res_arr: np.ndarray[RankingItem]
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
            odds_type: Literal['win_any', 'tiw_win', 'precise'] = 'tie_win',
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

    # Embed Builder

    def build_embed(self, with_autoplay: bool = False) -> discord.Embed:
        """Builds the embed for the table"""
        embed = discord.Embed(title='Poker • Texas Hold\'em', color=helpers.Colour.white())
        embed.description = (
            '*Waiting for players to join...*\n\n' if self.state == TableState.PREPARED else ''
        )

        embed.description += (
            f'**Small Blind:** `{self.small_blind}`\n'
            f'**Big Blind:** `{self.big_blind}`\n\n'
            f'**Pot:** {Emojis.Economy.coin} `{self.pot}`\n'
        )

        for i, side_pot in enumerate(self.side_pots, start=1):
            embed.description += f'**Side Pot *#{i}*:** {Emojis.Economy.coin} `{side_pot}`\n'

        if self.state == TableState.STOPPED:
            self._build_stopped_embed(embed)
        else:
            self._build_running_embed(embed, with_autoplay)

        return embed

    def _build_stopped_embed(self, embed: discord.Embed) -> None:
        embed.colour = discord.Color.lighter_grey()
        embed.description = (
            '*Waiting for players to join...*\n\n'
            f'Poker requires `2-4` players. The small blind and big blind are set to `{self.small_blind}` and `{self.big_blind}` Chips.\n'
            f'The minimum buy-in is `{self.min_buy_in}` Chips and the maximum buy-in is `{self.max_buy_in}` Chips.\n'
            'You can join the game by clicking the **Join** button below or click **Start** as the host to start the game.'
        )
        embed.set_footer(text=f'Players: {len(self.players)}/4')
        self._add_players_raw_to_embed(embed)

    def _build_running_embed(self, embed: discord.Embed, with_autoplay: bool = False) -> None:
        for index, player in enumerate(self.players, 1):
            name_parts = [f'Seat #{index}', player.member.display_name]
            text = f'**Stack:** {Emojis.Economy.coin} `{player.stack}`\n'

            if self.state == TableState.RUNNING:
                if index - 1 == self.player_index:
                    name_parts.insert(0, Emojis.Arrows.right)

                blind = 'BB' if index == self.blind_index[1] + 1 else 'SB' if index == self.blind_index[0] + 1 else None
                if blind is not None:
                    name_parts.append(blind)

                text += f'**Current Bet:** {Emojis.Economy.coin} `{player.bet}`\n'

                if self.players[self.player_index] == player and with_autoplay:
                    text += f'*\N{ALARM CLOCK} Autoplay {discord.utils.format_dt(
                        discord.utils.utcnow() + datetime.timedelta(seconds=20), 'R')}*\n'

            if player.all_in:
                name_parts.append('All In')

            if player.folded:
                name_parts.append('Folded')
            else:
                if self.state == TableState.FINISHED:
                    won_lost_chips = f'+{sum(pot.amount // len(winners) for winners, pot in self.winners if player in winners)}'
                    if won_lost_chips == '+0':
                        won_lost_chips = f'-{player.bet}'

                    name_parts.append(f'{Emojis.Economy.coin} {won_lost_chips}')

                # Check if there is only one player that has not folded,
                # if there is, he does not need to show his cards and wins automatically
                if len(self.playing_players) != 1:
                    text = self._append_finished_embed_text(player, text)
                else:
                    name_parts.append('👑')

            embed.add_field(name=' • '.join(name_parts), value=text, inline=False)

        self._add_community_cards_to_embed(embed)

    def _add_players_raw_to_embed(self, embed: discord.Embed) -> None:
        for index, player in enumerate(self.players, 1):
            name_parts = [f'Seat #{index}', player.member.display_name]
            text = f'**Stack:** {Emojis.Economy.coin} `{player.stack}`\n'

            embed.add_field(name=' • '.join(name_parts), value=text, inline=False)

    def _append_finished_embed_text(self, player: Player, text: str) -> str:
        if self.state == TableState.FINISHED:
            cards = [card.display('small') for card in player.hand.cards]
            _, hand = discord.utils.find(lambda x: x[0] == player, self.ranks)  # type: _, HandResult
            position = self.ranks.index((player, hand)) + 1

            hand_suffix = (
                f'**{number_suffix(position)} Best Hand** 👑' if position == 1 else f'{number_suffix(position)} Best Hand'
                if not self.tie else '**Tie**'
            )
            text += (
                f'{cards[0].top} {cards[1].top} {hand.name}\n'
                f'{cards[0].bottom} {cards[1].bottom} {hand_suffix}'
            )
        return text

    def _add_community_cards_to_embed(self, embed: discord.Embed) -> None:
        cards = [Card.from_arr(arr) for arr in self.community_arr]
        if len(cards) >= 3:
            card_list = [f'{elem1} {elem2} {elem3}' for elem1, elem2, elem3 in zip(
                *[card.display('large', formatted=True).split('\n') for card in cards[:3]])]
            embed.add_field(
                name='The Flop',
                value='\n'.join(card_list)
            )
        if len(cards) >= 4:
            embed.add_field(
                name='The Turn',
                value=cards[3].display('large', formatted=True)
            )
        if len(cards) == 5:
            embed.add_field(
                name='The River',
                value=cards[4].display('large', formatted=True)
            )


class RaiseBetModal(discord.ui.Modal, title='Bet/Raise'):
    amount = discord.ui.TextInput(
        label='Amount', placeholder='Enter the amount you want to raise by', min_length=1, max_length=10)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction
        self.stop()


class BuyInModal(discord.ui.Modal, title='Buy-In'):
    amount = discord.ui.TextInput(label='Amount', min_length=1, max_length=10)

    def __init__(self, table: TexasHoldem) -> None:
        super().__init__(timeout=100.)
        self.amount.placeholder = f'Enter your buy-in amount. (Min: {table.min_buy_in}, Max: {table.max_buy_in})'

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction
        self.stop()


class SetBlindsModal(discord.ui.Modal, title='Set Custom Big Blind'):
    big_blind = discord.ui.TextInput(label='Big Blind', min_length=1, max_length=10)

    def __init__(self, min_blind: int, max_blind: int) -> None:
        super().__init__(timeout=100.)
        self.big_blind.placeholder = f'Enter the big blind amount. (Min: {min_blind}, Max: {max_blind})'

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction
        self.stop()


class TableView(View):
    """Represents a view for a blackjack table"""

    def __init__(self, table: TexasHoldem) -> None:
        self.table: TexasHoldem = table
        super().__init__(timeout=500.)

        self.update_buttons()

    async def on_timeout(self) -> None:
        """Called when the view times out."""
        for player in self.table.players:
            await self.table.remove_player(player.member)

        try:
            del self.table.cog.poker_tables[self.table.message.channel.id]
        except KeyError:
            pass

        with suppress(discord.HTTPException):
            await self.table.message.reply(f'{Emojis.error} The table has been closed due to inactivity.')
            await self.table.message.delete()

    # Button Updating

    def update_buttons(self) -> None:
        if self.table.state != TableState.RUNNING:
            self._update_buttons_not_running()
            return

        self._update_buttons_running()

    def _update_buttons_not_running(self) -> None:
        """Updates the buttons when the table is not running"""
        table = self.table
        self.clear_items()

        self.add_item(self.join)
        self.add_item(self.start_next_round)
        self.add_item(self.leave_button)

        stopped_or_prepared = table.state in (TableState.STOPPED, TableState.PREPARED)

        if stopped_or_prepared:
            self.add_item(self.set_blinds_button)

        self.start_next_round.label = 'Start' if stopped_or_prepared else 'Next Round'

        if table.state == TableState.FINISHED:
            self.add_item(self.analysis_button)
        if table.state == TableState.PREPARED:
            self.remove_item(self.analysis_button)

        if len(table.players) < 2:
            self.start_next_round.disabled = True
        else:
            self.start_next_round.disabled = False

        if len(table.players) == 4:
            self.join.disabled = True
        else:
            self.join.disabled = False

    def _update_buttons_running(self) -> None:
        """Updates the buttons when the table is running"""
        table = self.table

        RUNNING_BUTTONS = [
            self.join,  # disabled
            self.my_hand,
            self.start_next_round,  # disabled
            self.fold,
            self.check_call,
            self.raise_bet,
            self.all_in
        ]

        # check if buttons are in the view
        if any(button not in self.children for button in RUNNING_BUTTONS):
            self.clear_items()
            for button in RUNNING_BUTTONS:
                self.add_item(button)

        self.join.disabled = True
        if self.table.state == TableState.PREPARED:
            self.start_next_round.label = 'Start'
        else:
            self.start_next_round.label = 'Next Round'
        self.start_next_round.disabled = True

        # Big/Small Blind can't raise/bet in the first round
        is_first_round_and_blind = len(table.community_arr) == 0 and table.player_index in table.blind_index
        self.raise_bet.disabled = is_first_round_and_blind
        self.raise_bet.label = 'Bet' if all(player.bet <= table.big_blind for player in table.playing_players) else 'Raise'

        # Setting the check/call button
        is_check = table.players[table.player_index].bet == max([player.bet for player in table.players])
        call_amount = max([player.bet for player in table.players]) - table.players[table.player_index].bet
        self.check_call.label = 'Check' if is_check else f'Call ({call_amount} Chips)'
        self.check_call.emoji = None if is_check else Emojis.Economy.coin

        if not is_check and table.players[table.player_index].stack < call_amount:
            self.check_call.disabled = True
            self.check_call.style = discord.ButtonStyle.grey
        else:
            self.check_call.disabled = False
            self.check_call.style = discord.ButtonStyle.grey if is_check else discord.ButtonStyle.green

    # Buttons

    @discord.ui.button(label='Join', style=discord.ButtonStyle.grey)
    async def join(self, interaction: discord.Interaction, _) -> None:
        """Joins the table"""
        if self.table.state != TableState.STOPPED:
            return await interaction.response.send_message(f'{Emojis.error} The table is already running.', ephemeral=True)

        if interaction.user in [player.member for player in self.table.players]:
            return await interaction.response.send_message(f'{Emojis.error} You are already in the game.', ephemeral=True)

        modal = BuyInModal(table=self.table)
        await interaction.response.send_modal(modal)
        await modal.wait()
        with suppress(AttributeError):
            interaction = modal.interaction

        try:
            amount = int(modal.amount.value)
        except ValueError:
            return await interaction.response.send_message(f'{Emojis.error} Invalid amount.', ephemeral=True)

        balance: Balance = await interaction.client.db.get_user_balance(interaction.user.id, interaction.guild_id)
        if balance.cash < amount:
            return await interaction.response.send_message(
                f'{Emojis.error} You don\'t have enough **cash** money to buy yourself in.\n'
                f'You need at least {Emojis.Economy.coin} **{fnumb(self.table.min_buy_in)}**.',
                ephemeral=True)

        await balance.remove(cash=amount)
        self.table.add_player(interaction.user, stack=amount)

        if len(self.table.players) == 4:
            self.table.start()
            self = TableView(table=self.table)  # noqa[override]

        self.update_buttons()
        await interaction.response.edit_message(embed=self.table.build_embed(), view=self)

    @discord.ui.button(label='My Hand', style=discord.ButtonStyle.blurple)
    async def my_hand(self, interaction: discord.Interaction, _) -> None:
        """Shows the player's hand"""
        player = discord.utils.get(self.table.players, member=interaction.user)
        if not player:
            return await interaction.response.send_message(f'{Emojis.error} You are not in the game.', ephemeral=True)

        if self.table.state != TableState.RUNNING:
            return await interaction.response.send_message(
                f'{Emojis.error} The game has not started yet.', ephemeral=True)

        embed = discord.Embed(title='Your Cards', color=discord.Color.blurple())

        card_list = [f'{elem1} {elem2}' for elem1, elem2 in zip(
            *[card.display('large', formatted=True).split('\n') for card in player.hand.cards])]
        embed.description = '\n'.join(card_list)

        # Returns your best hand
        hand = player.hand.evaluate(self.table.community_arr)

        card_list = [
            card.display('large', formatted=True).split('\n') for card in hand.cards
        ]
        # Use zip_longest to handle different lengths of display elements in each card
        results = [
            ' '.join(filter(None, elems))  # filter(None) removes empty strings
            for elems in zip_longest(*card_list, fillvalue='')
        ]

        embed.description += f'\n\n**Your Best Hand: *{hand.name}* **\n' + '\n'.join(results)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label='Start', style=discord.ButtonStyle.green, disabled=True)
    async def start_next_round(self, interaction: discord.Interaction, _) -> None:
        """Starts the game"""
        await interaction.response.defer()

        if self.table.state == TableState.RUNNING:
            return await interaction.followup.send(f'{Emojis.error} The table is already running.', ephemeral=True)

        if interaction.user != self.table.host:
            return await interaction.followup.send(
                f'{Emojis.error} You are not the host of this table.\n'
                f'Please aks {self.table.host.mention} to start the game!', ephemeral=True)

        if len(self.table.players) < 2:
            return await interaction.followup.send(
                f'{Emojis.error} You need at least 2 players to start the game.', ephemeral=True)

        if self.start_next_round.label == 'Next Round':
            await self.table.prepare_next_game()
        else:
            self.table.view = self = TableView(table=self.table)  # noqa[override]
            self.table.start()

        self.update_buttons()
        await interaction.edit_original_response(embed=self.table.build_embed(), view=self)

    @discord.ui.button(label='Fold', style=discord.ButtonStyle.red, row=1)
    async def fold(self, interaction: discord.Interaction, _) -> None:
        """Folds the player's hand"""
        player = await self.get_player(interaction)
        if not player:
            return

        if self.table.running_autoplay_loop is not None:
            self.table.running_autoplay_loop.cancel()

        self.table.Fold()
        self.table.switch_player()

        self.update_buttons()
        await interaction.response.edit_message(embed=self.table.build_embed(), view=self)

    @discord.ui.button(label='Check', style=discord.ButtonStyle.grey, row=1)
    async def check_call(self, interaction: discord.Interaction, _) -> None:
        """Checks or calls"""
        player = await self.get_player(interaction)
        if not player:
            return

        if self.table.running_autoplay_loop is not None:
            self.table.running_autoplay_loop.cancel()

        max_bet = max([p.bet for p in self.table.players])
        if player.bet == max_bet:
            self.table.Check()
        else:
            if player.stack < max_bet - player.bet:
                return await interaction.response.send_message(
                    f'{Emojis.error} You don\'t have enough chips. You\'ll need to go **All-In**!', ephemeral=True)

            self.table.Call()

        self.table.switch_player()
        self.update_buttons()
        await interaction.response.edit_message(embed=self.table.build_embed(), view=self)

    @discord.ui.button(label='Raise', style=discord.ButtonStyle.blurple, row=1)
    async def raise_bet(self, interaction: discord.Interaction, _) -> None:
        """Raises the bet"""
        player = await self.get_player(interaction)
        if not player:
            return

        if self.table.running_autoplay_loop is not None:
            self.table.running_autoplay_loop.cancel()

        modal = RaiseBetModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        interaction = modal.interaction

        try:
            amount = int(modal.amount.value)
        except ValueError:
            return await interaction.response.send_message(f'{Emojis.error} Invalid amount.', ephemeral=True)

        if amount > player.stack:
            return await interaction.response.send_message(
                f'{Emojis.error} You don\'t have enough chips.', ephemeral=True)

        is_bet = all(player.bet <= self.table.big_blind for player in self.table.playing_players)
        if is_bet:
            if amount < self.table.big_blind:
                return await interaction.response.send_message(
                    f'You have to raise by at least the big blind (**{self.table.big_blind}** Chips).', ephemeral=True)
        else:
            # Raise must be at least twice the current bet
            previous_bet = max([player.bet for player in self.table.players])
            if amount < previous_bet * 2:
                return await interaction.response.send_message(
                    f'You have to raise by at least twice the current bet (**{previous_bet * 2}** Chips).',
                    ephemeral=True)

            if (previous_bet + amount) > player.stack:
                return await interaction.response.send_message(
                    f'{Emojis.error} You don\'t have enough chips.', ephemeral=True)

        # check if its all-in
        if amount == player.stack:
            self.table.AllIn()
        else:
            self.table.Raise(amount)

        self.table.switch_player(by_raise=True)

        self.update_buttons()
        await interaction.response.edit_message(embed=self.table.build_embed(), view=self)

    @discord.ui.button(label='All In', style=discord.ButtonStyle.red, row=1)
    async def all_in(self, interaction: discord.Interaction, _) -> None:
        """Goes all in"""
        player = await self.get_player(interaction)
        if not player:
            return

        if self.table.running_autoplay_loop is not None:
            self.table.running_autoplay_loop.cancel()

        self.table.AllIn()
        self.table.switch_player(by_raise=True)

        self.update_buttons()
        await interaction.response.edit_message(embed=self.table.build_embed(), view=self)

    async def get_player(self, interaction: discord.Interaction) -> Player | None:
        player = self.table.players[self.table.player_index]
        if not player:
            await interaction.response.send_message(f'{Emojis.error} You are not in the game.', ephemeral=True)
            return None

        if player.member != interaction.user:
            await interaction.response.send_message(f'{Emojis.error} It\'s not your turn.', ephemeral=True)
            return None

        return player

    @discord.ui.button(label='Show Analysis', style=discord.ButtonStyle.blurple, emoji='\N{BAR CHART}', row=2)
    async def analysis_button(self, interaction: discord.Interaction, _) -> None:
        """Callback for the analysis button"""
        await interaction.response.defer()

        if self.table.state != TableState.FINISHED:
            return await interaction.followup.send(
                f'{Emojis.error} The table is currently running, please wait till the game is finished.',
                ephemeral=True)

        embed = discord.Embed(title='Game Odds Analysis', color=helpers.Colour.white())
        data: list[tuple[dict[str, float], dict[int, dict[str, float]]]] = self.table.analysis

        embeds, files = [], []
        for index, player in enumerate(self.table.players):
            embed = embed.copy()
            d_index = index + 1

            embed.set_author(name=f'{player.member.display_name} | Seat #{d_index}', icon_url=player.member.display_avatar.url)
            embed.description = (
                'This Analyis shows the odds of winning for each player at each stage of the game.'
                'The River is not included as the game is already over and nothing more to predict.\n\n'
            )

            match len(data):
                case 1:
                    embed.description += f'Pre-Flop: Win: **{data[0][0][f"Player {d_index} Win"]}**% | Tie: **{data[0][0][f"Player {d_index} Tie"]}**%\n'
                case 2:
                    embed.description += f'Pre-Flop: Win: **{data[0][0][f"Player {d_index} Win"]}**% | Tie: **{data[0][0][f"Player {d_index} Tie"]}**%\n'
                    embed.description += f'Flop: Win: **{data[1][0][f"Player {d_index} Win"]}**% | Tie: **{data[1][0][f"Player {d_index} Tie"]}**%\n'
                case 3:
                    embed.description += f'Pre-Flop: Win: **{data[0][0][f"Player {d_index} Win"]}**% | Tie: **{data[0][0][f"Player {d_index} Tie"]}**%\n'
                    embed.description += f'Flop: Win: **{data[1][0][f"Player {d_index} Win"]}**% | Tie: **{data[1][0][f"Player {d_index} Tie"]}**%\n'
                    embed.description += f'Turn: Win: **{data[2][0][f"Player {d_index} Win"]}**% | Tie: **{data[2][0][f"Player {d_index} Tie"]}**%'
                case _:
                    embed.description += '***NO DATA***'

            TITLE_MAP = {
                0: f'Seat #{d_index} - Hand Strength Analysis | Pre-Flop',
                1: 'Flop',
                2: 'Turn'
            }
            images: list[Image] = []
            for i in range(len(data)):
                chart = BarChart(
                    data=dict(dict((data[i][1][d_index]).items()).items()),
                    title=TITLE_MAP.get(i, '---'),
                )
                images.extend(chart.render(byted=False))

            image = BarChart._merge_and_render(images)

            embed.set_image(url=f'attachment://bar_chart-{index}.png')
            embeds.append(embed)
            files.append(image)

        await interaction.followup.send(embeds=embeds, files=files, ephemeral=True)

    @discord.ui.button(label='Leave', style=discord.ButtonStyle.red)
    async def leave_button(self, interaction: discord.Interaction, _) -> None:
        """Callback for the leave button"""
        if self.table.state == TableState.RUNNING:
            return await interaction.response.send_message(
                f'{Emojis.error} The table is currently running, please wait till the game is finished.',
                ephemeral=True)

        if interaction.user not in [player.member for player in self.table.players]:
            return await interaction.response.send_message(f'{Emojis.error} You are not in the game.', ephemeral=True)

        await self.table.remove_player(interaction.user)

        if len(self.table.players) == 1:
            self.table.state = TableState.STOPPED
        elif len(self.table.players) == 0:
            try:
                del self.table.cog.poker_tables[self.table.ctx.channel.id]
            except KeyError:
                pass
            await self.table.message.delete()

            return await interaction.response.send_message(
                '\N{LEAF FLUTTERING IN WIND} The Poker Table has been closed due to all players leaving.',
                delete_after=10)

        self.update_buttons()
        await interaction.response.edit_message(embed=self.table.build_embed(), view=self)
        await self.table.message.reply(
            f'\N{LEAF FLUTTERING IN WIND} {interaction.user.mention} has left the table.', delete_after=10)

    @discord.ui.button(label='Set Blinds', style=discord.ButtonStyle.blurple, row=1)
    async def set_blinds_button(self, interaction: discord.Interaction, _) -> None:
        """Callback for the set blinds button"""
        if self.table.state == TableState.RUNNING:
            return await interaction.response.send_message(
                f'{Emojis.error} The table is currently running, please wait till the game is finished.',
                ephemeral=True)

        if interaction.user != self.table.host:
            return await interaction.response.send_message(
                f'{Emojis.error} You are not the host of this table.\n'
                f'Please ask {self.table.host.mention} to set the blinds!', ephemeral=True)

        min_blind = max(1, int(self.table.first_buy_in * 0.005))  # 0.5% of the buy-in
        max_blind = int(self.table.first_buy_in * 0.05)  # 5% of the buy-in

        modal = SetBlindsModal(min_blind, max_blind)
        await interaction.response.send_modal(modal)
        await modal.wait()
        interaction = modal.interaction

        try:
            big_blind = int(modal.big_blind.value)
        except ValueError:
            return await interaction.response.send_message(f'{Emojis.error} Invalid bid/small blind.', ephemeral=True)

        if big_blind < min_blind or big_blind > max_blind:
            return await interaction.response.send_message(
                f'{Emojis.error} The big blind must be between **{min_blind}** and **{max_blind}**.', ephemeral=True)

        self.table.big_blind = big_blind
        self.table.small_blind = big_blind // 2

        self.update_buttons()
        await interaction.response.edit_message(embed=self.table.build_embed(), view=self)
