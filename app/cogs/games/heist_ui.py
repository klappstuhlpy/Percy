"""UI for the group bank heist: a join window that resolves into one heist."""

from __future__ import annotations

import discord

from app.cogs.games.engine.heist import ENTRY_CASH, MAX_CREW, MIN_CREW, MIN_TARGET_BANK, resolve_heist, success_chance
from app.cogs.games.models import Game, GameResult
from app.core.views import LayoutView
from app.utils import fnumb, helpers
from config import Emojis

__all__ = ("HeistView",)

#: Seconds the crew has to assemble before the heist starts on its own.
JOIN_WINDOW: float = 60.0


class HeistView(LayoutView):
    """Crew assembly for a heist on ``target``'s bank.

    Anyone may join until the window closes or the leader starts early. Joiners are
    checked on the way in (cash to cover a fine, not passive, not the target), and
    the target's bank is re-read at resolution so the loot is what is there *then*.
    """

    def __init__(self, client: discord.Client, leader: discord.Member, target: discord.Member) -> None:
        super().__init__(timeout=JOIN_WINDOW)
        self.client = client
        self.leader = leader
        self.target = target
        self.crew: list[discord.Member] = [leader]
        self._result_text: str | None = None
        self._success: bool | None = None
        self._compose()

    def _compose(self) -> None:
        self.clear_items()
        if self._success is None:
            colour = helpers.Colour.brand()
        else:
            colour = helpers.Colour.lime_green() if self._success else helpers.Colour.light_red()
        container = discord.ui.Container(accent_colour=colour)
        container.add_item(discord.ui.TextDisplay(f"## \N{BANK} Heist on {self.target.display_name}'s bank"))
        crew = ", ".join(m.mention for m in self.crew)
        container.add_item(discord.ui.TextDisplay(
            f"**Crew ({len(self.crew)}/{MAX_CREW}):** {crew}\n"
            f"Success chance: **{success_chance(len(self.crew)):.0%}** • Need **{MIN_CREW}+** members "
            f"carrying {Emojis.Economy.cash} **{fnumb(ENTRY_CASH)}** each"
        ))
        container.add_item(discord.ui.Separator())
        if self._result_text:
            container.add_item(discord.ui.TextDisplay(self._result_text))
        else:
            join = discord.ui.Button(label="Join the crew", style=discord.ButtonStyle.green, emoji="\N{SLEUTH OR SPY}")
            join.callback = self._join  # type: ignore[assignment]
            start = discord.ui.Button(label="Start now", style=discord.ButtonStyle.red, emoji="\N{BOMB}")
            start.callback = self._start  # type: ignore[assignment]
            start.disabled = len(self.crew) < MIN_CREW
            container.add_item(discord.ui.TextDisplay("-# The heist starts on its own when the window closes."))
            container.add_item(discord.ui.ActionRow(join, start))
        self.add_item(container)

    async def _join(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        assert isinstance(member, discord.Member)
        db = self.client.db  # type: ignore[attr-defined]
        error = None
        if member.bot or member.id == self.target.id:
            error = "You can't join this heist."
        elif any(m.id == member.id for m in self.crew):
            error = "You're already in the crew."
        elif len(self.crew) >= MAX_CREW:
            error = "The crew is full."
        elif await db.economy.is_passive(member.id, member.guild.id):
            error = "You're in passive mode — turn it off with `passive off` to join heists."
        elif (await db.get_user_balance(member.id, member.guild.id)).cash < ENTRY_CASH:
            error = f"You need {Emojis.Economy.cash} **{fnumb(ENTRY_CASH)}** in cash to join a heist."
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        self.crew.append(member)
        self._compose()
        await interaction.response.edit_message(view=self)

    async def _start(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.leader.id:
            await interaction.response.send_message("Only the heist leader can start early.", ephemeral=True)
            return
        if len(self.crew) < MIN_CREW or self._result_text:
            await interaction.response.defer()
            return
        await interaction.response.defer()  # resolution makes several DB round-trips
        await self._resolve()
        await interaction.edit_original_response(view=self)

    async def _resolve(self) -> None:
        self.stop()
        guild_id = self.target.guild.id
        db = self.client.db  # type: ignore[attr-defined]
        cash = Emojis.Economy.cash
        if len(self.crew) < MIN_CREW:
            self._result_text = "Nobody showed up. The heist was called off."
            self._compose()
            return

        target_balance = await db.get_user_balance(self.target.id, guild_id)
        if target_balance.bank < MIN_TARGET_BANK:
            self._result_text = f"**{self.target.display_name}** emptied their bank in time. Nothing worth taking."
            self._compose()
            return

        result = resolve_heist(len(self.crew), target_balance.bank)
        if result.success:
            await target_balance.remove(bank=result.loot)
            for member in self.crew:
                await (await db.get_user_balance(member.id, guild_id)).add(cash=result.share)
                await db.game_stats.record_result(
                    guild_id, member.id, Game.HEIST, GameResult.WIN, wagered=0, profit=result.share
                )
            self._success = True
            self._result_text = (
                f"\N{MONEY BAG} The crew cracked the vault and got away with {cash} **{fnumb(result.loot)}** — "
                f"{cash} **{fnumb(result.share)}** each."
            )
        else:
            lines = []
            for member in self.crew:
                balance = await db.get_user_balance(member.id, guild_id)
                fine = round(max(balance.cash, 0) * result.fine_fraction)
                if fine:
                    await balance.remove(cash=fine)
                await db.game_stats.record_result(
                    guild_id, member.id, Game.HEIST, GameResult.LOSS, wagered=0, profit=-fine
                )
                lines.append(f"{member.mention} paid {cash} **{fnumb(fine)}**")
            self._success = False
            self._result_text = "\N{POLICE CARS REVOLVING LIGHT} The alarm went off! Everyone got caught.\n" + "\n".join(lines)
        self._compose()

    async def on_timeout(self) -> None:
        if self._result_text:
            return
        await self._resolve()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
