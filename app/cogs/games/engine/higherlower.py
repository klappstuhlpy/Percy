"""Pure engine for the Higher/Lower card game.

A single card is shown; the player bets whether the next draw is strictly higher
or lower. Each correct call multiplies the pot by the *fair* payout for that call
(``ranks / favourable``) shaved by a house edge, so safe calls add little and risky
calls add a lot. A wrong call (or a tie) busts the run. No Discord imports — fully
unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ('RANKS', 'GuessOdds', 'HigherLower')

from app.cogs.games.engine.cards import BaseHand, BaseCard, Deck

#: Card ranks, Ace high (2..14). Suits are irrelevant to the odds, so the engine
#: draws ranks with replacement (an "infinite deck"); a tie counts as a loss and
#: is what creates the natural house edge alongside :data:`HOUSE_EDGE`.
RANKS: list[int] = list(range(2, 15))
HOUSE_EDGE: float = 0.92


@dataclass(frozen=True)
class GuessOdds:
    """The favourable outcomes for a call out of :data:`RANKS`."""

    favorable: int
    total: int


class HigherLower:
    """Stateful single-run Higher/Lower game."""

    def __init__(self) -> None:
        self.cards: Deck = Deck(game="basic", infinite=True)
        self.hand: BaseHand = BaseHand()

        # Draw the first two cards, the first is shown and the second is hidden until the first guess.
        self.current: BaseCard = self.add_next_card()
        self.next: BaseCard = self.add_next_card(hidden=True)

        self.multiplier: float = 1.0
        self.rounds: int = 0
        self.busted: bool = False

    def add_next_card(self, hidden: bool = False) -> BaseCard:
        """Adds the next card to the hand and returns it."""
        nxt = self.cards.draw()
        self.hand.add(nxt)
        cards = self.hand.cards
        cards[-1].hidden = hidden
        return cards[-1]

    def odds(self, higher: bool) -> GuessOdds:
        """Strictly-favourable ranks for a higher/lower call from the current card."""
        if higher:
            favorable = sum(1 for rank in RANKS if rank > self.current.value)
        else:
            favorable = sum(1 for rank in RANKS if rank < self.current.value)
        return GuessOdds(favorable, len(RANKS))

    def step_multiplier(self, higher: bool) -> float:
        """The factor the pot grows by if this call is correct."""
        favorable = self.odds(higher).favorable
        if favorable == 0:
            return 0.0
        return (len(RANKS) / favorable) * HOUSE_EDGE

    def guess(self, higher: bool) -> tuple[BaseCard, bool]:
        """Draws the next card and resolves the call.

        Returns ``(next_rank, correct)``. On a correct call the multiplier grows
        and the new card becomes current; otherwise the run busts.
        """
        if self.busted:
            raise RuntimeError("cannot guess after busting")

        self.next.hidden = False
        correct = self.next > self.current if higher else self.next < self.current
        self.rounds += 1

        if correct:
            self.current = self.next
            self.next = self.add_next_card(hidden=True)

            self.multiplier *= self.step_multiplier(higher)
        else:
            self.busted = True
        return self.next, correct
