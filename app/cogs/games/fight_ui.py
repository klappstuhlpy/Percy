"""UI for the PvP fight: an accept prompt that turns into the turn-based fight itself."""

from __future__ import annotations

import discord

from app.cogs.games.engine.fight import MAX_HP, Action, Fight, Turn
from app.cogs.games.models import Game, GameResult
from app.core.views import LayoutView
from app.utils import fnumb, helpers
from config import Emojis

__all__ = ("FightView",)

_GLOVE = "\N{BOXING GLOVE}"


def _hp_bar(hp: int, width: int = 10) -> str:
    filled = round(hp / MAX_HP * width)
    return "\N{LARGE GREEN SQUARE}" * filled + "\N{BLACK LARGE SQUARE}" * (width - filled)


class FightView(LayoutView):
    """Challenge → fight. Nobody is debited until the opponent accepts.

    On accept both stakes are re-validated and taken; the winner gets the pot.
    Idling on your turn for the timeout forfeits, exactly like fleeing.
    """

    def __init__(self, client: discord.Client, challenger: discord.Member, opponent: discord.Member, bet: int) -> None:
        super().__init__(timeout=60.0, members=(challenger, opponent))
        self.client = client
        self.fighters = (challenger, opponent)
        self.bet = bet
        self.engine: Fight | None = None
        self.log: list[str] = []
        self._closed_text: str | None = None
        self._compose()

    # -- rendering --------------------------------------------------------

    def _button(self, label: str, style: discord.ButtonStyle, emoji: str, callback) -> discord.ui.Button:
        button = discord.ui.Button(label=label, style=style, emoji=emoji)
        button.callback = callback  # type: ignore[assignment]
        return button

    def _compose(self) -> None:
        self.clear_items()
        a, b = self.fighters
        stake = f" for {Emojis.Economy.cash} **{fnumb(self.bet)}** each" if self.bet else ""
        engine = self.engine
        colour = helpers.Colour.lime_green() if engine and engine.finished else helpers.Colour.brand()
        container = discord.ui.Container(accent_colour=colour)
        container.add_item(discord.ui.TextDisplay(f"## {_GLOVE} Fight"))

        if self._closed_text:
            container.add_item(discord.ui.TextDisplay(self._closed_text))
        elif engine is None:
            container.add_item(discord.ui.TextDisplay(
                f"{a.mention} challenges {b.mention} to a fight{stake}. Do you accept?"
            ))
            container.add_item(discord.ui.ActionRow(
                self._button("Accept", discord.ButtonStyle.green, "\N{HEAVY CHECK MARK}", self._accept),
                self._button("Decline", discord.ButtonStyle.red, "\N{HEAVY MULTIPLICATION X}", self._decline),
            ))
        else:
            container.add_item(discord.ui.TextDisplay(
                "\n".join(
                    f"**{f.display_name}** {'\N{SHIELD}' if engine.guarding[i] else ''}\n"
                    f"{_hp_bar(engine.hp[i])} `{engine.hp[i]}/{MAX_HP}`"
                    for i, f in enumerate(self.fighters)
                )
            ))
            if self.log:
                container.add_item(discord.ui.Separator())
                container.add_item(discord.ui.TextDisplay("\n".join(self.log[-4:])))
            container.add_item(discord.ui.Separator())
            if engine.finished:
                assert engine.winner is not None
                winner = self.fighters[engine.winner]
                prize = f" and takes {Emojis.Economy.cash} **{fnumb(self.bet * 2)}**" if self.bet else ""
                container.add_item(discord.ui.TextDisplay(f"\N{TROPHY} **{winner.display_name}** wins{prize}!"))
            else:
                container.add_item(discord.ui.TextDisplay(f"{self.fighters[engine.turn].mention}, your move."))
                container.add_item(discord.ui.ActionRow(
                    self._button("Punch", discord.ButtonStyle.red, _GLOVE, self._punch),
                    self._button("Defend", discord.ButtonStyle.blurple, "\N{SHIELD}", self._defend),
                    self._button("Flee", discord.ButtonStyle.secondary, "\N{RUNNER}", self._flee),
                ))
        self.add_item(container)

    @staticmethod
    def _describe(turn: Turn, actor: discord.Member, target: discord.Member) -> str:
        if turn.action is Action.FLEE:
            return f"\N{RUNNER} **{actor.display_name}** ran away."
        if turn.action is Action.DEFEND:
            heal = f" and patched up **{turn.heal}** HP" if turn.heal else ""
            return f"\N{SHIELD} **{actor.display_name}** raised a guard{heal}."
        extra = " **Critical hit!**" if turn.crit else ""
        extra += " *(blocked half)*" if turn.blocked else ""
        return f"{_GLOVE} **{actor.display_name}** hit **{target.display_name}** for **{turn.damage}**.{extra}"

    # -- challenge phase --------------------------------------------------

    async def _accept(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.fighters[1].id:
            await interaction.response.send_message("Only the challenged member can accept.", ephemeral=True)
            return
        assert interaction.guild is not None
        if self.bet:
            balances = [await self.client.db.get_user_balance(f.id, interaction.guild.id) for f in self.fighters]  # type: ignore[attr-defined]
            for fighter, balance in zip(self.fighters, balances, strict=True):
                if balance.cash < self.bet:
                    self._closed_text = f"**{fighter.display_name}** cannot cover the stake anymore — fight cancelled."
                    self.stop()
                    self._compose()
                    await interaction.response.edit_message(view=self)
                    return
            for balance in balances:
                await balance.remove(cash=self.bet)
        self.engine = Fight()
        self._compose()
        await interaction.response.edit_message(view=self)

    async def _decline(self, interaction: discord.Interaction) -> None:
        if self.engine is not None:
            await interaction.response.defer()
            return
        who = interaction.user.display_name  # either side may call it off before it starts
        self._closed_text = f"**{who}** called the fight off."
        self.stop()
        self._compose()
        await interaction.response.edit_message(view=self)

    # -- fight phase ------------------------------------------------------

    async def _act(self, interaction: discord.Interaction, action: Action) -> None:
        engine = self.engine
        if engine is None or engine.finished:
            await interaction.response.defer()
            return
        if interaction.user.id != self.fighters[engine.turn].id:
            await interaction.response.send_message("It's not your turn.", ephemeral=True)
            return
        actor, target = self.fighters[engine.turn], self.fighters[1 - engine.turn]
        self.log.append(self._describe(engine.act(action), actor, target))
        self._compose()
        await interaction.response.edit_message(view=self)
        if engine.finished:
            assert interaction.guild is not None
            await self._settle(interaction.guild.id)

    async def _punch(self, interaction: discord.Interaction) -> None:
        await self._act(interaction, Action.PUNCH)

    async def _defend(self, interaction: discord.Interaction) -> None:
        await self._act(interaction, Action.DEFEND)

    async def _flee(self, interaction: discord.Interaction) -> None:
        await self._act(interaction, Action.FLEE)

    async def _settle(self, guild_id: int) -> None:
        self.stop()
        assert self.engine is not None and self.engine.winner is not None
        db = self.client.db  # type: ignore[attr-defined]
        winner, loser = self.fighters[self.engine.winner], self.fighters[1 - self.engine.winner]
        if self.bet:
            balance = await db.get_user_balance(winner.id, guild_id)
            await balance.add(cash=self.bet * 2)
        await db.game_stats.record_result(guild_id, winner.id, Game.FIGHT, GameResult.WIN, wagered=self.bet, profit=self.bet)
        await db.game_stats.record_result(guild_id, loser.id, Game.FIGHT, GameResult.LOSS, wagered=self.bet, profit=-self.bet)

    async def on_timeout(self) -> None:
        if self.message is None or self.message.guild is None or self._closed_text:
            return
        engine = self.engine
        if engine is None:
            self._closed_text = f"**{self.fighters[1].display_name}** never answered — challenge expired."
        elif not engine.finished:
            idler = self.fighters[engine.turn]
            engine.act(Action.FLEE)
            self.log.append(f"\N{ALARM CLOCK} **{idler.display_name}** took too long and forfeits.")
            await self._settle(self.message.guild.id)
        else:
            return
        self._compose()
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass
