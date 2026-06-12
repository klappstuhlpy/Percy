from __future__ import annotations

import discord

from app.cogs.games.engine.higherlower import HigherLower
from app.cogs.games.models import Game, GameResult
from app.core.views import LayoutView
from app.utils import fnumb, helpers
from config import Emojis

__all__ = ("HigherLowerGame",)


class HigherLowerGame(LayoutView):
    """Single-player Higher/Lower with a rising cash-out multiplier.

    The bet is debited by the cog before the view is created; a successful cash-out
    credits ``bet * multiplier`` while a wrong call (or timeout) simply keeps the
    already-debited stake.
    """

    def __init__(self, player: discord.Member, bet: int) -> None:
        super().__init__(members=player)
        self.player = player
        self.bet = bet
        self.engine = HigherLower()
        self.finished: bool = False
        self._status: str | None = None

        self.higher = discord.ui.Button(label="Higher", style=discord.ButtonStyle.green, emoji="\N{UP-POINTING SMALL RED TRIANGLE}")
        self.higher.callback = self._on_higher  # type: ignore[assignment]
        self.lower = discord.ui.Button(label="Lower", style=discord.ButtonStyle.red, emoji="\N{DOWN-POINTING SMALL RED TRIANGLE}")
        self.lower.callback = self._on_lower  # type: ignore[assignment]
        self.cash_out = discord.ui.Button(label="Cash Out", style=discord.ButtonStyle.blurple, emoji="\N{MONEY BAG}")
        self.cash_out.callback = self._on_cash_out  # type: ignore[assignment]

        self._compose()

    # -- rendering --------------------------------------------------------

    def _potential(self) -> int:
        return round(self.bet * self.engine.multiplier)

    def _compose(self) -> None:
        self.clear_items()

        if self.finished:
            colour = helpers.Colour.lime_green() if not self.engine.busted else helpers.Colour.light_red()
        else:
            colour = helpers.Colour.white()

        container = discord.ui.Container(accent_colour=colour)
        container.add_item(discord.ui.TextDisplay("## \N{UP DOWN ARROW} Higher or Lower"))
        container.add_item(discord.ui.Separator())

        higher_odds = self.engine.odds(True).favorable
        lower_odds = self.engine.odds(False).favorable

        container.add_item(discord.ui.TextDisplay(f"-# Current card\n{self.engine.current.display(size="large", formatted=True)}"))
        if self.engine.next is not None:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(f"-# Next\n{self.engine.next.display(size="large", formatted=True)}"))

        container.add_item(discord.ui.TextDisplay(
            f"Bet: {Emojis.Economy.cash} **{fnumb(self.bet)}** • Multiplier: **x{self.engine.multiplier:.2f}**\n"
            f"Cash-out value: {Emojis.Economy.cash} **{fnumb(self._potential())}** • Streak: **{self.engine.rounds}**"
        ))

        if not self.finished:
            container.add_item(discord.ui.TextDisplay(
                f"-# Next higher: `{higher_odds}/13` (**x{self.engine.step_multiplier(True):.2f}**) • "
                f"Next lower: `{lower_odds}/13` (**x{self.engine.step_multiplier(False):.2f}**)"
            ))

        if self._status:
            container.add_item(discord.ui.TextDisplay(self._status))

        container.add_item(discord.ui.Separator())
        if self.finished:
            container.add_item(discord.ui.TextDisplay("*Game over. Start a new round with `higherlower`.*"))
        else:
            container.add_item(discord.ui.ActionRow(self.higher, self.lower, self.cash_out))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"-# Player: {self.player}"))
        self.add_item(container)

    # -- outcomes ---------------------------------------------------------

    async def _record(self, interaction: discord.Interaction, result: GameResult, profit: int) -> None:
        assert interaction.guild is not None
        await interaction.client.db.game_stats.record_result(
            interaction.guild.id, self.player.id, Game.HIGHERLOWER, result, wagered=self.bet, profit=profit
        )

    async def _resolve_guess(self, interaction: discord.Interaction, higher: bool) -> None:
        _, correct = self.engine.guess(higher)
        if correct:
            self._status = "`\N{WHITE HEAVY CHECK MARK} Correct!` Keep going or cash out."
            self._compose()
            await interaction.response.edit_message(view=self)
        else:
            self.finished = True
            self._status = f"`\N{CROSS MARK} Busted!` You lost {Emojis.Economy.cash} **{fnumb(self.bet)}**."
            self._compose()
            await interaction.response.edit_message(view=self)
            await self._record(interaction, GameResult.LOSS, -self.bet)
            self.stop()

    async def _on_higher(self, interaction: discord.Interaction) -> None:
        await self._resolve_guess(interaction, True)

    async def _on_lower(self, interaction: discord.Interaction) -> None:
        await self._resolve_guess(interaction, False)

    async def _on_cash_out(self, interaction: discord.Interaction) -> None:
        if self.engine.rounds == 0:
            await interaction.response.send_message(
                f"{Emojis.error} Make at least one call before cashing out.", ephemeral=True
            )
            return

        self.finished = True
        payout = self._potential()
        assert interaction.guild is not None
        balance = await interaction.client.db.get_user_balance(self.player.id, interaction.guild.id)
        await balance.add(cash=payout)

        self._status = f"`\N{MONEY BAG} Cashed out` {Emojis.Economy.cash} **{fnumb(payout)}** (x{self.engine.multiplier:.2f})."
        self._compose()
        await interaction.response.edit_message(view=self)
        await self._record(interaction, GameResult.WIN, payout - self.bet)
        self.stop()
