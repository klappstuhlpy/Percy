from collections import namedtuple
from typing import Generic, TypeVar, Literal, Type

import discord
import numpy as np

from cogs.utils.constants import CARD_EMOJIS
from cogs.utils.formats import RevDict

CARD_PEMOJIS: dict[str, discord.PartialEmoji] = {
    name: discord.PartialEmoji(name=name, id=_id) for name, _id in CARD_EMOJIS.items()
}

DisplayCard = namedtuple('DisplayCard', ['top', 'middle', 'bottom'])

_BASE_CARDS: dict[str, int] = {'2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, 'T': 10}
POKER_NUM: dict[str, int] = _BASE_CARDS | {'J': 11, 'Q': 12, 'K': 13, 'A': 14}
BJ_NUM: dict[str, int] = _BASE_CARDS | {'J': 10, 'Q': 10, 'K': 10, 'A': 11}

SUITS: dict[str, int] = {'diamonds': 0, 'clubs': 1, 'spades': 2, 'hearts': 3}
NAMED_HAND: dict[int, str] = {
    0: 'High Card', 1: 'One Pair', 2: 'Two Pairs', 3: 'Three of a Kind', 4: 'Straight', 5: 'Flush',
    6: 'Full House', 7: 'Four of a Kind', 8: 'Straight Flush', 9: 'Royal Flush'}

LNAMED: dict[int, str] = {2: '2', 3: '3', 4: '4', 5: '5', 6: '6', 7: '7', 8: '8', 9: '9', 10: '10',
                          11: 'jack', 12: 'queen', 13: 'king', 14: 'ace'}
UNAMED: dict[int, str] = {key: value.title() for key, value in LNAMED.items()} | {0: 'None'}  # 0 is a placeholder


class BaseCard:
    """Represents a card in a deck"""

    def __init__(self, value: int, suit: int):
        self.name: str = LNAMED.get(value, str(value))
        self.value: int = value
        self.suit: int = suit

        self.color: str = 'red' if self.suit in (0, 3) else 'black'
        self.hidden: bool = False

    def __repr__(self):
        return f'Card(name={self.name}, value={self.value}, suit={self.suit})'

    def display(self, size: Literal['small', 'large'], formatted: bool = False) -> DisplayCard | str:
        if size == 'small':
            emojis = [
                CARD_PEMOJIS[f'{self.name}_{self.color}_nobottom'],
                CARD_PEMOJIS[f'{RevDict(SUITS)[self.suit]}_notop']
            ]
            return '\n'.join(map(str, emojis)) if formatted else DisplayCard(
                top=str(emojis[0]), middle=None, bottom=str(emojis[1])
            )
        else:
            top = [
                CARD_PEMOJIS[f'{self.name}_{self.color}_nobottomright'],
                CARD_PEMOJIS['blank_nobottomleft']
            ]
            middle = [CARD_PEMOJIS[RevDict(SUITS)[self.suit]]] * 2
            bottom = [
                CARD_PEMOJIS['blank_notopright'],
                CARD_PEMOJIS[f'{self.name}_{self.color}_notopleft']
            ]

            emojis = ["".join(map(str, top)), "".join(map(str, middle)), "".join(map(str, bottom))]
            return '\n'.join(emojis) if formatted else DisplayCard(top=emojis[0], middle=emojis[1],
                                                                   bottom=emojis[2])

    @property
    def display_text(self) -> str:
        """Returns the display text of the card"""
        return f'{self.name.title()} of {RevDict(SUITS)[self.suit].title()}'


C = TypeVar('C', bound=BaseCard)


class BaseHand(Generic[C]):
    """Represents a hand of cards"""

    def __init__(self):
        self.card_arr: np.ndarray = np.zeros(shape=(0, 2), dtype=int)

    def __repr__(self):
        return f'Hand(card_arr={len(self.card_arr)})'

    def __len__(self):
        return len(self.card_arr)

    def add(self, card: np.ndarray):
        """Adds a card to the hand, the card array must be a 2D array with the first dimension being 1"""
        self.card_arr = np.concatenate([self.card_arr, card], axis=0)

    @property
    def cards(self) -> list[C]:
        """Returns a list of cards formatted in the hand"""
        return [BaseCard(suit=suit, value=value) for value, suit in self.card_arr]


class Deck(Generic[C]):
    """Represents one or Card Decks with 52 cards (or more*) that can be shuffled and drawn from

    Parameters
    ----------
    game: Literal['blackjack', 'poker']
        The game that the deck is being used for, important for the value of the Ace card.
    decks: int
        The number of decks to use, defaults to 1.
    card_cls: Type[C]
        The class to use for the cards, defaults to BaseCard.

    *: The number of cards in the deck can be more than 52 if the number of decks is greater than 1.
    """

    def __init__(self, game: Literal['blackjack', 'poker'], decks: int = 1, card_cls: Type[C] = BaseCard):
        self._card_cls: Type[C] = card_cls
        self.game: Literal['blackjack', 'poker'] = game

        self.decks: int = decks

        self.cards: np.ndarray = np.zeros(shape=(0, 2), dtype=int)
        self._build_deck()

    def __repr__(self):
        return f'Deck(decks={self.decks} cards={len(self.cards)})'

    def _build_deck(self):
        _card_deck = POKER_NUM if self.game == 'poker' else BJ_NUM

        for _ in range(self.decks):
            self.cards = np.concatenate([
                self.cards,
                np.array([[value, suit] for value in _card_deck.values() for suit in SUITS.values()])
            ], axis=0)

        self.shuffle()

    def shuffle(self):
        """Shuffles the deck"""
        np.random.shuffle(self.cards)

    def draw(self) -> np.ndarray:
        """Draws a card as a numpy array from the deck"""
        if len(self.cards) == 0:
            raise Exception('No cards left in the deck')

        card = self.cards[0]
        self.cards = np.delete(self.cards, 0, 0)
        return np.array([[card[0], card[1]]])

    def __len__(self):
        return len(self.cards)
