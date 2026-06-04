from __future__ import annotations

import random
from typing import ClassVar

import discord

from app.core.views import LayoutView
from app.utils import fnumb, helpers
from config import Emojis

__all__ = ("Tower",)


class Tower(LayoutView):
    """Represents a tower building game with custom partially animated emojis."""

    GRASS: ClassVar[str] = "<:grass:1322337508381429904>"
    TOWER_BASE_O: ClassVar[str] = "<:tower_base_0:1322337520205168671>"
    TOWER_BASE_1: ClassVar[str] = "<:tower_base_1:1322337546151137391>"
    TOWER_BASE_0_BROKEN: ClassVar[str] = "<:tower_base_0_broken:1322337529084776599>"
    TOWER_BASE_1_BROKEN: ClassVar[str] = "<:tower_base_1_broken:1322337558591705088>"
    TOWER_BASE_0_FALLING: ClassVar[str] = "<a:tower_base_0_falling:1322337537834094712>"
    TOWER_BASE_1_FALLING: ClassVar[str] = "<a:tower_base_1_falling:1322337569865728042>"

    HOUSE: ClassVar[str] = "\N{HOUSE WITH GARDEN}"
    TREE1: ClassVar[str] = "\N{EVERGREEN TREE}"
    TREE2: ClassVar[str] = "\N{DECIDUOUS TREE}"

    def __init__(self, player: discord.Member, bet: int) -> None:
        super().__init__()
        self.player = player
        self.bet = bet

        self._stack = 0
        self.multiplier: float = 1.0
        self.finished: bool = False

        self.restart = discord.ui.Button(label="Restart", style=discord.ButtonStyle.green)
        self.restart.callback = self._on_restart  # type: ignore[assignment]
        self.stack = discord.ui.Button(
            label="Stack Tower (Increase Multiplier by 0.5)", style=discord.ButtonStyle.blurple
        )
        self.stack.callback = self._on_stack  # type: ignore[assignment]
        self.cash_out = discord.ui.Button(label="Cash Out", style=discord.ButtonStyle.red)
        self.cash_out.callback = self._on_cash_out  # type: ignore[assignment]

        self._compose()

    def __str__(self) -> str:
        return self.build()

    def add(self) -> None:
        self._stack += 1
        self.multiplier = (self._stack * 0.5) + 1

    def reset(self) -> None:
        self._stack = 0
        self.multiplier = 1.0
        self.finished = False

    def build(self, failed: bool = False) -> str:
        stacks = []
        parts = ["\n"]

        if failed:
            start = self.TOWER_BASE_0_BROKEN + self.TOWER_BASE_1_BROKEN
            self.finished = True
        else:
            start = self.TOWER_BASE_O + self.TOWER_BASE_1
            stacks = [start] * self._stack
            if self._stack != 0:
                stacks[0] = self.TOWER_BASE_0_FALLING + self.TOWER_BASE_1_FALLING

        parts.extend([Emojis.empty * 2 + tower for tower in stacks])
        parts.append(self.HOUSE + Emojis.empty + start + Emojis.empty + self.TREE1 + self.TREE2)
        parts.append(self.GRASS * 7)

        return "\n".join(parts)

    def build_container(self, failed: bool = False) -> discord.ui.Container:
        container = discord.ui.Container(accent_colour=helpers.Colour.white())
        container.add_item(discord.ui.TextDisplay(f"## \N{BUILDING CONSTRUCTION} Tower\n{self.build(failed)}"))
        container.add_item(discord.ui.TextDisplay(
            f"Bet: **{fnumb(self.bet)}** {Emojis.Economy.cash}\nMultiplier: **x{self.multiplier}**"
        ))

        if failed:
            container.add_item(discord.ui.TextDisplay("`\N{CROSS MARK} Tower has fallen!`"))
        if not failed and self.finished:
            container.add_item(discord.ui.TextDisplay(
                f"`\N{WHITE HEAVY CHECK MARK} Cashed out successfully with a multiplier of {self.multiplier}!`"
            ))

        container.add_item(discord.ui.TextDisplay(f"-# Player: {self.player}"))
        return container

    # View

    def _compose(self, failed: bool = False) -> None:
        """Recompose the layout: the tower card plus the appropriate control row.

        ``build_container`` is added first because ``build(failed=True)`` flips
        ``self.finished``, which decides whether the restart or stack/cash-out row shows.
        """
        self.clear_items()
        self.add_item(self.build_container(failed))
        if self.finished:
            self.add_item(discord.ui.ActionRow(self.restart))
        else:
            self.add_item(discord.ui.ActionRow(self.stack, self.cash_out))

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.user.id != self.player.id:
            await interaction.response.send_message(f"{Emojis.error} This isn't your game.", ephemeral=True)
            return False
        return True

    async def _on_restart(self, interaction: discord.Interaction) -> None:
        balance = await interaction.client.db.get_user_balance(interaction.user.id, interaction.guild.id)

        if self.bet > balance.cash:
            await interaction.response.send_message(
                f"{Emojis.error} You do not have enough money to bet that amount.\n"
                f"You currently have {Emojis.Economy.cash} **{fnumb(balance.cash)}** in **cash**.",
                ephemeral=True,
            )
            return

        await balance.remove(cash=self.bet)

        self.reset()
        self._compose()
        await interaction.response.edit_message(view=self)

    async def _on_stack(self, interaction: discord.Interaction) -> None:
        # Now calculate the probability of the tower crashing or not
        rate = random.uniform(0, 1)
        if rate > 0.7:
            # probability of 30% to crash
            self._compose(failed=True)
            await interaction.response.edit_message(view=self)
            return

        self.add()
        self._compose()
        await interaction.response.edit_message(view=self)

    async def _on_cash_out(self, interaction: discord.Interaction) -> None:
        self.finished = True
        balance = await interaction.client.db.get_user_balance(interaction.user.id, interaction.guild.id)
        amount = round(self.bet * self.multiplier)
        await balance.add(cash=amount)

        self._compose()
        await interaction.response.edit_message(view=self)

        await interaction.followup.send(
            f"\N{LEAF FLUTTERING IN WIND} You cashed out {Emojis.Economy.cash} **{fnumb(amount)}**.")
