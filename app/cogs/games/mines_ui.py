from __future__ import annotations

import discord

from app.cogs.games.engine.mines import COLS, ROWS, TILES, Mines
from app.cogs.games.models import Game, GameResult
from app.core.views import LayoutView
from app.utils import fnumb, helpers
from config import Emojis

__all__ = ("MinesGame",)

_GEM = "\N{GEM STONE}"
_MINE = "\N{BOMB}"
_HIDDEN = "​"  # zero-width space


class MinesButton(discord.ui.Button["MinesGame"]):
    """A single grid tile."""

    def __init__(self, index: int) -> None:
        self.index = index
        super().__init__(style=discord.ButtonStyle.blurple, label=_HIDDEN)

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.on_reveal(interaction, self.index)


class MinesGame(LayoutView):
    """Single-player Mines with a rising cash-out multiplier.

    The bet is debited by the cog before the view is created; a cash-out (or fully
    clearing the board) credits ``bet * multiplier`` while hitting a mine keeps the
    already-debited stake.
    """

    def __init__(self, player: discord.Member, bet: int, mine_count: int) -> None:
        super().__init__(timeout=300.0, members=player)
        self.player = player
        self.bet = bet
        self.engine = Mines(mine_count)
        self.finished: bool = False
        self._status: str | None = None

        self.tiles: list[MinesButton] = [MinesButton(i) for i in range(TILES)]
        self.cash_out = discord.ui.Button(label="Cash Out", style=discord.ButtonStyle.green, emoji="\N{MONEY BAG}")
        self.cash_out.callback = self._on_cash_out  # type: ignore[assignment]

        self._compose()

    # -- rendering --------------------------------------------------------

    def _potential(self) -> int:
        return round(self.bet * self.engine.multiplier)

    def _paint_tiles(self, *, reveal_all: bool = False) -> None:
        for tile in self.tiles:
            is_mine = tile.index in self.engine.mine_positions
            if tile.index in self.engine.revealed:
                tile.style, tile.label, tile.emoji, tile.disabled = discord.ButtonStyle.secondary, None, _GEM, True
            elif reveal_all and is_mine:
                tile.style, tile.label, tile.emoji, tile.disabled = discord.ButtonStyle.red, None, _MINE, True
            elif reveal_all:
                tile.style, tile.label, tile.emoji, tile.disabled = discord.ButtonStyle.secondary, _HIDDEN, None, True
            else:
                tile.style, tile.label, tile.emoji, tile.disabled = discord.ButtonStyle.blurple, _HIDDEN, None, False

    def _compose(self) -> None:
        self.clear_items()
        self._paint_tiles(reveal_all=self.finished)

        if self.finished:
            colour = helpers.Colour.light_red() if self.engine.busted else helpers.Colour.lime_green()
        else:
            colour = helpers.Colour.white()

        container = discord.ui.Container(accent_colour=colour)
        container.add_item(discord.ui.TextDisplay(f"## {_GEM} Mines"))
        container.add_item(discord.ui.TextDisplay(
            f"Bet: {Emojis.Economy.cash} **{fnumb(self.bet)}** • Mines: **{self.engine.mine_count}**/{TILES}\n"
            f"Gems found: **{self.engine.safe_revealed}** • Multiplier: **x{self.engine.multiplier:.2f}** • "
            f"Cash-out: {Emojis.Economy.cash} **{fnumb(self._potential())}**"
        ))
        if not self.finished:
            container.add_item(discord.ui.TextDisplay(
                f"-# Next gem pays **x{self.engine.next_multiplier():.2f}**"
            ))
        if self._status:
            container.add_item(discord.ui.TextDisplay(self._status))
        container.add_item(discord.ui.Separator())

        for r in range(ROWS):
            row = discord.ui.ActionRow()
            for c in range(COLS):
                row.add_item(self.tiles[r * COLS + c])
            container.add_item(row)

        if not self.finished:
            self.cash_out.disabled = self.engine.safe_revealed == 0
            container.add_item(discord.ui.ActionRow(self.cash_out))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"-# Player: {self.player}"))
        self.add_item(container)

    # -- outcomes ---------------------------------------------------------

    async def _record(self, interaction: discord.Interaction, result: GameResult, profit: int) -> None:
        assert interaction.guild is not None
        await interaction.client.db.game_stats.record_result(
            interaction.guild.id, self.player.id, Game.MINES, result, wagered=self.bet, profit=profit
        )

    async def _payout(self, interaction: discord.Interaction) -> int:
        payout = self._potential()
        assert interaction.guild is not None
        balance = await interaction.client.db.get_user_balance(self.player.id, interaction.guild.id)
        await balance.add(cash=payout)
        return payout

    async def on_reveal(self, interaction: discord.Interaction, index: int) -> None:
        if self.finished:
            await interaction.response.defer()
            return

        if self.engine.reveal(index):
            if self.engine.cleared:
                self.finished = True
                payout = await self._payout(interaction)
                self._status = (
                    f"`\N{SPARKLES} Board cleared!` Won {Emojis.Economy.cash} **{fnumb(payout)}** "
                    f"(x{self.engine.multiplier:.2f})."
                )
                self._compose()
                await interaction.response.edit_message(view=self)
                await self._record(interaction, GameResult.WIN, payout - self.bet)
                self.stop()
                return
            self._status = None
            self._compose()
            await interaction.response.edit_message(view=self)
        else:
            self.finished = True
            self._status = f"`\N{COLLISION SYMBOL} Boom!` You hit a mine and lost {Emojis.Economy.cash} **{fnumb(self.bet)}**."
            self._compose()
            await interaction.response.edit_message(view=self)
            await self._record(interaction, GameResult.LOSS, -self.bet)
            self.stop()

    async def _on_cash_out(self, interaction: discord.Interaction) -> None:
        if self.finished or self.engine.safe_revealed == 0:
            await interaction.response.defer()
            return

        self.finished = True
        payout = await self._payout(interaction)
        self._status = f"`\N{MONEY BAG} Cashed out` {Emojis.Economy.cash} **{fnumb(payout)}** (x{self.engine.multiplier:.2f})."
        self._compose()
        await interaction.response.edit_message(view=self)
        await self._record(interaction, GameResult.WIN, payout - self.bet)
        self.stop()
