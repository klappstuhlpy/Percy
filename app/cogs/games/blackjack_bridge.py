"""Discord bridge for the pure blackjack engine.

:class:`Blackjack` owns the Discord-facing concerns the engine deliberately does not:
the invoking context, the view, embed rendering from the engine's state and the session
lifecycle. The cog and UI talk to the engine through this bridge (which exposes the
engine as ``self.engine`` and proxies the state the renderer / view needs).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord

from app.cogs.games.blackjack_ui import TableView
from app.cogs.games.engine.blackjack import BlackjackGame, WinningType
from app.utils import fnumb, helpers
from config import Emojis

if TYPE_CHECKING:
    from app.cogs.games.engine.blackjack import Hand
    from app.cogs.games.engine.cards import Deck
    from app.core import Context

__all__ = (
    "Blackjack",
    "BlackjackGame",
    "WinningType",
)


class Blackjack:
    """Bridges a :class:`~app.cogs.games.engine.blackjack.BlackjackGame` engine to Discord."""

    def __init__(self, ctx: Context, bet: int, decks: int = 1) -> None:
        self.ctx: Context = ctx
        self.engine: BlackjackGame = BlackjackGame(bet, decks=decks)
        self.view: TableView = TableView(table=self)
        self.message: Any = None  # The Discord message showing the game

    def __repr__(self) -> str:
        return f"Blackjack(ctx={self.ctx}, decks={self.deck.decks} dealer={self.dealer})"

    # -- Engine state proxies ------------------------------------------------------

    @property
    def deck(self) -> Deck:
        return self.engine.deck

    @property
    def dealer(self) -> Hand:
        return self.engine.dealer

    @property
    def player_hands(self) -> list[Hand]:
        return self.engine.player_hands

    @property
    def active_hand(self) -> Hand:
        return self.engine.active_hand

    @property
    def is_running(self) -> bool:
        return self.engine.is_running

    @property
    def playing_players(self) -> bool:
        return self.engine.playing_players

    def hit(self, hand: Hand) -> None:
        self.engine.hit(hand)

    def stand(self) -> None:
        self.engine.stand()

    def advance_hand(self) -> Hand:
        return self.engine.advance_hand()

    def split(self) -> Hand:
        return self.engine.split()

    def get_winner(self, hand: Hand) -> WinningType | None:
        return self.engine.get_winner(hand)

    def surrender(self) -> None:
        self.engine.surrender()

    def take_insurance(self, amount: int) -> None:
        self.engine.take_insurance(amount)

    @property
    def can_offer_insurance(self) -> bool:
        return self.engine.can_offer_insurance

    @property
    def dealer_shows_ace(self) -> bool:
        return self.engine.dealer_shows_ace

    @property
    def hole_card_hidden(self) -> bool:
        return self.engine.hole_card_hidden

    def dealer_has_blackjack(self) -> bool:
        return self.engine.dealer_has_blackjack()

    # -- Lifecycle -----------------------------------------------------------------

    def wake_up(self, ctx: Context, bet: int) -> Blackjack:
        """Starts a fresh round with the same number of decks, returning a new bridge."""
        return Blackjack(ctx, bet, decks=self.deck.decks)

    # -- Rendering -----------------------------------------------------------------

    def build_container(
        self,
        view: TableView,
        colour: discord.Colour = helpers.Colour.white(),
        text: str | None = None,
        image_url: str | None = None,
        with_buttons: bool = True,
    ) -> discord.ui.Container:
        """Build the Components V2 card for the blackjack table showing all hands."""
        container = discord.ui.Container(accent_colour=colour)
        active = self.active_hand

        # Header with bet info
        if text:
            description = text
        else:
            total_bet = sum(h.bet for h in self.player_hands)
            total_insurance = sum(h.insurance_bet for h in self.player_hands)
            description = f"Your Bet: {Emojis.Economy.cash} **{fnumb(total_bet)}**"
            if total_insurance > 0:
                description += f" | Insurance: {Emojis.Economy.cash} **{fnumb(total_insurance)}**"
        container.add_item(discord.ui.TextDisplay(f"## Blackjack\n{description}"))

        if image_url:
            container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(image_url)))
            return container

        # Dealer hand
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay("**Dealer Hand**\n" + "\n".join(self.dealer.display_blocks)))
        container.add_item(discord.ui.Separator())

        # Player hands - show all with active indicator
        for i, hand in enumerate(self.player_hands):
            is_active = hand is active and not hand.finished
            hand_num = f" #{i + 1}" if len(self.player_hands) > 1 else ""
            indicator = "### " if is_active else "-# "
            status = ""
            if hand.finished and not view._game_over:
                status = " *(waiting)*"
            name = f"{indicator} Your Hand{hand_num}{status}"
            container.add_item(discord.ui.TextDisplay(f"{name}\n" + "\n".join(hand.display_blocks)))

        # Action buttons (only for active hand)
        if with_buttons and not view._game_over:
            container.add_item(discord.ui.ActionRow(view.hit, view.stand, view.double_down, view.split))
            # Second row: conditionally show Insurance/Surrender only when applicable
            second_row = discord.ui.ActionRow()
            # Insurance: only when dealer shows Ace, first action, not already taken
            show_insurance = (
                self.dealer_shows_ace
                and len(active) == 2
                and not active.splitted
                and active.insurance_bet == 0
                and not active.finished
            )
            if show_insurance:
                second_row.add_item(view.insurance)
            # Late Surrender: only on first action (2 cards), not split, dealer doesn't have blackjack
            show_surrender = (
                len(active) == 2
                and not active.splitted
                and not active.finished
                and not self.dealer_has_blackjack()
            )
            if show_surrender:
                second_row.add_item(view.surrender_btn)
            second_row.add_item(view.help)
            container.add_item(second_row)

        if colour == discord.Colour.blurple():
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(f"-# Cards remaining: {len(self.deck)}"))

        if with_buttons and view._game_over:
            container.add_item(discord.ui.Separator())
            row = discord.ui.ActionRow()
            row.add_item(view.help)
            row.add_item(view.NewGameButton(view.table))
            container.add_item(row)

        return container
