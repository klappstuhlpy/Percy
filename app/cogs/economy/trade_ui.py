"""Components V2 trade card: two members put items and cash on the table, both confirm, one transaction runs it.

Every edit to either offer resets both confirmations, so nobody confirms a deal
that changed under them. Execution goes through the cog's ``trade_action`` (one
repository transaction that re-checks every debit), and the card is only ever
a view of the two offers — the database is the authority.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord

from app.core import LayoutView
from app.services.economy import MAX_TRADE_LINES, TradeOffer, summarize_offer
from app.utils import fnumb, helpers
from config import Emojis

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.cogs.economy.cog import Economy

__all__ = ('TradeView',)

#: A select menu in a modal holds at most 25 options.
SELECT_LIMIT = 25


class _AddItemModal(discord.ui.Modal, title='Add an item'):
    def __init__(self, options: list[discord.SelectOption], on_add: Callable[[discord.Interaction, int, int], Awaitable[None]]) -> None:
        super().__init__(timeout=120.0)
        self.on_add = on_add
        self.item = discord.ui.Select(options=options, placeholder='Pick an item…')
        self.amount = discord.ui.TextInput(default='1', max_length=9)
        self.add_item(discord.ui.Label(text='Item', component=self.item))
        self.add_item(discord.ui.Label(text='Quantity', component=self.amount))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.amount.value.strip()
        quantity = int(raw) if raw.isdigit() else 0
        if not self.item.values or quantity < 1:
            await interaction.response.send_message(f'{Emojis.error} Pick an item and enter a whole number.', ephemeral=True)
            return
        await self.on_add(interaction, int(self.item.values[0]), quantity)


class _CashModal(discord.ui.Modal, title='Offer cash'):
    def __init__(self, on_cash: Callable[[discord.Interaction, int], Awaitable[None]]) -> None:
        super().__init__(timeout=120.0)
        self.on_cash = on_cash
        self.amount = discord.ui.TextInput(default='0', placeholder='0 removes the cash from your offer', max_length=12)
        self.add_item(discord.ui.Label(text='Cash', component=self.amount))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.amount.value.strip().replace(',', '')
        if not raw.isdigit():
            await interaction.response.send_message(f'{Emojis.error} Enter a whole number.', ephemeral=True)
            return
        await self.on_cash(interaction, int(raw))


class _RemoveModal(discord.ui.Modal, title='Remove from your offer'):
    def __init__(self, options: list[discord.SelectOption], on_remove: Callable[[discord.Interaction, list[str]], Awaitable[None]]) -> None:
        super().__init__(timeout=120.0)
        self.on_remove = on_remove
        self.lines = discord.ui.Select(options=options, min_values=1, max_values=len(options), placeholder='Pick lines to remove…')
        self.add_item(discord.ui.Label(text='Lines', component=self.lines))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.on_remove(interaction, list(self.lines.values))


class TradeView(LayoutView):
    """The trade between ``a`` (who opened it) and ``b``; only those two may click."""

    def __init__(self, cog: Economy, a: discord.Member, b: discord.Member) -> None:
        super().__init__(timeout=300.0, members=[a, b], disable_on_timeout=True)
        self.cog = cog
        self.a, self.b = a, b
        self.offers = {a.id: TradeOffer(), b.id: TradeOffer()}
        self.notice: str | None = None
        self._busy = False

    @property
    def guild(self) -> discord.Guild:
        return self.a.guild

    # -- rendering -------------------------------------------------------------

    async def _names(self) -> dict[int, str]:
        return {row['id']: row['name'] for row in await self.cog.bot.db.economy.get_items(self.guild.id)}

    def _side(self, member: discord.Member, names: dict[int, str]) -> str:
        offer = self.offers[member.id]
        cash = f'{Emojis.Economy.cash} **{fnumb(offer.cash)}**' if offer.cash else ''
        mark = '\N{WHITE HEAVY CHECK MARK} confirmed' if offer.confirmed else '\N{HOURGLASS WITH FLOWING SAND} deciding'
        return f'### {member.mention} — {mark}\n{summarize_offer(offer, names, cash)}'

    async def build(self) -> None:
        names = await self._names()
        container = discord.ui.Container(accent_colour=helpers.Colour.brand())
        container.add_item(discord.ui.TextDisplay(
            f'## \N{HANDSHAKE} Trade\n{self.a.mention} and {self.b.mention}: add items or cash, then both confirm.\n'
            f'-# Any change resets both confirmations. Nothing moves until you both confirm.'
        ))
        if self.notice:
            container.add_item(discord.ui.TextDisplay(self.notice))
            self.notice = None
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(self._side(self.a, names)))
        container.add_item(discord.ui.TextDisplay(self._side(self.b, names)))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(
            self._button('Add item', self._add_item, emoji='\N{PACKAGE}'),
            self._button('Add cash', self._add_cash, emoji=Emojis.Economy.cash),
            self._button('Remove…', self._remove),
        ))
        container.add_item(discord.ui.ActionRow(
            self._button('Confirm', self._confirm, style=discord.ButtonStyle.success),
            self._button('Cancel', self._cancel, style=discord.ButtonStyle.danger),
        ))
        self.clear_items()
        self.add_item(container)

    def _button(self, label: str, callback: Callable[[discord.Interaction], Awaitable[None]], *,
                style: discord.ButtonStyle = discord.ButtonStyle.secondary, emoji: str | None = None) -> discord.ui.Button:
        button: discord.ui.Button = discord.ui.Button(label=label, style=style, emoji=emoji)
        button.callback = callback  # type: ignore[assignment]
        return button

    async def _refresh(self, interaction: discord.Interaction, notice: str | None = None) -> None:
        self.notice = notice
        await self.build()
        await interaction.response.edit_message(view=self)

    def _changed(self) -> None:
        for offer in self.offers.values():
            offer.confirmed = False

    async def _reject(self, interaction: discord.Interaction, message: str) -> None:
        await interaction.response.send_message(f'{Emojis.error} {message}', ephemeral=True)

    # -- editing an offer ---------------------------------------------------------

    async def _add_item(self, interaction: discord.Interaction) -> None:
        offer = self.offers[interaction.user.id]
        rows = await self.cog.bot.db.economy.get_inventory(interaction.user.id, self.guild.id)
        options = [
            discord.SelectOption(label=str(row['name'])[:100], value=str(row['item_id']), description=f'You own ×{row["quantity"]:,}')
            for row in rows[:SELECT_LIMIT]
        ]
        if not options:
            await self._reject(interaction, 'You have no items to offer.')
            return

        async def on_add(modal_interaction: discord.Interaction, item_id: int, quantity: int) -> None:
            owned = next((r['quantity'] for r in rows if r['item_id'] == item_id), 0)
            if item_id not in offer.items and offer.lines >= MAX_TRADE_LINES:
                await self._reject(modal_interaction, f'An offer holds at most {MAX_TRADE_LINES} lines.')
            elif quantity > owned:
                await self._reject(modal_interaction, f'You only own ×{owned:,} of that.')
            else:
                offer.items[item_id] = quantity  # setting, not adding: re-adding corrects the amount
                self._changed()
                await self._refresh(modal_interaction)

        await interaction.response.send_modal(_AddItemModal(options, on_add))

    async def _add_cash(self, interaction: discord.Interaction) -> None:
        offer = self.offers[interaction.user.id]

        async def on_cash(modal_interaction: discord.Interaction, amount: int) -> None:
            balance = await self.cog.bot.db.get_user_balance(modal_interaction.user.id, self.guild.id)
            if amount > balance.cash:
                await self._reject(modal_interaction, f'You only have {Emojis.Economy.cash} {fnumb(balance.cash)} in hand.')
            elif amount and not offer.cash and offer.lines >= MAX_TRADE_LINES:
                await self._reject(modal_interaction, f'An offer holds at most {MAX_TRADE_LINES} lines.')
            else:
                offer.cash = amount
                self._changed()
                await self._refresh(modal_interaction)

        await interaction.response.send_modal(_CashModal(on_cash))

    async def _remove(self, interaction: discord.Interaction) -> None:
        offer = self.offers[interaction.user.id]
        if offer.empty:
            await self._reject(interaction, 'Your offer is empty.')
            return
        names = await self._names()
        options = [discord.SelectOption(label=names.get(i, 'Unknown item')[:100], value=str(i)) for i in offer.items]
        if offer.cash:
            options.append(discord.SelectOption(label=f'{offer.cash:,} cash', value='cash'))

        async def on_remove(modal_interaction: discord.Interaction, values: list[str]) -> None:
            for value in values:
                if value == 'cash':
                    offer.cash = 0
                else:
                    offer.items.pop(int(value), None)
            self._changed()
            await self._refresh(modal_interaction)

        await interaction.response.send_modal(_RemoveModal(options, on_remove))

    # -- finishing ------------------------------------------------------------------

    async def _finish(self, interaction: discord.Interaction, text: str, colour: Any) -> None:
        self.clear_items()
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(text), accent_colour=colour))
        self.stop()
        await interaction.response.edit_message(view=self)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        if self._busy:
            await self._reject(interaction, 'The trade is being processed.')
            return
        if all(offer.empty for offer in self.offers.values()):
            await self._reject(interaction, 'Nobody has offered anything yet.')
            return
        self.offers[interaction.user.id].confirmed = True
        if not all(offer.confirmed for offer in self.offers.values()):
            await self._refresh(interaction)
            return

        self._busy = True
        try:
            ok, message = await self.cog.trade_action(self.a, self.b, self.offers[self.a.id], self.offers[self.b.id])
        finally:
            self._busy = False
        if ok:
            await self._finish(interaction, f'{Emojis.success} {message}', discord.Colour.green())
        else:
            self._changed()
            await self._refresh(interaction, f'{Emojis.error} {message}')

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await self._finish(
            interaction, f'{Emojis.error} Trade cancelled by **{interaction.user.display_name}**.', discord.Colour.red()
        )
