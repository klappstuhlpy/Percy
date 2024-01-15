import enum
import random
from typing import Generic, TypeVar, Literal

import discord

from cogs.utils.constants import CARD_EMOJIS


CARD_EMOJIS_PARTIAL: dict[str, discord.PartialEmoji] = {
    name: discord.PartialEmoji(name=name, id=_id) for name, _id in CARD_EMOJIS.items()
}


class Suit(enum.Enum):
    """Enum for the suits of a card"""

    # The emojis are named like: "2ofspades, "queenofspades", etc.

    SPADES = 'spades'
    HEARTS = 'hearts'
    DIAMONDS = 'diamonds'
    CLUBS = 'clubs'


class BaseCard:
    """Represents a card in a deck"""

    def __init__(self, name: str, value: int, suit: Suit):
        self.name: str = name
        self.value: int = value
        self.suit: Suit = suit

        self.color: str = 'red' if self.suit in (Suit.HEARTS, Suit.DIAMONDS) else 'black'
        self.rl_name: str | int = self.value if self.name.isdigit() else self.name

    def __repr__(self):
        return f'Card(name={self.name}, value={self.value}, suit={self.suit})'

    def display(self, size: Literal["small", "large"]) -> str:
        """Returns the emoji representation of the card"""
        top, middle, bottom = [], [], []

        if size == 'small':
            top.append(CARD_EMOJIS_PARTIAL[f'{self.suit.value}_{self.color}_no_bottom'])
            bottom.append(CARD_EMOJIS_PARTIAL[f'{self.suit.name}_notop'])
        else:
            top.extend([CARD_EMOJIS_PARTIAL[f'{self.suit.value}_{self.color}_nobottomright'],
                        CARD_EMOJIS_PARTIAL['blank_nobottomleft']])
            middle.extend([CARD_EMOJIS_PARTIAL[f'{self.suit.name}']] * 2)
            bottom.extend([CARD_EMOJIS_PARTIAL['blank_notopright'],
                           CARD_EMOJIS_PARTIAL[f'{self.suit.value}_{self.color}_notopleft']])

        print(f"\n{''.join(map(str, top))}\n{''.join(map(str, middle))}\n{''.join(map(str, bottom))}")
        return f"\n{''.join(map(str, top))}\n{''.join(map(str, middle))}\n{''.join(map(str, bottom))}"


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


class Deck:
    """Represents one or Card Decks with 52 cards that can be shuffled and drawn from"""

    def __init__(self, decks: int = 1):
        self.decks: int = decks
        self.cards: list[BaseCard] = []
        self.used_cards: list[BaseCard] = []

        # Build the deck
        self._build_deck()

        # Burn the first card ;)
        self.draw()

    def __repr__(self):
        return f'Deck(decks={self.decks} cards={len(self.cards)} used_cards={len(self.used_cards)})'

    def _build_deck(self):
        """Builds the deck"""
        for _ in range(self.decks):
            for suit in Suit:
                for i in range(2, 11):
                    self.cards.append(BaseCard(name=str(i), value=i, suit=suit))

                for name, value in (('jack', 10), ('queen', 10), ('king', 10), ('ace', 11)):
                    self.cards.append(BaseCard(name=name, value=value, suit=suit))

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
