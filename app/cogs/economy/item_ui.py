"""Components V2 item browser: the shop and inventory as one interactive card.

One :class:`ItemBrowser` serves both surfaces. The list page shows a header with
totals, one section per item (with a *View* button), a category filter and a
pager; the detail page shows a single item and the actions that make sense for
the viewer — buy in the shop, use/sell/gift in your own inventory, nothing in
someone else's. Every action runs through the cog's shared ``*_item_action``
methods (the same ones the text commands call) and the card re-reads the
database afterwards, so it always shows live numbers.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any, Literal

import discord

from app.cogs.economy.ui import boost_display_line
from app.core import LayoutView
from app.services.economy import (
    CATEGORIES,
    daily_sale,
    filter_items,
    get_rarity,
    item_category,
    item_emoji,
    item_expired,
    sale_price,
    sell_price,
)
from app.utils import fnumb, get_asset_url, helpers
from config import Emojis

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from app.cogs.economy.cog import Economy

__all__ = ('ItemBrowser',)

#: Items per list page (each costs three components; a CV2 message allows 40).
PAGE_SIZE = 5

Mode = Literal['shop', 'inventory']


def _shorten(text: str | None, limit: int) -> str:
    text = (text or '').strip().replace('\n', ' ')
    return text if len(text) <= limit else text[: limit - 1] + '…'


class _QuantityModal(discord.ui.Modal):
    """Asks for a quantity, then hands it to ``on_quantity``."""

    def __init__(self, title: str, *, maximum: int | None, on_quantity: Callable[[discord.Interaction, int], Awaitable[None]]) -> None:
        super().__init__(title=title, timeout=120.0)
        self.maximum = maximum
        self.on_quantity = on_quantity
        hint = f'1 - {maximum}' if maximum else 'At least 1'
        self.amount = discord.ui.TextInput(label='Quantity', placeholder=hint, max_length=9)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.amount.value.strip().lower()
        quantity = self.maximum if raw in ('all', 'max') and self.maximum else int(raw) if raw.isdigit() else 0
        if quantity < 1 or (self.maximum and quantity > self.maximum):
            await interaction.response.send_message(f'{Emojis.error} Enter a whole number, {self.amount.placeholder}.', ephemeral=True)
            return
        await self.on_quantity(interaction, quantity)


class _GiftModal(discord.ui.Modal, title='Gift item'):
    """Picks a recipient and a quantity for a gift."""

    def __init__(self, *, maximum: int, on_gift: Callable[[discord.Interaction, discord.Member, int], Awaitable[None]]) -> None:
        super().__init__(timeout=120.0)
        self.maximum = maximum
        self.on_gift = on_gift
        self.recipient = discord.ui.UserSelect(placeholder='Who gets it?', required=True)
        self.amount = discord.ui.TextInput(default='1', placeholder=f'1 - {maximum}', max_length=9)
        self.add_item(discord.ui.Label(text='Recipient', component=self.recipient))
        self.add_item(discord.ui.Label(text='Quantity', component=self.amount))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.amount.value.strip()
        quantity = int(raw) if raw.isdigit() else 0
        member = self.recipient.values[0] if self.recipient.values else None
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(f'{Emojis.error} Pick a member of this server.', ephemeral=True)
            return
        if not 1 <= quantity <= self.maximum:
            await interaction.response.send_message(f'{Emojis.error} Enter a number between 1 and {self.maximum}.', ephemeral=True)
            return
        await self.on_gift(interaction, member, quantity)


class ItemBrowser(LayoutView):
    """The shop or a member's inventory as one interactive CV2 card.

    ``viewer`` is who may click (and whose wallet the shop uses); ``target`` is
    whose inventory is shown — actions only appear when it is the viewer's own.
    Pass ``item_id`` to open straight on an item's detail page.
    """

    def __init__(
        self,
        cog: Economy,
        viewer: discord.Member,
        *,
        mode: Mode,
        target: discord.Member | None = None,
        item_id: int | None = None,
    ) -> None:
        super().__init__(timeout=300.0, members=viewer, disable_on_timeout=True)
        self.cog = cog
        self.viewer = viewer
        self.target = target or viewer
        self.mode: Mode = mode
        self.category: str | None = None
        self.page = 0
        self.item_id = item_id
        #: One-line feedback from the last action, shown at the top of the card.
        self.notice: str | None = None
        #: Today's shop deals (``item_id -> percent off``), refreshed on every build.
        self.deals: dict[int, int] = {}

    @property
    def guild(self) -> discord.Guild:
        return self.viewer.guild

    @property
    def own_inventory(self) -> bool:
        return self.mode == 'inventory' and self.target.id == self.viewer.id

    # -- data -----------------------------------------------------------------

    async def _rows(self) -> list[Any]:
        db = self.cog.bot.db.economy
        if self.mode == 'shop':
            return await db.get_items(self.guild.id)
        return await db.get_inventory(self.target.id, self.guild.id)

    @staticmethod
    def _row_id(row: Mapping[str, Any]) -> int:
        return row['id'] if 'id' in row else row['item_id']

    # -- plumbing ---------------------------------------------------------------

    async def build(self) -> None:
        """(Re)build the card for the current page or item."""
        rows = await self._rows()
        if self.mode == 'shop':
            self.deals = daily_sale(self.guild.id, datetime.datetime.now(datetime.UTC).date(), [self._row_id(r) for r in rows])
        item = next((r for r in rows if self._row_id(r) == self.item_id), None) if self.item_id else None
        if self.item_id is not None and item is None:
            # Sold out of the inventory or deleted from the shop since the last render.
            self.item_id = None
        container = await (self._detail(item) if item is not None else self._list(rows))
        self.clear_items()
        self.add_item(container)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await self.build()
        await interaction.response.edit_message(view=self)

    def _button(self, label: str, callback: Callable[[discord.Interaction], Awaitable[None]], *,
                style: discord.ButtonStyle = discord.ButtonStyle.secondary, emoji: str | None = None,
                disabled: bool = False) -> discord.ui.Button:
        button: discord.ui.Button = discord.ui.Button(label=label, style=style, emoji=emoji, disabled=disabled)
        button.callback = callback  # type: ignore[assignment]
        return button

    def _notice_item(self) -> discord.ui.TextDisplay | None:
        if self.notice is None:
            return None
        text, self.notice = self.notice, None
        return discord.ui.TextDisplay(text)

    def _price(self, row: Mapping[str, Any]) -> str:
        """The shop price, struck through next to today's discounted one when the item is on sale."""
        cash, price = Emojis.Economy.cash, row['price']
        if percent := self.deals.get(self._row_id(row)):
            return f'~~{fnumb(price)}~~ {cash} **{fnumb(sale_price(price, percent))}** *-{percent}% today*'
        return f'{cash} **{fnumb(price)}**'

    @staticmethod
    def _expired(row: Mapping[str, Any]) -> bool:
        return item_expired(row, datetime.datetime.now(datetime.UTC).replace(tzinfo=None))

    # -- list page --------------------------------------------------------------

    async def _list(self, rows: list[Any]) -> discord.ui.Container:
        container = discord.ui.Container(accent_colour=helpers.Colour.brand())
        header, thumbnail = await self._header(rows), self._thumbnail()
        # An icon-less guild has no URL, and Discord rejects an empty thumbnail.
        container.add_item(
            discord.ui.Section(header, accessory=discord.ui.Thumbnail(thumbnail)) if thumbnail
            else discord.ui.TextDisplay(header)
        )
        if notice := self._notice_item():
            container.add_item(notice)
        container.add_item(discord.ui.Separator())

        shown = filter_items(rows, self.category)
        pages = max((len(shown) - 1) // PAGE_SIZE + 1, 1)
        self.page = min(self.page, pages - 1)
        owned = await self._owned_map() if self.mode == 'shop' else {}

        if not shown:
            container.add_item(discord.ui.TextDisplay(self._empty_text(bool(rows))))
        for row in shown[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]:
            container.add_item(discord.ui.Section(
                self._list_line(row, owned.get(self._row_id(row), 0)),
                accessory=self._button('View', self._open(self._row_id(row))),
            ))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(self._category_select(rows)))
        if pages > 1:
            container.add_item(discord.ui.ActionRow(
                self._button('Previous', self._turn(-1), emoji='\N{BLACK LEFT-POINTING TRIANGLE}', disabled=self.page == 0),
                self._button(f'Page {self.page + 1}/{pages}', self._turn(0), disabled=True),
                self._button('Next', self._turn(1), emoji='\N{BLACK RIGHT-POINTING TRIANGLE}', disabled=self.page >= pages - 1),
            ))
        return container

    def _thumbnail(self) -> str:
        if self.mode == 'shop':
            return get_asset_url(self.guild)  # type: ignore[arg-type]
        return get_asset_url(self.target)

    async def _owned_map(self) -> dict[int, int]:
        rows = await self.cog.bot.db.economy.get_inventory(self.viewer.id, self.guild.id)
        return {row['item_id']: row['quantity'] for row in rows}

    async def _header(self, rows: list[Any]) -> str:
        cash = Emojis.Economy.cash
        if self.mode == 'shop':
            balance = await self.cog.bot.db.get_user_balance(self.viewer.id, self.guild.id)
            return (
                f'## \N{CONVENIENCE STORE} {self.guild.name} Shop\n'
                f'**{len(rows)}** items for sale • your cash {cash} **{fnumb(balance.cash)}**\n'
                f'-# Pick an item to see what it does and buy it.'
                + self._deals_line(rows)
            )

        worth = sum(sell_price(r['price']) * r['quantity'] for r in rows)
        total = sum(r['quantity'] for r in rows)
        boosts = await self.cog.bot.db.economy.get_active_boosts(self.target.id, self.guild.id)
        lines = [
            f'## \N{SCHOOL SATCHEL} {self.target.display_name}\'s Inventory',
            f'\N{PACKAGE} **{total:,}** items • **{len(rows)}** kinds • worth {cash} **{fnumb(worth)}**',
            *(boost_display_line(b) for b in boosts),
        ]
        return '\n'.join(lines)

    def _deals_line(self, rows: list[Any]) -> str:
        names = {self._row_id(r): f'{item_emoji(r)} **{r["name"]}**' for r in rows}
        deals = ', '.join(f'{names[i]} -{p}%' for i, p in self.deals.items() if i in names)
        return f'\n\N{LABEL} **Today\'s deals:** {deals}' if deals else ''

    def _empty_text(self, has_any: bool) -> str:
        if has_any:
            return '*Nothing in this category.*'
        if self.mode == 'shop':
            return '*The shop is empty.* Admins can stock it with `shop add`.'
        return '*No items yet.* Browse the `shop` to buy some.'

    def _list_line(self, row: Mapping[str, Any], owned: int) -> str:
        rarity = get_rarity(row.get('rarity'))
        cash = Emojis.Economy.cash
        title = f'{item_emoji(row)} **{row["name"]}**'
        if self.mode == 'inventory':
            unit = sell_price(row['price'])
            detail = f'×**{row["quantity"]:,}** • worth {cash} {fnumb(unit * row["quantity"])}'
        else:
            stock = row.get('stock')
            stock_text = '' if stock is None else (' • **sold out**' if stock <= 0 else f' • {stock:,} left')
            owned_text = f' • you own {owned:,}' if owned else ''
            expired_text = ' • *no longer on sale*' if self._expired(row) else ''
            detail = f'{self._price(row)}{stock_text}{owned_text}{expired_text}'
        blurb = _shorten(row.get('description') or self.cog._effect_line(self.guild, row), 90)
        return f'{title} {rarity.emoji}\n{detail}' + (f'\n-# {blurb}' if blurb else '')

    def _category_select(self, rows: list[Any]) -> discord.ui.Select:
        counts = {c.id: 0 for c in CATEGORIES}
        for row in rows:
            counts[item_category(row.get('effect')).id] += 1
        select: discord.ui.Select = discord.ui.Select(placeholder='Filter by category…', options=[
            discord.SelectOption(label=f'All items ({len(rows)})', value='all', emoji='\N{CARD INDEX DIVIDERS}',
                                 default=self.category is None),
            *(
                discord.SelectOption(label=f'{c.name} ({counts[c.id]})', value=c.id, emoji=c.emoji,
                                     default=self.category == c.id)
                for c in CATEGORIES
            ),
        ])

        async def callback(interaction: discord.Interaction) -> None:
            value = select.values[0]
            self.category = None if value == 'all' else value
            self.page = 0
            await self._refresh(interaction)

        select.callback = callback  # type: ignore[assignment]
        return select

    def _turn(self, delta: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            self.page = max(self.page + delta, 0)
            await self._refresh(interaction)
        return callback

    def _open(self, item_id: int | None) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            self.item_id = item_id
            await self._refresh(interaction)
        return callback

    # -- detail page ------------------------------------------------------------

    async def _detail(self, item: Mapping[str, Any]) -> discord.ui.Container:
        rarity = get_rarity(item.get('rarity'))
        category = item_category(item.get('effect'))
        cash = Emojis.Economy.cash
        item_id = self._row_id(item)
        container = discord.ui.Container(accent_colour=discord.Colour(rarity.colour))

        heading = (
            f'## {item_emoji(item)} {item["name"]}\n'
            f'{rarity.emoji} **{rarity.name}** • {category.emoji} {category.name}\n'
            f'{item.get("description") or "*No description.*"}'
        )
        emoji = discord.PartialEmoji.from_str(item['emoji']) if item.get('emoji') else None
        if emoji is not None and emoji.id:
            container.add_item(discord.ui.Section(heading, accessory=discord.ui.Thumbnail(emoji.url)))
        else:
            container.add_item(discord.ui.TextDisplay(heading))
        if notice := self._notice_item():
            container.add_item(notice)
        container.add_item(discord.ui.Separator())

        # The shop row has the listing columns; an inventory row has the quantity.
        shop_row = item if self.mode == 'shop' else await self.cog.bot.db.economy.get_item_by_id(self.guild.id, item_id)
        owned = await self.cog.bot.db.economy.get_quantity(self.target.id, self.guild.id, item_id)
        price_text = self._price(item) if self.mode == 'shop' else f'{cash} {fnumb(item["price"])}'
        facts = [
            f'**Price** {price_text} • **Sells for** {cash} {fnumb(sell_price(item["price"]))}',
            f'**{"You own" if self.target.id == self.viewer.id else "Owned"}** ×{owned:,}',
        ]
        if effect := self.cog._effect_line(self.guild, item):
            facts.append(f'\N{SMALL BLUE DIAMOND} {effect}')
        expired = shop_row is not None and self._expired(shop_row)
        if shop_row is not None:
            if shop_row['stock'] is not None:
                facts.append(f'\N{PACKAGE} Stock: **{shop_row["stock"]:,}** left' if shop_row['stock'] > 0 else '\N{PACKAGE} **Sold out**')
            if shop_row['max_per_user'] is not None:
                facts.append(f'\N{NO ENTRY SIGN} Limit: **{shop_row["max_per_user"]}** per member')
            if shop_row['required_role_id']:
                facts.append(f'\N{LOCK} Requires <@&{shop_row["required_role_id"]}>')
            if shop_row['available_until']:
                until = discord.utils.format_dt(shop_row['available_until'].replace(tzinfo=datetime.UTC), 'R')
                facts.append('\N{HOURGLASS} **No longer on sale**' if expired else f'\N{HOURGLASS WITH FLOWING SAND} On sale until {until}')
            if shop_row['remove_role_id']:
                facts.append(f'\N{CROSS MARK} Using it removes <@&{shop_row["remove_role_id"]}>')
        container.add_item(discord.ui.TextDisplay('\n'.join(facts)))
        container.add_item(discord.ui.Separator())

        back = self._button('Back', self._open(None), emoji='\N{LEFTWARDS ARROW WITH HOOK}')
        if self.mode == 'shop':
            container.add_item(discord.ui.ActionRow(
                self._button('Buy 1', self._buy(item_id, 1), style=discord.ButtonStyle.success, emoji=Emojis.Economy.cash,
                             disabled=expired),
                self._button('Buy…', self._buy_many(item, shop_row), style=discord.ButtonStyle.primary, disabled=expired),
                back,
            ))
        elif self.own_inventory:
            container.add_item(discord.ui.ActionRow(
                self._button('Use', self._use(item_id), style=discord.ButtonStyle.success, emoji='\N{SPARKLES}'),
                self._button('Sell 1', self._sell(item_id, 1)),
                self._button(f'Sell all ({owned:,})', self._sell(item_id, owned), style=discord.ButtonStyle.danger, disabled=owned < 2),
                self._button('Gift…', self._gift(item_id, owned), emoji='\N{WRAPPED PRESENT}'),
                back,
            ))
        else:
            container.add_item(discord.ui.ActionRow(back))
        return container

    # -- actions ----------------------------------------------------------------

    async def _after(self, interaction: discord.Interaction, ok: bool, message: str) -> None:
        self.notice = f'{Emojis.success if ok else Emojis.error} {message}'
        await self._refresh(interaction)

    def _buy(self, item_id: int, quantity: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            ok, message = await self.cog.buy_item_action(self.viewer, item_id, quantity)
            await self._after(interaction, ok, message)
        return callback

    def _buy_many(self, item: Mapping[str, Any], shop_row: Mapping[str, Any] | None) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            balance = await self.cog.bot.db.get_user_balance(self.viewer.id, self.guild.id)
            percent = self.deals.get(self._row_id(item), 0)
            limits = [balance.cash // max(sale_price(item['price'], percent) if percent else item['price'], 1)]
            if shop_row is not None and shop_row['stock'] is not None:
                limits.append(shop_row['stock'])
            maximum = max(min(limits), 1)

            async def on_quantity(modal_interaction: discord.Interaction, quantity: int) -> None:
                ok, message = await self.cog.buy_item_action(self.viewer, self._row_id(item), quantity)
                await self._after(modal_interaction, ok, message)

            await interaction.response.send_modal(_QuantityModal(f'Buy {item["name"]}'[:45], maximum=maximum, on_quantity=on_quantity))
        return callback

    def _sell(self, item_id: int, quantity: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            ok, message = await self.cog.sell_item_action(self.viewer, item_id, quantity)
            await self._after(interaction, ok, message)
        return callback

    def _use(self, item_id: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            ok, message = await self.cog.use_item_action(self.viewer, item_id)
            await self._after(interaction, ok, message)
        return callback

    def _gift(self, item_id: int, owned: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            async def on_gift(modal_interaction: discord.Interaction, member: discord.Member, quantity: int) -> None:
                ok, message = await self.cog.gift_item_action(self.viewer, member, item_id, quantity)
                await self._after(modal_interaction, ok, message)

            await interaction.response.send_modal(_GiftModal(maximum=max(owned, 1), on_gift=on_gift))
        return callback
