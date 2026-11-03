from __future__ import annotations

import discord

from app.cogs.games.engine import keno
from app.cogs.games.models import Game, GameResult
from app.core.views import LayoutView
from app.utils import fnumb, helpers
from config import Emojis

__all__ = ("KenoGame",)


class KenoGame(LayoutView):
    """One Keno round. The bet is debited by the cog; :meth:`play` settles exactly once."""

    def __init__(self, player: discord.Member, bet: int, picks: set[int]) -> None:
        super().__init__(timeout=None, members=player)
        self.player = player
        self.bet = bet
        self.picks = picks
        self.drawn = keno.draw()
        self.hits, self.multiplier = keno.score(picks, self.drawn)
        self.payout = round(bet * self.multiplier)
        self._settled: bool = False
        self._compose()

    def _grid(self) -> str:
        rows = []
        for start in range(1, keno.POOL + 1, 10):
            cells = []
            for n in range(start, start + 10):
                mark = "*" if n in self.hits else "x" if n in self.drawn else "o" if n in self.picks else " "
                cells.append(f"{n:>2}{mark}")
            rows.append(" ".join(cells))
        return "```\n" + "\n".join(rows) + "\n```\n-# `*` hit  `x` drawn  `o` your pick"

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
        container.add_item(discord.ui.TextDisplay("## Keno"))
        container.add_item(discord.ui.TextDisplay(self._grid()))
        container.add_item(
            discord.ui.TextDisplay(
                f"# {len(self.hits)}/{len(self.picks)} hits \N{BULLET} x{self.multiplier:g}\n"
                f"Bet: {cash} **{fnumb(self.bet)}** \N{BULLET} Payout: {cash} **{fnumb(self.payout)}**\n{outcome}"
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
            guild_id, self.player.id, Game.KENO, result, wagered=self.bet, profit=profit
        )
