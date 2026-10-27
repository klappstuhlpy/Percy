from __future__ import annotations

import asyncio
import functools
from contextlib import suppress
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
            # One new round per finished table, and only once its stake is taken.
            if self.disabled:
                await interaction.response.defer()
                return
            self.disabled = True
            games = self.table.ctx.bot.get_cog("Games")
            running = games.blackjack_tables.get(self.table.ctx.user.id) if games else None
            if running is not None and running.is_running:
                await interaction.response.send_message(
                    f"{Emojis.error} You are already playing a game of Blackjack.", ephemeral=True
                )
                return
            bet = self.table.initial_bet  # not the active hand's bet, which a double-down may have grown
            if await interaction.client.db.economy.debit_cash(interaction.user.id, interaction.guild_id, bet) is None:  # type: ignore[attr-defined]
                self.disabled = False
                await interaction.response.send_message(
                    f"{Emojis.error} You do not have enough **cash** for that.", ephemeral=True
                )
                return
            table = self.table.wake_up(self.table.ctx, bet)
            if games:
                games.blackjack_tables[table.ctx.user.id] = table

            # Shuffle cards, just for aesthetics
            await interaction.response.edit_message(
                view=table.view.render(
                    colour=discord.Colour.light_grey(),
                    text="*Shuffling Cards...*",
                    image_url="https://klappstuhl.me/gallery/raw/TpjOl.gif",
                    with_buttons=False,
                )
            )
            table.message = interaction.message

            await asyncio.sleep(3)

            await table.view.update_buttons(active=True)
            if not await table.view.check_for_winner(interaction):
                await interaction.message.edit(view=table.view.render())

    def __init__(self, table: Blackjack) -> None:
        super().__init__(timeout=300.0, members=table.ctx.user)

        self.table: Blackjack = table
        self.economy: Economy | None = self.table.ctx.bot.get_cog("Economy")  # type: ignore
        self._game_over: bool = False
        # Serialises the action buttons: two clicks racing the first edit must not both act (or both pay).
        self._lock = asyncio.Lock()

        self.hit = discord.ui.Button(label="Hit", style=discord.ButtonStyle.blurple)
        self.hit.callback = functools.partial(self._locked, self._on_hit)  # type: ignore[assignment]
        self.stand = discord.ui.Button(label="Stand", style=discord.ButtonStyle.red)
        self.stand.callback = functools.partial(self._locked, self._on_stand)  # type: ignore[assignment]
        self.double_down = discord.ui.Button(label="Double Down", style=discord.ButtonStyle.grey)
        self.double_down.callback = functools.partial(self._locked, self._on_double_down)  # type: ignore[assignment]
        self.split = discord.ui.Button(label="Split", style=discord.ButtonStyle.grey, disabled=True)
        self.split.callback = functools.partial(self._locked, self._on_split)  # type: ignore[assignment]
        self.insurance = discord.ui.Button(label="Insurance", style=discord.ButtonStyle.green, disabled=True)
        self.insurance.callback = functools.partial(self._locked, self._on_insurance)  # type: ignore[assignment]
        self.surrender_btn = discord.ui.Button(label="Surrender", style=discord.ButtonStyle.grey, disabled=True)
        self.surrender_btn.callback = functools.partial(self._locked, self._on_surrender)  # type: ignore[assignment]
        self.help = discord.ui.Button(
            label="Help", style=discord.ButtonStyle.grey, emoji="\N{WHITE QUESTION MARK ORNAMENT}"
        )
        self.help.callback = self._on_help  # type: ignore[assignment]

    def render(
        self,
        colour: discord.Colour = helpers.Colour.white(),
        text: str | None = None,
        image_url: str | None = None,
        *,
        with_buttons: bool = True,
    ) -> TableView:
        """Recompose the layout showing all hands with active indicator."""
        self.clear_items()
        self.add_item(self.table.build_container(self, colour, text, image_url, with_buttons))
        return self

    async def _locked(self, action, interaction: discord.Interaction) -> None:
        """Run one action button under the table lock, ignoring clicks on a finished hand or table."""
        async with self._lock:
            if self._game_over or self.table.active_hand.finished:
                await interaction.response.defer()
                return
            await action(interaction)

    async def on_timeout(self) -> None:
        """An abandoned table stands every open hand and settles, so the stake is resolved and the player unlocked."""
        async with self._lock:
            if self._game_over or self.table.message is None:
                return
            while self.table.playing_players:
                self.table.advance_hand()
                self.table.stand()
            with suppress(discord.HTTPException):
                await self.check_for_winner(self.table.ctx)

    async def finish_game(self, interaction: discord.Interaction) -> tuple[str, discord.Colour]:
        """Process all hands and return combined result text and overall colour."""
        results: list[str] = []
        total_profit = 0
        total_wagered = 0
        has_win = False
        has_loss = False

        for i, hand in enumerate(self.table.player_hands):
            winner = self.table.get_winner(hand)
            amount: int | None = None
            insurance_result = ""
            hand_num = f" #{i + 1}" if len(self.table.player_hands) > 1 else ""

            # Handle insurance payout
            if hand.insurance_bet > 0:
                if self.table.dealer_has_blackjack():
                    insurance_payout = hand.insurance_bet * 3
                    user_balance: Balance = await interaction.client.db.get_user_balance(
                        interaction.user.id, interaction.guild_id
                    )
                    await user_balance.add(cash=insurance_payout)
                    total_profit += insurance_payout - hand.insurance_bet
                    insurance_result = f" (Insurance +{fnumb(insurance_payout)})"
                else:
                    total_profit -= hand.insurance_bet
                    insurance_result = f" (Insurance -{fnumb(hand.insurance_bet)})"

            if winner == WinningType.PLAYER_BLACKJACK:
                # 3:2 — the stake comes back plus one and a half times it.
                amount = hand.bet + int(hand.bet * 1.5)
                results.append(f"**Blackjack{hand_num}!** +{Emojis.Economy.cash} {fnumb(amount - hand.bet)}{insurance_result}")
                has_win = True
            elif winner == WinningType.SURRENDER:
                amount = hand.bet // 2
                results.append(f"**Surrender{hand_num}** {Emojis.Economy.cash} {fnumb(amount)} returned")
            elif winner == WinningType.PLAYER_BUST:
                results.append(f"**Bust{hand_num}** -{Emojis.Economy.cash} {fnumb(hand.bet)}{insurance_result}")
                has_loss = True
            elif winner == WinningType.DEALER_BLACKJACK:
                results.append(f"**Dealer Blackjack{hand_num}** -{Emojis.Economy.cash} {fnumb(hand.bet)}{insurance_result}")
                has_loss = True
            elif winner == WinningType.DEALER_WIN:
                results.append(f"**Dealer Wins{hand_num}** -{Emojis.Economy.cash} {fnumb(hand.bet)}{insurance_result}")
                has_loss = True
            elif winner == WinningType.PLAYER_WIN:
                amount = hand.bet * 2
                results.append(f"**You Win{hand_num}!** +{Emojis.Economy.cash} {fnumb(hand.bet)}{insurance_result}")
                has_win = True
            elif winner == WinningType.DEALER_BUST:
                amount = hand.bet * 2
                results.append(f"**Dealer Bust{hand_num}!** +{Emojis.Economy.cash} {fnumb(hand.bet)}{insurance_result}")
                has_win = True
            elif winner == WinningType.PUSH:
                amount = hand.bet
                results.append(f"**Push{hand_num}** {Emojis.Economy.cash} {fnumb(hand.bet)} returned{insurance_result}")

            if amount:
                user_balance = await interaction.client.db.get_user_balance(interaction.user.id, interaction.guild_id)
                await user_balance.add(cash=amount)

            bet = hand.bet + hand.insurance_bet
            total_wagered += bet
            total_profit += (amount or 0) - hand.bet

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

        # Determine overall colour
        if has_win and not has_loss:
            color = helpers.Colour.lime_green()
        elif has_loss and not has_win:
            color = helpers.Colour.light_red()
        else:
            color = helpers.Colour.light_grey()

        return " | ".join(results), color

    async def check_for_winner(self, interaction: discord.Interaction | Context) -> bool:
        """Checks if there is a winner and updates the view accordingly."""
        if not self.table.active_hand.finished:
            if (self.table.playing_players and self.table.active_hand.value >= 21) or (
                self.table.dealer.value == 21 and len(self.table.dealer) == 2
            ):
                # Auto-stand on 21+, blackjack, or dealer blackjack
                self.table.stand()
                await self.check_for_winner(interaction)
                return True
            return False

        if self.table.playing_players:
            # Move to next hand, update the same message
            self.table.advance_hand()
            await self.update_buttons(active=True)
            if isinstance(interaction, Context):
                await self.table.message.edit(view=self.render())
            elif interaction.response.is_done():
                await interaction.message.edit(view=self.render())
            else:
                await interaction.response.edit_message(view=self.render())
        else:
            # Game over - reveal dealer's hole card
            self.table.dealer.set_card_hidden(1, False)

            # Flip before the first await so nothing else can settle this table a second time.
            if self._game_over:
                return True
            self._game_over = True
            await self.update_buttons(active=False)

            text, color = await self.finish_game(interaction)
            if isinstance(interaction, Context):
                await self.table.message.edit(view=self.render(color, text))
            elif interaction.response.is_done():
                await interaction.message.edit(view=self.render(color, text))
            else:
                await interaction.response.edit_message(view=self.render(color, text))

        return True

    async def update_buttons(self, *, active: bool) -> None:
        """Updates the enabled state of the action buttons (Help stays available)."""
        for item in (self.hit, self.stand, self.double_down, self.split):
            item.disabled = not active

        hand = self.table.active_hand
        balance: Balance = await self.table.ctx.db.get_user_balance(self.table.ctx.user.id, self.table.ctx.guild.id)

        if not hand.finished:
            can_split = self.table.can_split and hand.bet <= balance.cash
            insurance_cost = hand.bet // 2

            self.split.disabled = not can_split
            # Doubling, like splitting, is a first-action-of-the-hand decision.
            self.double_down.disabled = not (len(hand) == 2 and hand.bet <= balance.cash)
            # Insurance: only when dealer shows Ace, first action, and player can afford it
            self.insurance.disabled = not (self.table.can_offer_insurance and insurance_cost <= balance.cash)
            # Late Surrender: only on first action, not after split. Whether the dealer holds blackjack must not
            # shape the controls (that would leak the hole card); the engine voids a surrender against one instead.
            can_surrender = len(hand) == 2 and not hand.splitted
            self.surrender_btn.disabled = not can_surrender

    async def _on_hit(self, interaction: discord.Interaction) -> None:
        """Hits the player"""
        self.table.hit(self.table.active_hand)

        if not await self.check_for_winner(interaction):
            await interaction.response.edit_message(view=self.render())

    async def _on_stand(self, interaction: discord.Interaction) -> None:
        """Stands the player"""
        self.table.stand()

        if not await self.check_for_winner(interaction):
            await interaction.response.edit_message(view=self.render())

    async def _pay(self, interaction: discord.Interaction, amount: int) -> bool:
        """Atomically debits ``amount`` cash; on a shortfall answers the click and returns ``False``."""
        if await interaction.client.db.economy.debit_cash(interaction.user.id, interaction.guild_id, amount) is not None:  # type: ignore[attr-defined]
            return True
        await interaction.response.send_message(f"{Emojis.error} You do not have enough **cash** for that.", ephemeral=True)
        return False

    async def _on_double_down(self, interaction: discord.Interaction) -> None:
        """Doubles the bet and hits the player"""
        if len(self.table.active_hand) != 2:
            await interaction.response.send_message(f"{Emojis.error} You can only double on your first two cards.", ephemeral=True)
            return
        if not await self._pay(interaction, self.table.active_hand.bet):
            return

        self.table.active_hand.bet *= 2

        self.table.hit(self.table.active_hand)
        self.table.stand()

        if not await self.check_for_winner(interaction):
            await interaction.response.edit_message(view=self.render())

    async def _on_split(self, interaction: discord.Interaction) -> None:
        """Splits the hand"""
        if not self.table.can_split:
            await interaction.response.send_message(
                f"{Emojis.error} You can only split a starting pair, up to 3 hands.", ephemeral=True
            )
            return
        if not await self._pay(interaction, self.table.active_hand.bet):
            return
        self.table.split()

        if not await self.check_for_winner(interaction):
            await interaction.response.edit_message(view=self.render())

    async def _on_insurance(self, interaction: discord.Interaction) -> None:
        """Takes insurance (half the original bet) against dealer blackjack."""
        insurance_cost = self.table.active_hand.bet // 2
        if not self.table.can_offer_insurance:
            await interaction.response.defer()
            return

        if not await self._pay(interaction, insurance_cost):
            return

        self.table.take_insurance(insurance_cost)
        self.insurance.disabled = True

        await interaction.response.edit_message(view=self.render())

    async def _on_surrender(self, interaction: discord.Interaction) -> None:
        """Surrenders the hand, forfeiting half the bet."""
        if len(self.table.active_hand) != 2 or self.table.active_hand.splitted:
            await interaction.response.defer()
            return
        self.table.surrender()

        if not await self.check_for_winner(interaction):
            await interaction.response.edit_message(view=self.render())

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
