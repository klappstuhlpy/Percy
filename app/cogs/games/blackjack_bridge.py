"""Discord bridge for the pure blackjack engine.

:class:`Blackjack` owns the Discord-facing concerns the engine deliberately does not:
the invoking context, the view, embed rendering from the engine's state and the session
lifecycle. The cog and UI talk to the engine through this bridge (which exposes the
engine as ``self.engine`` and proxies the state the renderer / view needs).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from app.cogs.games.blackjack_ui import TableView
from app.cogs.games.engine.blackjack import BlackjackGame, WinningType
from app.utils import fnumb, helpers
from config import Emojis

if TYPE_CHECKING:
    from app.cogs.games.engine.cards import Deck
    from app.cogs.games.engine.blackjack import Hand
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

    # -- Lifecycle -----------------------------------------------------------------

    def wake_up(self, ctx: Context, bet: int) -> Blackjack:
        """Starts a fresh round with the same number of decks, returning a new bridge."""
        return Blackjack(ctx, bet, decks=self.deck.decks)

    # -- Rendering -----------------------------------------------------------------

    def build_container(
        self,
        view: TableView,
        hand: Hand,
        colour: discord.Colour = helpers.Colour.white(),
        text: str | None = None,
        image_url: str | None = None,
        with_buttons: bool = True,
    ) -> discord.ui.Container:
        """Build the Components V2 card for the blackjack table."""
        container = discord.ui.Container(accent_colour=colour)
        description = text or f"Your Bet: {Emojis.Economy.cash} **{fnumb(hand.bet)}**"
        container.add_item(discord.ui.TextDisplay(f"## Blackjack\n{description}"))

        if image_url:
            container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(image_url)))
            return container

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay("**Dealer Hand**\n" + "\n".join(self.dealer.display_blocks)))
        container.add_item(discord.ui.Separator())

        name = "Your Hand"
        if len(self.player_hands) > 1:
            name += f" #{self.player_hands.index(hand) + 1}"
        container.add_item(discord.ui.TextDisplay(f"**{name}**\n" + "\n".join(hand.display_blocks)))

        if with_buttons:
            container.add_item(discord.ui.ActionRow(view.hit, view.stand, view.double_down, view.split))

        if colour == discord.Colour.blurple():
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(f"-# Cards remaining: {len(self.deck)}"))

        if with_buttons:
            container.add_item(discord.ui.Separator())
            row = discord.ui.ActionRow()
            row.add_item(view.help)

            if view._game_over:
                row.add_item(view.NewGameButton(view.table))

            container.add_item(row)

        return container
