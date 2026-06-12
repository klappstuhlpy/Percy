from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

import discord

from app.cogs.games.models import Game, GameResult
from app.core.views import LayoutView
from app.utils import fnumb, helpers, pluralize
from config import Emojis

if TYPE_CHECKING:
    from app.cogs.games.cog import Games
    from app.core.models import Context

__all__ = ("CHAMBERS", "MAX_PLAYERS", "RussianRoulette")

CHAMBERS: int = 6
MAX_PLAYERS: int = 6


class Phase(Enum):
    LOBBY = auto()
    PLAYING = auto()
    ENDED = auto()


@dataclass
class RRPlayer:
    member: discord.Member
    alive: bool = True


class RussianRoulette(LayoutView):
    """Multiplayer Russian Roulette lobby + game.

    Each player antes into a shared pot when they join. On their turn a player pulls
    the trigger with a ``1/CHAMBERS`` chance of being eliminated; the last survivor
    takes the whole pot. The cog debits the host's ante and registers the table; this
    view debits/refunds joiners and credits the winner.
    """

    def __init__(self, cog: Games, ctx: Context, ante: int) -> None:
        super().__init__(timeout=600.0)
        self.cog = cog
        self.ctx = ctx
        self.ante = ante
        self.host = ctx.author
        self.players: list[RRPlayer] = [RRPlayer(ctx.author)]  # type: ignore[arg-type]
        self.phase = Phase.LOBBY
        self._rng = random.Random()
        self.order: list[RRPlayer] = []
        self.turn: int = 0
        self._status: str | None = None

        self.join = discord.ui.Button(label="Join", style=discord.ButtonStyle.green, emoji=Emojis.join)
        self.join.callback = self._on_join  # type: ignore[assignment]
        self.leave = discord.ui.Button(label="Leave", style=discord.ButtonStyle.grey, emoji=Emojis.leave)
        self.leave.callback = self._on_leave  # type: ignore[assignment]
        self.start = discord.ui.Button(label="Start", style=discord.ButtonStyle.blurple)
        self.start.callback = self._on_start  # type: ignore[assignment]
        self.pull = discord.ui.Button(label="Pull Trigger", style=discord.ButtonStyle.red, emoji="\N{PISTOL}")
        self.pull.callback = self._on_pull  # type: ignore[assignment]

        self._compose()

    # -- helpers ----------------------------------------------------------

    @property
    def pot(self) -> int:
        return self.ante * len(self.players)

    @property
    def current(self) -> RRPlayer | None:
        return self.order[self.turn] if self.order else None

    def _find(self, user: discord.abc.User) -> RRPlayer | None:
        return next((p for p in self.players if p.member.id == user.id), None)

    # The base view is open to everyone (no ``members`` gate); per-button checks
    # below enforce host-only start and current-player-only trigger pulls.

    # -- rendering --------------------------------------------------------

    def _compose(self) -> None:
        self.clear_items()
        colour = {
            Phase.LOBBY: helpers.Colour.white(),
            Phase.PLAYING: helpers.Colour.light_orange(),
            Phase.ENDED: helpers.Colour.lime_green(),
        }[self.phase]

        container = discord.ui.Container(accent_colour=colour)
        container.add_item(discord.ui.TextDisplay("## \N{PISTOL} Russian Roulette"))
        container.add_item(discord.ui.TextDisplay(
            f"Ante: {Emojis.Economy.cash} **{fnumb(self.ante)}** • Pot: {Emojis.Economy.cash} **{fnumb(self.pot)}** • "
            f"Chamber odds: **1/{CHAMBERS}**"
        ))
        container.add_item(discord.ui.Separator())

        if self.phase is Phase.LOBBY:
            roster = "\n".join(
                f"{Emojis.success} {p.member.mention}{' \N{CROWN}' if p.member.id == self.host.id else ''}"
                for p in self.players
            )
            container.add_item(discord.ui.TextDisplay(roster))
            container.add_item(discord.ui.TextDisplay(f"-# {pluralize(len(self.players)):player} in the lobby (max {MAX_PLAYERS})."))
            container.add_item(discord.ui.Separator())
            self.start.disabled = len(self.players) < 2
            container.add_item(discord.ui.ActionRow(self.join, self.leave, self.start))
        else:
            lines = []
            for p in self.players:
                marker = "\N{SKULL}" if not p.alive else ("\N{PISTOL}" if p is self.current else "\N{LARGE GREEN CIRCLE}")
                lines.append(f"{marker} {p.member.mention}")
            container.add_item(discord.ui.TextDisplay("\n".join(lines)))
            if self._status:
                container.add_item(discord.ui.TextDisplay(self._status))
            container.add_item(discord.ui.Separator())

            if self.phase is Phase.PLAYING and self.current is not None:
                container.add_item(discord.ui.TextDisplay(f"\N{PISTOL} It's {self.current.member.mention}'s turn."))
                container.add_item(discord.ui.ActionRow(self.pull))
            elif self.phase is Phase.ENDED:
                container.add_item(discord.ui.TextDisplay("*Game over.*"))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"-# Host: {self.host}"))
        self.add_item(container)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self._compose()
        await interaction.response.edit_message(view=self)

    # -- lobby ------------------------------------------------------------

    async def _on_join(self, interaction: discord.Interaction) -> None:
        if self.phase is not Phase.LOBBY:
            await interaction.response.send_message(f"{Emojis.error} The game has already started.", ephemeral=True)
            return
        if self._find(interaction.user):
            await interaction.response.send_message(f"{Emojis.error} You're already in the lobby.", ephemeral=True)
            return
        if len(self.players) >= MAX_PLAYERS:
            await interaction.response.send_message(f"{Emojis.error} The lobby is full.", ephemeral=True)
            return

        assert interaction.guild is not None
        balance = await interaction.client.db.get_user_balance(interaction.user.id, interaction.guild.id)
        if balance.cash < self.ante:
            await interaction.response.send_message(
                f"{Emojis.error} You need {Emojis.Economy.cash} **{fnumb(self.ante)}** to join.", ephemeral=True
            )
            return

        await balance.remove(cash=self.ante)
        self.players.append(RRPlayer(interaction.user))  # type: ignore[arg-type]
        await self._refresh(interaction)

    async def _on_leave(self, interaction: discord.Interaction) -> None:
        if self.phase is not Phase.LOBBY:
            await interaction.response.send_message(f"{Emojis.error} You can't leave mid-game.", ephemeral=True)
            return
        player = self._find(interaction.user)
        if player is None:
            await interaction.response.send_message(f"{Emojis.error} You're not in the lobby.", ephemeral=True)
            return

        assert interaction.guild is not None
        balance = await interaction.client.db.get_user_balance(interaction.user.id, interaction.guild.id)
        await balance.add(cash=self.ante)

        if interaction.user.id == self.host.id:
            # Host left — refund everyone else and close the table.
            for other in self.players:
                if other.member.id != self.host.id:
                    other_balance = await interaction.client.db.get_user_balance(other.member.id, interaction.guild.id)
                    await other_balance.add(cash=self.ante)
            self.phase = Phase.ENDED
            self._status = "`\N{CROSS MARK}` The host left — antes refunded."
            self.cog.russian_tables.pop(self.ctx.channel.id, None)
            self._compose()
            await interaction.response.edit_message(view=self)
            self.stop()
            return

        self.players.remove(player)
        await self._refresh(interaction)

    async def _on_start(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.host.id:
            await interaction.response.send_message(f"{Emojis.error} Only the host can start.", ephemeral=True)
            return
        if len(self.players) < 2:
            await interaction.response.send_message(f"{Emojis.error} Need at least 2 players.", ephemeral=True)
            return

        self.phase = Phase.PLAYING
        self.order = list(self.players)
        self.turn = 0
        self._status = "The cylinder spins... \N{PISTOL}"
        await self._refresh(interaction)

    # -- gameplay ---------------------------------------------------------

    async def _on_pull(self, interaction: discord.Interaction) -> None:
        if self.phase is not Phase.PLAYING:
            await interaction.response.send_message(f"{Emojis.error} The game isn't running.", ephemeral=True)
            return
        current = self.current
        if current is None or interaction.user.id != current.member.id:
            await interaction.response.send_message(f"{Emojis.error} It's not your turn.", ephemeral=True)
            return

        bang = self._rng.randrange(CHAMBERS) == 0
        if bang:
            current.alive = False
            self.order.pop(self.turn)
            if len(self.order) == 1:
                await self._finish(interaction, eliminated=current)
                return
            if self.turn >= len(self.order):
                self.turn = 0
            self._status = f"\N{COLLISION SYMBOL} **BANG!** {current.member.mention} is out. The cylinder reloads."
        else:
            self._status = f"\N{WHITE HEAVY CHECK MARK} *click* — {current.member.mention} survives."
            self.turn = (self.turn + 1) % len(self.order)

        await self._refresh(interaction)

    async def _finish(self, interaction: discord.Interaction, eliminated: RRPlayer) -> None:
        winner = self.order[0]
        self.phase = Phase.ENDED
        assert interaction.guild is not None

        balance = await interaction.client.db.get_user_balance(winner.member.id, interaction.guild.id)
        await balance.add(cash=self.pot)
        self._status = (
            f"\N{COLLISION SYMBOL} **BANG!** {eliminated.member.mention} is out.\n"
            f"\N{TROPHY} {winner.member.mention} is the last one standing and wins "
            f"{Emojis.Economy.cash} **{fnumb(self.pot)}**!"
        )

        game_stats = interaction.client.db.game_stats
        for player in self.players:
            result = GameResult.WIN if player.member.id == winner.member.id else GameResult.LOSS
            profit = (self.pot - self.ante) if result is GameResult.WIN else -self.ante
            await game_stats.record_result(
                interaction.guild.id, player.member.id, Game.RUSSIAN_ROULETTE, result, wagered=self.ante, profit=profit
            )

        self.cog.russian_tables.pop(self.ctx.channel.id, None)
        self._compose()
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self) -> None:
        # Lobby that never started: refund everyone so no cash is stranded.
        if self.phase is Phase.LOBBY and self.ctx.guild is not None:
            for player in self.players:
                balance = await self.ctx.bot.db.get_user_balance(player.member.id, self.ctx.guild.id)
                await balance.add(cash=self.ante)
        self.cog.russian_tables.pop(self.ctx.channel.id, None)
