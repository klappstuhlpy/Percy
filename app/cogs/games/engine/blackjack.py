"""Pure blackjack engine.

Contains the complete game state and rules for blackjack: the deck, the dealer and
player hands, hand value calculation (with ace adjustment), the dealer drawing rules,
splitting, and winner determination. It has **no** ``discord`` imports and performs no
IO -- mirroring the ``poker`` engine, it builds on the discord-free card primitives in
``app.cogs.games._classes``.

``Hand.message`` is an opaque slot the :class:`~app.cogs.games._blackjack.Blackjack`
bridge uses to remember which Discord message renders the hand; the engine never touches
it. Rendering (embeds), the economy payouts and the views live in that bridge / the
``app.cogs.games.blackjack_ui`` module.
"""

from __future__ import annotations

import enum
from itertools import zip_longest
from typing import Any

import numpy as np

from app.cogs.games.engine.cards import BaseHand, Deck

__all__ = (
    "BlackjackGame",
    "Hand",
    "WinningType",
)


class WinningType(enum.Enum):
    """Enum for the winning type of a hand"""

    PLAYER_WIN = "Player Win"
    PLAYER_BUST = "Player Bust"
    PLAYER_BLACKJACK = "Player Blackjack"

    DEALER_WIN = "Dealer Win"
    DEALER_BUST = "Dealer Bust"
    DEALER_BLACKJACK = "Dealer Blackjack"

    PUSH = "Push"
    SURRENDER = "Surrender"


class Hand(BaseHand):
    """Represents a hand of cards for a blackjack game"""

    def __init__(self, bet: int) -> None:
        super().__init__()
        self.bet: int = bet
        # Opaque slot owned by the Discord bridge -- the engine never reads or writes it.
        self.message: Any = None

        self.finished: bool = False
        self.splitted: bool = False
        self.surrendered: bool = False
        self.insurance_bet: int = 0
        self._hidden_indices: set[int] = set()

    @property
    def cards(self) -> list:
        """Returns a list of cards with hidden state preserved."""
        from app.cogs.games.engine.cards import BaseCard

        result = []
        for i, (value, suit) in enumerate(self.card_arr):
            card = BaseCard(suit=suit, value=value)
            if i in self._hidden_indices:
                card.hidden = True
            result.append(card)
        return result

    def set_card_hidden(self, index: int, hidden: bool) -> None:
        """Set the hidden state of a card at the given index."""
        if hidden:
            self._hidden_indices.add(index)
        else:
            self._hidden_indices.discard(index)

    def get_real_card_values(self, include_hidden: bool = False) -> list[int]:
        """Because the card values for Jack, King Queen and Ace are 11, 12, 13 and 14, we now need to
        translate them for the blackjack game into the actual values they represent."""
        values = []
        for i, (value, _suit) in enumerate(self.card_arr):
            if i in self._hidden_indices and not include_hidden:
                continue
            if value >= 10:
                if value == 14:
                    values.append(11)  # Ace
                else:
                    values.append(10)  # Face cards
            else:
                values.append(value)
        return values

    @property
    def value(self) -> int:
        """Gets the value of the hand"""
        _sum = sum(self.get_real_card_values())

        # Check and adjust for aces
        if _sum > 21:
            for card in self.cards:
                if card.value == 14:  # Ace
                    card.value = 1
                if _sum <= 21:
                    break

        # Check and adjust for aces after a split
        if len(self) == 2 and _sum < 21:
            for card in self.cards:
                if card.value == 1:  # Ace
                    card.value = 14

        return sum(self.get_real_card_values())

    @property
    def display_text(self) -> str:
        """Gets the display text for the hand"""
        card_list = [card.display("large", formatted=True).split("\n") for card in self.cards]
        # Use zip_longest to handle different lengths of display elements in each card
        results = [
            " ".join(filter(None, elems))  # filter(None) removes empty strings
            for elems in zip_longest(*card_list, fillvalue="")
        ]
        return "\n".join(results) + f"\n\nValue: `{self.value}`"

    @property
    def display_blocks(self) -> list[str]:
        """Returns display text split by whole cards (safe chunks).
        The hand value is appended ONLY to the first block.
        """
        card_blocks = [card.display("large", formatted=True).split("\n") for card in self.cards]

        value_suffix = f"\n\nValue: `{self.value}`"
        value_len = len(value_suffix)

        blocks: list[str] = []
        current_cards: list[list[str]] = []

        def render(cards: list[list[str]]) -> str:
            return "\n".join(" ".join(row) for row in zip(*cards))

        for card in card_blocks:
            rendered = render([*current_cards, card]) if current_cards else render([card])

            # only reserve space for the value in the FIRST block
            limit = 1024 - value_len if not blocks else 1024

            if len(rendered) > limit:
                blocks.append(render(current_cards))
                current_cards = [card]
            else:
                current_cards.append(card)

        if current_cards:
            blocks.append(render(current_cards))

        # append value ONLY to first block
        blocks[0] += value_suffix

        return blocks


class BlackjackGame:
    """The pure blackjack game state and rules: the deck, the dealer and player hands,
    dealing, hitting, standing (with the dealer drawing rules), splitting and winner
    determination. No Discord, no IO."""

    def __init__(self, bet: int, decks: int = 1) -> None:
        self.deck: Deck = Deck(game="blackjack", decks=decks)

        self.dealer: Hand = Hand(bet=bet)
        self.player_hands: list[Hand] = [Hand(bet=bet)]

        self.active_hand: Hand = self.player_hands[0]

        self.deal()

    def __repr__(self) -> str:
        return f"BlackjackGame(decks={self.deck.decks} dealer={self.dealer})"

    @property
    def is_running(self) -> bool:
        """Checks if the game is running"""
        return not all(hand.finished for hand in self.player_hands)

    @property
    def playing_players(self) -> bool:
        """Checks if there are any players that are still playing"""
        return any(not hand.finished for hand in self.player_hands)

    def deal(self) -> None:
        """Deals the cards to the players and the dealer"""
        for _ in range(2):
            self.active_hand.add(self.deck.draw())
            self.dealer.add(self.deck.draw())

        # Sets the dealer's second card (hole card) to hidden
        self.dealer.set_card_hidden(1, True)

    def hit(self, hand: Hand) -> None:
        """Hits a hand."""
        hand.add(self.deck.draw())

    def advance_hand(self) -> Hand:
        """Set the active hand to the next unfinished player hand and return it.

        Only called while :attr:`playing_players` is true, so a non-finished hand is
        guaranteed to exist.
        """
        self.active_hand = next(hand for hand in self.player_hands if not hand.finished)
        return self.active_hand

    def stand(self) -> None:
        """Stands the active hand."""
        self.active_hand.finished = True

        if self.playing_players:
            return

        self.dealer.set_card_hidden(1, False)

        if len(self.player_hands) == 1 and self.player_hands[0].value > 21:
            return
        if len(self.player_hands) == 1 and self.player_hands[0].value == 21 and len(self.player_hands[0]) == 2:
            return

        while self.dealer.value <= 16:
            self.hit(self.dealer)

    def split(self) -> Hand:
        """Splits the active hand into two, drawing a new card for each. Returns the new hand."""
        new_hand = Hand(bet=self.active_hand.bet)
        new_hand.splitted = True
        self.active_hand.splitted = True

        # Get the left card and add it to the second hand and draw a new card for both hands
        hand = self.active_hand.card_arr
        card = hand[1]
        new_hand.add(np.array([[card[0], card[1]]]))
        self.active_hand.card_arr = np.delete(hand, 1, 0)

        self.hit(new_hand)
        self.hit(self.active_hand)

        self.player_hands.append(new_hand)
        return new_hand

    def surrender(self) -> None:
        """Surrenders the active hand, forfeiting half the bet."""
        self.active_hand.surrendered = True
        self.active_hand.finished = True

    def take_insurance(self, amount: int) -> None:
        """Places an insurance side-bet (up to half the original bet)."""
        self.active_hand.insurance_bet = amount

    @property
    def dealer_shows_ace(self) -> bool:
        """Returns True if the dealer's face-up card (first card) is an Ace."""
        if not self.dealer.cards:
            return False
        return self.dealer.cards[0].value == 14

    @property
    def can_offer_insurance(self) -> bool:
        """Insurance can be offered only at the start when dealer shows an Ace."""
        return (
            self.dealer_shows_ace
            and len(self.active_hand) == 2
            and not self.active_hand.splitted
            and self.active_hand.insurance_bet == 0
        )

    @property
    def hole_card_hidden(self) -> bool:
        """Returns True if the dealer's hole card is still hidden."""
        return 1 in self.dealer._hidden_indices

    def get_winner(self, hand: Hand) -> WinningType | None:
        """Gets a potential winner for the game.

        This implements logic for a player/dealer blackjack, player/dealer win, player/dealer bust.
        Also for events like both having a blackjack, both having the same value etc.
        Also checks that requirements for a blackjack are met.
        """
        if hand.surrendered:
            return WinningType.SURRENDER

        player_value = hand.value
        dealer_value = self.dealer.value

        if player_value == 21 and len(hand) == 2:
            if dealer_value == 21 and len(self.dealer) == 2:
                return WinningType.PUSH
            if hand.splitted:
                return WinningType.PLAYER_WIN
            return WinningType.PLAYER_BLACKJACK
        elif dealer_value == 21 and len(self.dealer) == 2:
            return WinningType.DEALER_BLACKJACK

        if player_value > 21:
            return WinningType.PLAYER_BUST
        elif dealer_value > 21:
            return WinningType.DEALER_BUST

        if self.hole_card_hidden:
            if player_value == 21:
                return WinningType.PLAYER_WIN
        else:
            if player_value == dealer_value:
                return WinningType.PUSH
            elif player_value > dealer_value:
                return WinningType.PLAYER_WIN
            elif player_value < dealer_value:
                return WinningType.DEALER_WIN
        return None

    def dealer_has_blackjack(self) -> bool:
        """Returns True if the dealer has blackjack (used for insurance payout).
        Must check with include_hidden=True to see the full hand."""
        dealer_full_value = sum(self.dealer.get_real_card_values(include_hidden=True))
        return dealer_full_value == 21 and len(self.dealer) == 2
