from __future__ import annotations

import asyncio
import copy
from typing import TYPE_CHECKING

import discord

from app.cogs.games.engine.blackjack import WinningType
from app.core import Context, View
from app.utils import fnumb, helpers
from config import Emojis

if TYPE_CHECKING:
    from app.cogs.economy import Economy
    from app.cogs.games.blackjack_bridge import Blackjack
    from app.database.base import Balance

__all__ = ("NewGameButton", "TableView")


class TableView(View):
    """Represents a view for the blackjack game"""

    def __init__(self, table: Blackjack) -> None:
        super().__init__(timeout=300.0, members=table.ctx.user)

        self.table: Blackjack = table
        self.economy: Economy | None = self.table.ctx.bot.get_cog("Economy")  # type: ignore

    async def finish_winner(self, interaction: discord.Interaction, winner: WinningType) -> tuple[str, discord.Colour]:
        amount: int | None = None
        if winner == WinningType.PLAYER_BLACKJACK:
            amount = int(self.table.active_hand.bet * 1.5)
            result = f"{winner.value}. You won {Emojis.Economy.cash} **{fnumb(amount)}**."  # type: ignore
            color = helpers.Colour.lime_green()
        elif winner in {WinningType.DEALER_BLACKJACK, WinningType.DEALER_WIN, WinningType.PLAYER_BUST}:
            result = f"{winner.value}. You lost {Emojis.Economy.cash} **{fnumb(self.table.active_hand.bet)}**."
            color = helpers.Colour.light_red()
        elif winner in {WinningType.PLAYER_WIN, WinningType.DEALER_BUST}:
            amount = self.table.active_hand.bet * 2
            result = f"{winner.value}. You won {Emojis.Economy.cash} **{fnumb(self.table.active_hand.bet)}**."
            color = helpers.Colour.lime_green()
        elif winner == WinningType.PUSH:
            amount = self.table.active_hand.bet
            result = f"{winner.value}. {Emojis.Economy.cash} **{fnumb(self.table.active_hand.bet)}** returned."
            color = helpers.Colour.light_grey()
        else:
            result = "Something went wrong."
            color = helpers.Colour.white()

        if amount:
            user_balance: Balance = await interaction.client.db.get_user_balance(interaction.user.id, interaction.guild_id)
            await user_balance.add(cash=amount)

        return result, color

    async def check_for_winner(self, interaction: discord.Interaction | Context) -> bool:
        """Checks if there is a winner and updates the embed accordingly"""
        if not self.table.active_hand.finished:
            if (self.table.playing_players and self.table.active_hand.value >= 21) or (
                self.table.dealer.value == 21 and len(self.table.dealer) == 2
            ):
                # If the player has over 21 or a blackjack, or the dealer has a blackjack, stand automatically
                self.table.stand()
                await self.check_for_winner(interaction)
                return True
            return False

        # Just not get a "Failed Interaction" error displayed
        if isinstance(interaction, Context):
            _send_action = self.table.active_hand.message.edit
        else:
            _send_action = interaction.message.edit if interaction.response.is_done() else interaction.response.edit_message
        await _send_action(embed=self.table.build_embed(self.table.active_hand), view=self)

        _disabled_self = copy.copy(self)
        for item in _disabled_self.children:
            item.disabled = True
        await self.table.active_hand.message.edit(view=_disabled_self)

        if self.table.playing_players:
            # Start the next hand
            next_hand = self.table.advance_hand()
            await self.update_buttons(active=True)
            message = await interaction.followup.send(embed=self.table.build_embed(next_hand), view=self)
            next_hand.message = message
        else:
            # Ensure to show the dealer's second card regardless of the outcome
            self.table.dealer.cards[1].hidden = False

            await self.update_buttons(active=False)

            self.add_item(NewGameButton(self.table))

            for hand in self.table.player_hands:
                winner = self.table.get_winner(hand)
                text, color = await self.finish_winner(interaction, winner)  # type: ignore
                await hand.message.edit(embed=self.table.build_embed(hand, color, text), view=self)

        return True

    async def update_buttons(self, *, active: bool) -> None:
        """Updates the buttons of the view"""
        for item in self.children:
            item.disabled = not active

        hand = self.table.active_hand
        balance: Balance = await self.table.ctx.db.get_user_balance(self.table.ctx.user.id, self.table.ctx.guild.id)

        if not hand.finished:
            can_split = len(hand) == 2 and hand.cards[0].value == hand.cards[1].value and hand.bet <= balance.cash

            self.split.disabled = not can_split
            self.double_down.disabled = not (hand.bet <= balance.cash)

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.blurple)
    async def hit(self: TableView, interaction: discord.Interaction, _) -> None:
        """Hits the player"""
        self.table.hit(self.table.active_hand)

        if not await self.check_for_winner(interaction):
            await interaction.response.edit_message(embed=self.table.build_embed(self.table.active_hand), view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.red)
    async def stand(self: TableView, interaction: discord.Interaction, _) -> None:
        """Stands the player"""
        self.table.stand()

        if not await self.check_for_winner(interaction):
            await interaction.response.edit_message(embed=self.table.build_embed(self.table.active_hand), view=self)

    @discord.ui.button(label="Double Down", style=discord.ButtonStyle.grey)
    async def double_down(self: TableView, interaction: discord.Interaction, _) -> None:
        """Doubles the bet and hits the player"""
        user_balance: Balance = await interaction.client.db.get_user_balance(interaction.user.id, interaction.guild_id)
        await user_balance.remove(cash=self.table.active_hand.bet)

        self.table.active_hand.bet *= 2

        self.table.hit(self.table.active_hand)
        self.table.stand()

        if not await self.check_for_winner(interaction):
            await interaction.response.edit_message(embed=self.table.build_embed(self.table.active_hand), view=self)

    @discord.ui.button(label="Split", style=discord.ButtonStyle.grey, disabled=True)
    async def split(self: TableView, interaction: discord.Interaction, _) -> None:
        """Splits the hand"""
        self.table.split()

        user_balance: Balance = await interaction.client.db.get_user_balance(interaction.user.id, interaction.guild_id)
        await user_balance.remove(cash=self.table.active_hand.bet)

        if not await self.check_for_winner(interaction):
            await interaction.response.edit_message(embed=self.table.build_embed(self.table.active_hand), view=self)

    @discord.ui.button(label="Help", style=discord.ButtonStyle.grey, emoji="\N{WHITE QUESTION MARK ORNAMENT}", row=1)
    async def help(self: TableView, interaction: discord.Interaction, _) -> None:
        """Shows the help menu"""
        embed = discord.Embed(title="Blackjack Help", colour=helpers.Colour.blurple())
        embed.set_thumbnail(url="https://klappstuhl.me/gallery/nnxiW.gif")
        embed.description = (
            "The goal of blackjack is to beat the dealer's hand without going over 21.\n"
            "Face cards are worth 10. Aces are worth 1 or 11, whichever makes a better hand.\n"
            "Each player starts with two cards, one of the dealer's cards is hidden until the end.\n"
            "If you go over 21 you *bust*, and the dealer wins regardless of the dealer's hand.\n"
            "If you are dealt 21 from the start (Ace & 10), you got a *blackjack*.\n"
            "Blackjack usually means you win **1.5** the amount of your bet.\n"
            "Dealer will hit until his/her cards total **17 or higher**.\n"
            "You can only double/split on the first move, or first move of a hand created by a split.\n"
            "You cannot play on two aces after they are split.\n\n"
            "For more information, see [this](https://en.wikipedia.org/wiki/Blackjack) article."
        )
        embed.add_field(name="Hit", value="Draws a card from the deck.")
        embed.add_field(name="Stand", value="Stands the current hand.")
        embed.add_field(name="Double Down", value="Doubles the bet and hits the current hand.")
        embed.add_field(name="Split", value="Splits the current hand into two hands. Doubles the bet.")

        await interaction.response.send_message(embed=embed, ephemeral=True)


class NewGameButton(discord.ui.Button):
    """Button to start a new game with a different bet"""

    def __init__(self, table: Blackjack) -> None:
        super().__init__(label="New game (same Bet)", style=discord.ButtonStyle.green, emoji="\U0001f501", row=2)
        self.table: Blackjack = table

    async def callback(self, interaction: discord.Interaction) -> None:
        """Starts a new game with the same bet"""
        table = self.table.wake_up(self.table.ctx, self.table.active_hand.bet)

        # Shuffle cards, just for aesthetics
        embed = table.build_embed(
            hand=table.active_hand,
            image_url="https://klappstuhl.me/gallery/TpjOl.gif",
            colour=discord.Colour.light_grey(),
            text="*Shuffling Cards...*",
        )
        await interaction.response.edit_message(embed=embed, view=None)
        table.active_hand.message = interaction.message

        await asyncio.sleep(3)

        await table.view.update_buttons(active=True)
        if not await table.view.check_for_winner(interaction):
            await interaction.message.edit(embed=table.build_embed(table.active_hand), view=table.view)

        table.ctx.bot.get_cog("Games").blackjack_tables[table.ctx.user.id] = table
