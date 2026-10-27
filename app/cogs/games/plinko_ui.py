from __future__ import annotations

import discord

from app.cogs.games.engine import plinko
from app.cogs.games.models import Game, GameResult
from app.core.views import LayoutView
from app.utils import fnumb, helpers
from config import Emojis

__all__ = ("PlinkoGame",)


class PlinkoGame(LayoutView):
    """One Plinko drop. The bet is debited by the cog; :meth:`play` settles exactly once."""

    def __init__(self, player: discord.Member, bet: int, rows: int, risk: str) -> None:
        super().__init__(timeout=None, members=player)
        self.player = player
        self.bet = bet
        self.rows = rows
        self.risk = risk
        self.path, self.slot, self.multiplier = plinko.drop(rows, risk)
        self.payout = round(bet * self.multiplier)
        self._settled: bool = False
        self._compose()

    def _board(self) -> str:
        table = plinko.multipliers(self.rows, self.risk)
        pos = 0
        lines = []
        for i, step in enumerate(self.path):
            pos += step
            lines.append(" " * (self.rows - i - 1) + " ".join("o" if j == pos else "." for j in range(i + 2)))
        return "```\n" + "\n".join(lines) + "\n```\n`" + " ".join(f"{m:g}" for m in table) + "`"

    def _compose(self) -> None:
        self.clear_items()
        diff = self.payout - self.bet
        colour = helpers.Colour.lime_green() if diff > 0 else helpers.Colour.light_red()
        cash = Emojis.Economy.cash
        if diff > 0:
            outcome = f"Won {cash} **{fnumb(diff)}**."
        elif diff < 0:
            outcome = f"Lost {cash} **{fnumb(-diff)}**."
        else:
            outcome = "Got your bet back."
        container = discord.ui.Container(accent_colour=colour)
        container.add_item(discord.ui.TextDisplay(f"## Plinko \N{BULLET} {self.rows} rows, {self.risk} risk"))
        container.add_item(discord.ui.TextDisplay(self._board()))
        container.add_item(
            discord.ui.TextDisplay(
                f"# x{self.multiplier:g}\nBet: {cash} **{fnumb(self.bet)}** \N{BULLET} "
                f"Payout: {cash} **{fnumb(self.payout)}**\n{outcome}"
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"-# Player: {self.player}"))
        self.add_item(container)

    async def play(self, client: discord.Client, guild_id: int) -> None:
        """Credit the payout and record the round — once."""
        if self._settled:
            return
        self._settled = True
        self.stop()
        if self.payout:
            balance = await client.db.get_user_balance(self.player.id, guild_id)  # type: ignore[attr-defined]
            await balance.add(cash=self.payout)
        profit = self.payout - self.bet
        result = GameResult.WIN if profit > 0 else GameResult.LOSS if profit < 0 else GameResult.PUSH
        await client.db.game_stats.record_result(  # type: ignore[attr-defined]
            guild_id, self.player.id, Game.PLINKO, result, wagered=self.bet, profit=profit
        )
