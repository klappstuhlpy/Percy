from __future__ import annotations

import random

import discord

from app.cogs.games.engine.scratch import PICKS, TILES, ScratchCard
from app.cogs.games.models import Game, GameResult
from app.core.views import LayoutView
from app.utils import fnumb, helpers
from config import Emojis

__all__ = ("ScratchGame",)

_HIDDEN = "\N{BLACK LARGE SQUARE}"


class ScratchTile(discord.ui.Button["ScratchGame"]):
    def __init__(self, index: int) -> None:
        self.index = index
        super().__init__(style=discord.ButtonStyle.secondary, emoji=_HIDDEN)

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.on_scratch(interaction, self.index)


class ScratchGame(LayoutView):
    """A 3x3 scratch card. The cog debits the bet; finishing the card pays ``bet * multiplier``.

    Walking away does not forfeit the card: on timeout the remaining scratches are
    made at random and the card is settled anyway.
    """

    def __init__(self, client: discord.Client, player: discord.Member, bet: int) -> None:
        super().__init__(timeout=90.0, members=player)
        self.client = client
        self.player = player
        self.bet = bet
        self.card = ScratchCard()
        self.tiles = [ScratchTile(i) for i in range(TILES)]
        self._compose()

    def _compose(self) -> None:
        self.clear_items()
        card = self.card
        for tile in self.tiles:
            if tile.index in card.scratched or card.finished:
                value = card.tiles[tile.index]
                tile.emoji = None
                tile.label = f"x{value:g}"
                tile.style = (
                    discord.ButtonStyle.green if tile.index in card.scratched and value >= 1
                    else discord.ButtonStyle.red if tile.index in card.scratched
                    else discord.ButtonStyle.secondary
                )
                tile.disabled = True

        if card.finished:
            colour = helpers.Colour.lime_green() if card.multiplier >= 1 else helpers.Colour.light_red()
        else:
            colour = helpers.Colour.white()
        container = discord.ui.Container(accent_colour=colour)
        container.add_item(discord.ui.TextDisplay("## \N{TICKET} Scratch Card"))
        status = f"Bet: {Emojis.Economy.cash} **{fnumb(self.bet)}** • Scratches left: **{PICKS - len(card.scratched)}**"
        if card.finished:
            status += (
                f"\nYour tiles average **x{card.multiplier:.2f}** — "
                f"paid {Emojis.Economy.cash} **{fnumb(round(self.bet * card.multiplier))}**."
            )
        else:
            status += "\n-# Scratch three tiles — you win the average of what you uncover."
        container.add_item(discord.ui.TextDisplay(status))
        container.add_item(discord.ui.Separator())
        for row in range(3):
            container.add_item(discord.ui.ActionRow(*self.tiles[row * 3:row * 3 + 3]))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"-# Player: {self.player}"))
        self.add_item(container)

    async def _settle(self, guild_id: int) -> None:
        self.stop()
        client = self.client
        payout = round(self.bet * self.card.multiplier)
        if payout:
            balance = await client.db.get_user_balance(self.player.id, guild_id)  # type: ignore[attr-defined]
            await balance.add(cash=payout)
        profit = payout - self.bet
        result = GameResult.WIN if profit > 0 else GameResult.PUSH if profit == 0 else GameResult.LOSS
        await client.db.game_stats.record_result(  # type: ignore[attr-defined]
            guild_id, self.player.id, Game.SCRATCH, result, wagered=self.bet, profit=profit
        )

    async def on_scratch(self, interaction: discord.Interaction, index: int) -> None:
        if self.card.finished or index in self.card.scratched:
            await interaction.response.defer()
            return
        self.card.scratch(index)
        self._compose()
        await interaction.response.edit_message(view=self)
        if self.card.finished:
            assert interaction.guild is not None
            await self._settle(interaction.guild.id)

    async def on_timeout(self) -> None:
        if self.card.finished or self.message is None or self.message.guild is None:
            return
        left = [i for i in range(TILES) if i not in self.card.scratched]
        for index in random.sample(left, PICKS - len(self.card.scratched)):
            self.card.scratch(index)
        self._compose()
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass
        await self._settle(self.message.guild.id)
