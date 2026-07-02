from __future__ import annotations

import enum
import re
from collections import namedtuple
from typing import Literal, TypeVar

import numpy as np

from app.utils import RevDict
from config import Emojis

DisplayCard = namedtuple("DisplayCard", ["top", "middle", "bottom"])

_BASE_CARDS: dict[str, int] = {"2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "T": 10}
FULL_CARD_DECK: dict[str, int] = _BASE_CARDS | {"J": 11, "Q": 12, "K": 13, "A": 14}

SUITS: dict[str, int] = {"diamonds": 0, "clubs": 1, "spades": 2, "hearts": 3}
NAMED_HAND: dict[int, str] = {
    0: "High Card",
    1: "One Pair",
    2: "Two Pairs",
    3: "Three of a Kind",
    4: "Straight",
    5: "Flush",
    6: "Full House",
    7: "Four of a Kind",
    8: "Straight Flush",
    9: "Royal Flush",
}

LNAMED: dict[int, str] = {
    2: "2",
    3: "3",
    4: "4",
    5: "5",
    6: "6",
    7: "7",
    8: "8",
    9: "9",
    10: "10",
    11: "jack",
    12: "queen",
    13: "king",
    14: "ace",
}
UNAMED: dict[int, str] = {key: value.title() for key, value in LNAMED.items()} | {0: "None"}  # 0 is a placeholder


class MinimumBet(enum.IntEnum):
    BLACKJACK = 100
    ROULETTE = 100
    POKER = 500


class Payouts(enum.IntEnum):
    WORK_PAYOUT_MIN = 20
    WORK_PAYOUT_MAX = 250
    WORK_COODLWON = 7200  # 2 hours

    CRIME_PAYOUT_MIN = 250
    CRIME_PAYOUT_MAX = 700
    CRIME_FINE_MIN = 0.2  # 20%
    CRIME_FINE_MAX = 0.4  # 40%
    CRIME_FAIL_RATE = 0.6  # 60%
    CRIME_COOLDOWN = 86400  # 1 Day

    SLUT_PAYOUT_MIN = 100
    SLUT_PAYOUT_MAX = 400
    SLUT_FINE_MIN = 0.1  # 10%
    SLUT_FINE_MAX = 0.2  # 20%
    SLUT_FAIL_RATE = 0.35  # 35%
    SLUT_COODLWON = 14400  # 4 hours


def number_to_text(text: str) -> str:
    NUMBER_MAP = {
        "0": "zero",
        "1": "one",
        "2": "two",
        "3": "three",
        "4": "four",
        "5": "five",
        "6": "six",
        "7": "seven",
        "8": "eight",
        "9": "nine",
        "10": "ten",
    }
    NUMBER_REGEX = re.compile(r"\b(?:10|[1-9])")

    def replace(match: re.Match) -> str:
        number = match.group(0)
        return NUMBER_MAP.get(number, number)

    return NUMBER_REGEX.sub(replace, text)


class BaseCard:
    """Represents a card in a deck"""

    def __init__(self, value: int, suit: int) -> None:
        self.name: str = LNAMED.get(value, str(value))
        self.value: int = value
        self.suit: int = suit

        self.color: str = "red" if self.suit in (0, 3) else "black"
        self.hidden: bool = False

    def __repr__(self) -> str:
        return f"Card(name={self.name}, value={self.value}, suit={self.suit})"

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, BaseCard):
            return NotImplemented
        return self.value > other.value

    def display(self, size: Literal["small", "large"], formatted: bool = False) -> DisplayCard | str:
        """Render the card as stacked emoji rows.

        Returns the rows joined by newlines when ``formatted`` is set, otherwise a
        :class:`DisplayCard`. The ``small`` layout has two rows (its ``middle`` is
        ``None``); ``large`` has three.
        """
        cards = Emojis.Card

        if self.hidden:
            top = f"{cards.cardback_top1}{cards.cardback_top2}"
            bottom = f"{cards.cardback_bottom1}{cards.cardback_bottom2}"
            middle = f"{cards.cardback_middle}{cards.cardback_middle}" if size == "large" else None
        elif size == "small":
            suit_name = RevDict(SUITS)[self.suit]
            top = str(getattr(cards, number_to_text(f"{self.name}_{self.color}_nobottom")))
            bottom = str(getattr(cards, f"{suit_name}_notop"))
            middle = None
        else:
            suit = getattr(cards, RevDict(SUITS)[self.suit])
            top_emoji = getattr(cards, number_to_text(f"{self.name}_{self.color}_nobottomright"))
            bottom_emoji = getattr(cards, number_to_text(f"{self.name}_{self.color}_notopleft"))
            top = f"{top_emoji}{cards.blank_nobottomleft}"
            middle = f"{suit}{suit}"
            bottom = f"{cards.blank_notopright}{bottom_emoji}"

        if formatted:
            rows = (top, bottom) if middle is None else (top, middle, bottom)
            return "\n".join(rows)
        return DisplayCard(top=top, middle=middle, bottom=bottom)

    @property
    def display_text(self) -> str:
        """Returns the display text of the card"""
        return f"{self.name.title()} of {RevDict(SUITS)[self.suit].title()}"


CardT = TypeVar("CardT", bound=BaseCard)


class BaseHand[CardT: BaseCard]:
    """Represents a hand of cards"""

    def __init__(self) -> None:
        self.card_arr: np.ndarray = np.zeros(shape=(0, 2), dtype=int)

    def __repr__(self) -> str:
        return f"Hand(card_arr={len(self.card_arr)})"

    def __len__(self) -> int:
        return len(self.card_arr)

    def add(self, card: np.ndarray) -> None:
        """Adds a card to the hand, the card array must be a 2D array with the first dimension being 1"""
        self.card_arr = np.concatenate([self.card_arr, card], axis=0)

    @property
    def cards(self) -> list[CardT]:
        """Returns a list of cards formatted in the hand"""
        return [BaseCard(suit=suit, value=value) for value, suit in self.card_arr]


class Deck[CardT: BaseCard]:
    """Represents one or Card Decks with 52 cards (or more*) that can be shuffled and drawn from

    Parameters
    ----------
    game: Literal['blackjack', 'poker', 'basic']
        The game that the deck is being used for, important for the value of the Ace card.
        Basic is a generic deck type that is used by multiple other games and treats the Ace as 14.
    decks: int
        The number of decks to use, defaults to 1.
    card_cls: Type[C]
        The class to use for the cards, defaults to BaseCard.
    infinite: bool
        Whether the deck should be infinite, meaning that it will not run out of cards and will not be shuffled. Defaults to False.

    *: The number of cards in the deck can be more than 52 if the number of decks is greater than 1.
    """

    def __init__(
            self,
            game: Literal["blackjack", "poker", "basic"],
            decks: int = 1,
            card_cls: type[CardT] = BaseCard,
            infinite: bool = False
    ) -> None:
        self._card_cls: type[CardT] = card_cls
        self.game: Literal["blackjack", "poker", "basic"] = game
        self.infinite: bool = infinite

        self.decks: int = decks

        self.cards: np.ndarray = np.zeros(shape=(0, 2), dtype=int)
        self._build_deck()

    def __repr__(self) -> str:
        return f"Deck(decks={self.decks} cards={len(self.cards)})"

    def _build_deck(self) -> None:
        for _ in range(self.decks):
            self.cards = np.concatenate(
                [self.cards, np.array([[value, suit] for value in FULL_CARD_DECK.values() for suit in SUITS.values()])],
                axis=0,
            )

        self.shuffle()

    def shuffle(self) -> None:
        """Shuffles the deck"""
        np.random.shuffle(self.cards)

    def draw(self) -> np.ndarray:
        """Draws a card as a numpy array from the deck"""
        if len(self.cards) == 0:
            raise Exception("No cards left in the deck")

        card = self.cards[0]
        if not self.infinite:
            self.cards = np.delete(self.cards, 0, 0)
        else:
            self.cards = np.roll(self.cards, -1, 0)
        return np.array([[card[0], card[1]]])

    def __len__(self) -> int:
        return len(self.cards)
