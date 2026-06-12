from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord

from app.cogs.games.engine.blackjack import WinningType
from app.cogs.games.models import Game, GameResult
from app.core import Context, LayoutView
from app.utils import fnumb, helpers
from config import Emojis

if TYPE_CHECKING:
    from app.cogs.economy import Economy
    from app.cogs.games.blackjack_bridge import Blackjack
    from app.cogs.games.engine.blackjack import Hand
    from app.database.base import Balance

__all__ = ("TableView",)


class TableView(LayoutView):
    """The Components V2 view for the blackjack game (card + action controls).

    Because a CV2 message couples the card with its controls, the view is recomposed
    per hand right before each edit/send via :meth:`render`, so every hand's message
    shows its own card. The action buttons are stable instances whose enabled state is
    managed by :meth:`update_buttons` (mirroring the old in-place mutation).
    """

    class NewGameButton(discord.ui.Button):
        """Button to start a new game with a different bet"""

        def __init__(self, table: Blackjack) -> None:
            super().__init__(label="New game (same Bet)", style=discord.ButtonStyle.green, emoji="\U0001f501")
            self.table: Blackjack = table

        async def callback(self, interaction: discord.Interaction) -> None:
            """Starts a new game with the same bet"""
            table = self.table.wake_up(self.table.ctx, self.table.active_hand.bet)

            # Shuffle cards, just for aesthetics
            await interaction.response.edit_message(
                view=table.view.render(
                    table.active_hand,
                    colour=discord.Colour.light_grey(),
                    text="*Shuffling Cards...*",
                    image_url="https://klappstuhl.me/gallery/raw/TpjOl.gif",
                    with_buttons=False,
                )
            )
            table.active_hand.message = interaction.message

            await asyncio.sleep(3)

            await table.view.update_buttons(active=True)
            if not await table.view.check_for_winner(interaction):
                await interaction.message.edit(view=table.view.render(table.active_hand))

            table.ctx.bot.get_cog("Games").blackjack_tables[table.ctx.user.id] = table

    def __init__(self, table: Blackjack) -> None:
        super().__init__(timeout=300.0, members=table.ctx.user)

        self.table: Blackjack = table
        self.economy: Economy | None = self.table.ctx.bot.get_cog("Economy")  # type: ignore
        self._game_over: bool = False

        self.hit = discord.ui.Button(label="Hit", style=discord.ButtonStyle.blurple)
        self.hit.callback = self._on_hit  # type: ignore[assignment]
        self.stand = discord.ui.Button(label="Stand", style=discord.ButtonStyle.red)
        self.stand.callback = self._on_stand  # type: ignore[assignment]
        self.double_down = discord.ui.Button(label="Double Down", style=discord.ButtonStyle.grey)
        self.double_down.callback = self._on_double_down  # type: ignore[assignment]
        self.split = discord.ui.Button(label="Split", style=discord.ButtonStyle.grey, disabled=True)
        self.split.callback = self._on_split  # type: ignore[assignment]
        self.insurance = discord.ui.Button(label="Insurance", style=discord.ButtonStyle.green, disabled=True)
        self.insurance.callback = self._on_insurance  # type: ignore[assignment]
        self.surrender_btn = discord.ui.Button(label="Surrender", style=discord.ButtonStyle.grey, disabled=True)
        self.surrender_btn.callback = self._on_surrender  # type: ignore[assignment]
        self.help = discord.ui.Button(
            label="Help", style=discord.ButtonStyle.grey, emoji="\N{WHITE QUESTION MARK ORNAMENT}"
        )
        self.help.callback = self._on_help  # type: ignore[assignment]

    def render(
        self,
        hand: Hand,
        colour: discord.Colour = helpers.Colour.white(),
        text: str | None = None,
        image_url: str | None = None,
        *,
        with_buttons: bool = True,
    ) -> TableView:
        """Recompose the layout for ``hand``: the card plus (optionally) the controls."""
        self.clear_items()
        self.add_item(self.table.build_container(self, hand, colour, text, image_url, with_buttons))
        return self

    async def finish_winner(self, interaction: discord.Interaction, winner: WinningType) -> tuple[str, discord.Colour]:
        hand = self.table.active_hand
        amount: int | None = None
        insurance_result = ""

        # Handle insurance payout first (separate from main bet)
        if hand.insurance_bet > 0:
            if self.table.dealer_has_blackjack():
                insurance_payout = hand.insurance_bet * 3  # 2:1 payout + original bet back
                user_balance: Balance = await interaction.client.db.get_user_balance(
                    interaction.user.id, interaction.guild_id
                )
                await user_balance.add(cash=insurance_payout)
                insurance_result = f" Insurance paid {Emojis.Economy.cash} **{fnumb(insurance_payout)}**!"
            else:
                insurance_result = f" Insurance lost {Emojis.Economy.cash} **{fnumb(hand.insurance_bet)}**."

        if winner == WinningType.PLAYER_BLACKJACK:
            amount = int(hand.bet * 1.5)
            result = f"{winner.value}. You won {Emojis.Economy.cash} **{fnumb(amount)}**.{insurance_result}"
            color = helpers.Colour.lime_green()
        elif winner == WinningType.SURRENDER:
            amount = hand.bet // 2  # Return half the bet
            result = f"{winner.value}. Half your bet returned: {Emojis.Economy.cash} **{fnumb(amount)}**."
            color = helpers.Colour.light_grey()
        elif winner in {WinningType.DEALER_BLACKJACK, WinningType.DEALER_WIN, WinningType.PLAYER_BUST}:
            result = f"{winner.value}. You lost {Emojis.Economy.cash} **{fnumb(hand.bet)}**.{insurance_result}"
            color = helpers.Colour.light_red()
        elif winner in {WinningType.PLAYER_WIN, WinningType.DEALER_BUST}:
            amount = hand.bet * 2
            result = f"{winner.value}. You won {Emojis.Economy.cash} **{fnumb(hand.bet)}**.{insurance_result}"
            color = helpers.Colour.lime_green()
        elif winner == WinningType.PUSH:
            amount = hand.bet
            result = f"{winner.value}. {Emojis.Economy.cash} **{fnumb(hand.bet)}** returned.{insurance_result}"
            color = helpers.Colour.light_grey()
        else:
            result = "Something went wrong."
            color = helpers.Colour.white()

        if amount:
            user_balance = await interaction.client.db.get_user_balance(interaction.user.id, interaction.guild_id)
            await user_balance.add(cash=amount)

        bet = hand.bet + hand.insurance_bet
        if winner in {WinningType.PLAYER_BLACKJACK, WinningType.PLAYER_WIN, WinningType.DEALER_BUST}:
            game_result = GameResult.WIN
        elif winner in {WinningType.PUSH, WinningType.SURRENDER}:
            game_result = GameResult.PUSH
        else:
            game_result = GameResult.LOSS
        await interaction.client.db.game_stats.record_result(
            interaction.guild_id,
            interaction.user.id,
            Game.BLACKJACK,
            game_result,
            wagered=bet,
            profit=(amount or 0) - bet,
        )

        return result, color

    async def check_for_winner(self, interaction: discord.Interaction | Context) -> bool:
        """Checks if there is a winner and updates the card accordingly."""
        if not self.table.active_hand.finished:
            if (self.table.playing_players and self.table.active_hand.value >= 21) or (
                self.table.dealer.value == 21 and len(self.table.dealer) == 2
            ):
                # If the player has over 21 or a blackjack, or the dealer has a blackjack, stand automatically
                self.table.stand()
                await self.check_for_winner(interaction)
                return True
            return False

        # Respond to the interaction and disable the finished hand's message.
        finished = self.table.active_hand
        if isinstance(interaction, Context):
            await finished.message.edit(view=self.render(finished, with_buttons=False))
        elif interaction.response.is_done():
            await interaction.message.edit(view=self.render(finished, with_buttons=False))
        else:
            await interaction.response.edit_message(view=self.render(finished, with_buttons=False))

        if self.table.playing_players:
            # Start the next hand on its own message.
            next_hand = self.table.advance_hand()
            await self.update_buttons(active=True)
            message = await interaction.followup.send(view=self.render(next_hand))
            next_hand.message = message
        else:
            # Ensure to show the dealer's second card regardless of the outcome
            self.table.dealer.set_card_hidden(1, False)

            await self.update_buttons(active=False)
            self._game_over = True

            for hand in self.table.player_hands:
                winner = self.table.get_winner(hand)
                text, color = await self.finish_winner(interaction, winner)  # type: ignore
                await hand.message.edit(view=self.render(hand, color, text))

        return True

    async def update_buttons(self, *, active: bool) -> None:
        """Updates the enabled state of the action buttons (Help stays available)."""
        for item in (self.hit, self.stand, self.double_down, self.split):
            item.disabled = not active

        hand = self.table.active_hand
        balance: Balance = await self.table.ctx.db.get_user_balance(self.table.ctx.user.id, self.table.ctx.guild.id)

        if not hand.finished:
            can_split = len(hand) == 2 and hand.cards[0].value == hand.cards[1].value and hand.bet <= balance.cash
            insurance_cost = hand.bet // 2

            self.split.disabled = not can_split
            self.double_down.disabled = not (hand.bet <= balance.cash)
            # Insurance: only when dealer shows Ace, first action, and player can afford it
            self.insurance.disabled = not (self.table.can_offer_insurance and insurance_cost <= balance.cash)
            # Late Surrender: only on first action, not after split, and dealer must not have blackjack
            can_surrender = (
                len(hand) == 2
                and not hand.splitted
                and not self.table.dealer_has_blackjack()
            )
            self.surrender_btn.disabled = not can_surrender

    async def _on_hit(self, interaction: discord.Interaction) -> None:
        """Hits the player"""
        self.table.hit(self.table.active_hand)

        if not await self.check_for_winner(interaction):
            await interaction.response.edit_message(view=self.render(self.table.active_hand))

    async def _on_stand(self, interaction: discord.Interaction) -> None:
        """Stands the player"""
        self.table.stand()

        if not await self.check_for_winner(interaction):
            await interaction.response.edit_message(view=self.render(self.table.active_hand))

    async def _on_double_down(self, interaction: discord.Interaction) -> None:
        """Doubles the bet and hits the player"""
        user_balance: Balance = await interaction.client.db.get_user_balance(interaction.user.id, interaction.guild_id)
        await user_balance.remove(cash=self.table.active_hand.bet)

        self.table.active_hand.bet *= 2

        self.table.hit(self.table.active_hand)
        self.table.stand()

        if not await self.check_for_winner(interaction):
            await interaction.response.edit_message(view=self.render(self.table.active_hand))

    async def _on_split(self, interaction: discord.Interaction) -> None:
        """Splits the hand"""
        self.table.split()

        user_balance: Balance = await interaction.client.db.get_user_balance(interaction.user.id, interaction.guild_id)
        await user_balance.remove(cash=self.table.active_hand.bet)

        if not await self.check_for_winner(interaction):
            await interaction.response.edit_message(view=self.render(self.table.active_hand))

    async def _on_insurance(self, interaction: discord.Interaction) -> None:
        """Takes insurance (half the original bet) against dealer blackjack."""
        insurance_cost = self.table.active_hand.bet // 2

        user_balance: Balance = await interaction.client.db.get_user_balance(interaction.user.id, interaction.guild_id)
        await user_balance.remove(cash=insurance_cost)

        self.table.take_insurance(insurance_cost)
        self.insurance.disabled = True

        await interaction.response.edit_message(view=self.render(self.table.active_hand))

    async def _on_surrender(self, interaction: discord.Interaction) -> None:
        """Surrenders the hand, forfeiting half the bet."""
        self.table.surrender()

        if not await self.check_for_winner(interaction):
            await interaction.response.edit_message(view=self.render(self.table.active_hand))

    async def _on_help(self, interaction: discord.Interaction) -> None:
        """Shows the help menu"""
        embed = discord.Embed(title="Blackjack Help", colour=helpers.Colour.blurple())
        embed.set_thumbnail(url="https://klappstuhl.me/gallery/raw/nnxiW.gif")
        embed.description = (
            "The goal of blackjack is to beat the dealer's hand without going over 21.\n"
            "Face cards are worth 10. Aces are worth 1 or 11, whichever makes a better hand.\n"
            "Each player starts with two cards, one of the dealer's cards is hidden until the end.\n"
            "If you go over 21 you *bust*, and the dealer wins regardless of the dealer's hand.\n"
            "If you are dealt 21 from the start (Ace & 10), you got a *blackjack*.\n"
            "Blackjack usually means you win **1.5** the amount of your bet.\n"
            "Dealer will hit until his/her cards total **17 or higher**.\n"
            "You can only double/split/surrender on the first move, or first move of a hand created by a split.\n"
            "You cannot play on two aces after they are split.\n\n"
            "For more information, see [this](https://en.wikipedia.org/wiki/Blackjack) article."
        )
        embed.add_field(name="Hit", value="Draws a card from the deck.")
        embed.add_field(name="Stand", value="Stands the current hand.")
        embed.add_field(name="Double Down", value="Doubles the bet and hits the current hand.")
        embed.add_field(name="Split", value="Splits the current hand into two hands. Doubles the bet.")
        embed.add_field(name="Insurance", value="Side bet (half your bet) that pays 2:1 if dealer has blackjack. Only available when dealer shows an Ace.")
        embed.add_field(name="Surrender", value="Forfeit the hand and get half your bet back. Only available on your first action.")

        await interaction.response.send_message(embed=embed, ephemeral=True)
