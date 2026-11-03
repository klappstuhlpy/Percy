from __future__ import annotations

import asyncio

import discord

from app.cogs.games.engine.crash import Crash
from app.cogs.games.models import Game, GameResult
from app.core.views import LayoutView
from app.utils import fnumb, helpers
from config import Emojis

__all__ = ("CrashGame",)

#: Seconds between multiplier ticks (one message edit each).
TICK_SECONDS: float = 1.5


class CrashGame(LayoutView):
    """Single-player Crash: :meth:`run` ticks the multiplier, the button cashes out.

    The bet is debited by the cog before the view is created. Settlement happens
    exactly once, from whichever side ends the run first (button, auto-cash-out,
    the cap, or the crash).
    """

    def __init__(self, player: discord.Member, bet: int, auto_cashout: float | None) -> None:
        super().__init__(timeout=None, members=player)
        self.player = player
        self.bet = bet
        self.engine = Crash(auto_cashout)
        self._settled: bool = False

        self.cash_out = discord.ui.Button(label="Cash Out", style=discord.ButtonStyle.green, emoji="\N{MONEY BAG}")
        self.cash_out.callback = self._on_cash_out  # type: ignore[assignment]
        self._compose()

    def _compose(self) -> None:
        self.clear_items()
        engine = self.engine
        if engine.crashed:
            colour, headline = helpers.Colour.light_red(), f"# \N{COLLISION SYMBOL} Crashed at x{engine.crash_point:.2f}"
        elif engine.cashed_at is not None:
            colour, headline = helpers.Colour.lime_green(), f"# \N{MONEY BAG} Cashed out at x{engine.cashed_at:.2f}"
        else:
            colour, headline = helpers.Colour.white(), f"# \N{CHART WITH UPWARDS TREND} x{engine.multiplier:.2f}"

        container = discord.ui.Container(accent_colour=colour)
        container.add_item(discord.ui.TextDisplay("## Crash"))
        container.add_item(discord.ui.TextDisplay(headline))
        lines = f"Bet: {Emojis.Economy.cash} **{fnumb(self.bet)}**"
        if engine.auto_cashout is not None:
            lines += f" • Auto cash-out: **x{engine.auto_cashout:.2f}**"
        if engine.crashed:
            lines += f"\nLost {Emojis.Economy.cash} **{fnumb(self.bet)}**."
        elif engine.cashed_at is not None:
            lines += f"\nWon {Emojis.Economy.cash} **{fnumb(self._payout())}**."
        else:
            lines += f" • Cash-out now: {Emojis.Economy.cash} **{fnumb(self._payout())}**"
        container.add_item(discord.ui.TextDisplay(lines))

        if not engine.finished:
            self.cash_out.disabled = engine.tick_count == 0
            container.add_item(discord.ui.ActionRow(self.cash_out))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"-# Player: {self.player}"))
        self.add_item(container)

    def _payout(self) -> int:
        return round(self.bet * (self.engine.cashed_at or self.engine.multiplier))

    async def _settle(self, client: discord.Client, guild_id: int) -> None:
        """Credit a cash-out and record the round — once."""
        if self._settled or not self.engine.finished:
            return
        self._settled = True
        self.stop()
        if self.engine.crashed:
            result, profit = GameResult.LOSS, -self.bet
        else:
            payout = self._payout()
            balance = await client.db.get_user_balance(self.player.id, guild_id)  # type: ignore[attr-defined]
            await balance.add(cash=payout)
            result, profit = GameResult.WIN, payout - self.bet
        await client.db.game_stats.record_result(  # type: ignore[attr-defined]
            guild_id, self.player.id, Game.CRASH, result, wagered=self.bet, profit=profit
        )

    async def run(self, client: discord.Client) -> None:
        """Tick until the run ends, editing the message each step."""
        assert self.message is not None and self.message.guild is not None
        guild_id = self.message.guild.id
        while not self.engine.finished:
            await asyncio.sleep(TICK_SECONDS)
            if self.engine.finished:  # cashed out by the button while we slept
                break
            self.engine.tick()
            self._compose()
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        await self._settle(client, guild_id)

    async def _on_cash_out(self, interaction: discord.Interaction) -> None:
        if self.engine.cash_out() is None:
            await interaction.response.defer()
            return
        self._compose()
        await interaction.response.edit_message(view=self)
        assert interaction.guild is not None
        await self._settle(interaction.client, interaction.guild.id)
