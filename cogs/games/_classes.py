import enum
import random
from collections import namedtuple
from typing import Generic, TypeVar, Literal, Type

import discord

from cogs.utils.constants import CARD_EMOJIS

CARD_EMOJIS_PARTIAL: dict[str, discord.PartialEmoji] = {
    name: discord.PartialEmoji(name=name, id=_id) for name, _id in CARD_EMOJIS.items()
}

DisplayCard = namedtuple('DisplayCard', ['top', 'middle', 'bottom'])


class Suit(enum.Enum):
    """Enum for the suits of a card"""

    # The emojis are named like: "2ofspades, "queenofspades", etc.

    SPADES = 'spades'
    HEARTS = 'hearts'
    DIAMONDS = 'diamonds'
    CLUBS = 'clubs'


class BaseCard:
    """Represents a card in a deck"""

    def __init__(self, name: str | int, value: int, suit: Suit):
        self.name: str = str(name)
        self.value: int = value
        self.suit: Suit = suit

        self.color: str = 'red' if self.suit in (Suit.HEARTS, Suit.DIAMONDS) else 'black'
        self.hidden: bool = False

    def __repr__(self):
        return f'Card(name={self.name}, value={self.value}, suit={self.suit})'

    def display(self, size: Literal["small", "large"], formatted: bool = False) -> DisplayCard | str:
        if size == 'small':
            emojis = [
                CARD_EMOJIS_PARTIAL[f'{self.name}_{self.color}_nobottom'],
                CARD_EMOJIS_PARTIAL[f'{self.suit.value}_notop']
            ]
            return '\n'.join(map(str, emojis)) if formatted else DisplayCard(
                top=str(emojis[0]), middle=None, bottom=str(emojis[1])
            )
        else:
            top = [
                CARD_EMOJIS_PARTIAL[f'{self.name}_{self.color}_nobottomright'],
                CARD_EMOJIS_PARTIAL['blank_nobottomleft']
            ]
            middle = [CARD_EMOJIS_PARTIAL[f'{self.suit.value}']] * 2
            bottom = [
                CARD_EMOJIS_PARTIAL['blank_notopright'],
                CARD_EMOJIS_PARTIAL[f'{self.name}_{self.color}_notopleft']
            ]

            emojis = ["".join(map(str, top)), "".join(map(str, middle)), "".join(map(str, bottom))]
            return '\n'.join(emojis) if formatted else DisplayCard(top=emojis[0], middle=emojis[1],
                                                                   bottom=emojis[2])


C = TypeVar('C')


class BaseHand(Generic[C]):
    """Represents a hand of cards"""

    def __init__(self):
        self.cards: list[C] = []

    def __repr__(self):
        return f'Hand(cards={len(self.cards)})'

    def __len__(self):
        return len(self.cards)

    def add(self, card: C):
        """Adds a card to the hand"""
        self.cards.append(card)


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
        self.cards: list[C] = []
        self.used_cards: list[C] = []

        # Build the deck
        self._build_deck()

        # Burn the first card ;)
        self.draw()

    def __repr__(self):
        return f'Deck(decks={self.decks} cards={len(self.cards)} used_cards={len(self.used_cards)})'

    def _build_deck(self):
        """Builds the deck"""
        poker_card_list = ('jack', 11), ('queen', 12), ('king', 13), ('ace', 14)
        blackjack_card_list = ('jack', 10), ('queen', 10), ('king', 10), ('ace', 11)

        for _ in range(self.decks):
            for suit in Suit:
                for i in range(2, 11):
                    self.cards.append(self._card_cls(name=i, value=i, suit=suit))

                for name, value in (poker_card_list if self.game == 'poker' else blackjack_card_list):
                    self.cards.append(self._card_cls(name=name, value=value, suit=suit))

        # Shuffle the deck
        self.shuffle()

    def shuffle(self):
        """Shuffles the deck"""
        random.shuffle(self.cards)

    def draw(self) -> C:
        """Draws a card from the deck"""
        card = self.cards.pop(0)
        self.used_cards.append(card)
        return card

    def __len__(self):
        return len(self.cards)
