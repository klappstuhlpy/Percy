"""UI for the coinflip duel: an accept/decline prompt that runs the flip on accept."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

import discord

from app.cogs.games.models import Game, GameResult
from app.core import LayoutView
from app.core.components_v2 import Accent, make_notice
from app.utils import fnumb, helpers
from config import Emojis

if TYPE_CHECKING:
    from app.cogs.games.cog import Games
    from app.core.models import Context

__all__ = ('COIN', 'SIDES', 'DuelPrompt')

COIN = '\N{COIN}'
SIDES = ('heads', 'tails')


class DuelPrompt(LayoutView):
    """Asks the challenged member to accept a coinflip duel.

    Nobody is debited until the duel is accepted: on accept both stakes are
    re-validated and taken, the coin is flipped, and the winner takes the whole
    pot. Declining or timing out costs neither side anything.
    """

    def __init__(self, cog: Games, ctx: Context, opponent: discord.Member, bet: int, side: str) -> None:
        super().__init__(timeout=60.0, members=opponent)
        self.cog = cog
        self.ctx = ctx
        self.opponent = opponent
        self.bet = bet
        self.side = side  # the challenger's call; the opponent gets the other side
        self.message: discord.Message | None = None

        accept_btn = discord.ui.Button(label='Accept', style=discord.ButtonStyle.green)
        accept_btn.callback = self._accept  # type: ignore[assignment]

        decline_btn = discord.ui.Button(label='Decline', style=discord.ButtonStyle.red)
        decline_btn.callback = self._decline  # type: ignore[assignment]

        other = SIDES[1 - SIDES.index(side)]
        container = discord.ui.Container(accent_colour=helpers.Colour.brand())
        container.add_item(discord.ui.TextDisplay(
            f'## {COIN} Coinflip Duel\n'
            f'{ctx.author.mention} challenges {opponent.mention} to a coinflip for '
            f'{Emojis.Economy.cash} **{fnumb(bet)}** each — winner takes the pot.\n'
            f'{ctx.author.mention} called **{side}**, so you get **{other}**. Do you accept?'
        ))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(accept_btn, decline_btn))
        self.add_item(container)

    async def _accept(self, interaction: discord.Interaction) -> None:
        self.stop()
        assert self.ctx.guild is not None
        guild_id = self.ctx.guild.id
        challenger = self.ctx.author

        challenger_balance = await self.ctx.db.get_user_balance(challenger.id, guild_id)
        opponent_balance = await self.ctx.db.get_user_balance(self.opponent.id, guild_id)
        assert challenger_balance is not None and opponent_balance is not None

        for member, balance in ((challenger, challenger_balance), (self.opponent, opponent_balance)):
            if self.bet > balance.cash:
                await interaction.response.edit_message(view=make_notice(
                    f'{COIN} Coinflip Duel',
                    f'**{member.display_name}** cannot cover the stake anymore — duel cancelled.',
                    accent=Accent.error,
                ))
                return

        await challenger_balance.remove(cash=self.bet)
        await opponent_balance.remove(cash=self.bet)

        result = random.choice(SIDES)
        challenger_won = result == self.side
        winner, winner_balance = (
            (challenger, challenger_balance) if challenger_won else (self.opponent, opponent_balance)
        )
        await winner_balance.add(cash=self.bet * 2)

        stats = self.cog.bot.db.game_stats
        await stats.record_result(
            guild_id, challenger.id, Game.COINFLIP,
            GameResult.WIN if challenger_won else GameResult.LOSS,
            wagered=self.bet, profit=self.bet if challenger_won else -self.bet,
        )
        await stats.record_result(
            guild_id, self.opponent.id, Game.COINFLIP,
            GameResult.LOSS if challenger_won else GameResult.WIN,
            wagered=self.bet, profit=-self.bet if challenger_won else self.bet,
        )

        await interaction.response.edit_message(view=make_notice(
            f'{COIN} Coinflip Duel',
            f'The coin lands on **{result}** — {winner.mention} takes the pot of '
            f'{Emojis.Economy.cash} **{fnumb(self.bet * 2)}**!',
            accent=Accent.success,
        ))

    async def _decline(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(view=make_notice(
            f'{COIN} Coinflip Duel',
            f'{self.opponent.mention} declined the duel. No coins changed hands.',
            accent=Accent.error,
        ))

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(view=make_notice(
                    f'{COIN} Coinflip Duel',
                    f'{self.opponent.mention} did not answer in time. No coins changed hands.',
                    accent=Accent.neutral,
                ))
            except discord.HTTPException:
                pass
