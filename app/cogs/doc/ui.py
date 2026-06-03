from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord
from discord import Embed

from app.core import Context
from app.core.pagination import BasePaginator

from .models import DocItem, DocItemT

if TYPE_CHECKING:
    from .cog import Documentation


class DocSelect(discord.ui.Select):
    def __init__(self, parent: BasePaginator[DocItemT]) -> None:
        super().__init__(
            placeholder="Select a similar Documentation...",
            max_values=1,
            row=1,
        )
        self.super_parent: BasePaginator[DocItemT] = parent  # separate attribute — dpy overrides "parent" internally

        for item in self.super_parent.entries:
            self.add_option(
                label=item.symbol_id,
                description=item.group,
                value=str(self.super_parent.entries.index(item)),
            )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        self.super_parent._current_page = int(self.values[0])
        entries = self.super_parent.switch_page(0)
        page = await self.super_parent.format_page(entries)
        await interaction.message.edit(**self.super_parent.resolve_msg_kwargs(page))


class DocPaginator(BasePaginator[DocItemT]):
    """A View that represents a documentation page for a specific object."""

    async def format_page(self, entries: list[DocItem]) -> Embed | None:
        """Format the page for the given item."""
        item = entries[0]

        if item.embed is not None:
            return item.embed

        cog: Documentation | None = self.extras.get("cog", None)
        if cog is None:
            raise ValueError("The cog was not passed to the paginator.")

        embed = await cog.create_symbol_embed(item)
        item.embed = embed

        try:
            # update the embed in the doc_symbols cache
            # we set the embed permanently, so we don't have to re-create it next time
            origin = cog.doc_symbols[item.package][item.symbol_id]
        except KeyError:
            pass
        else:
            origin.embed = embed
            cog.doc_symbols[item.package][item.symbol_id] = origin

        return embed

    @classmethod
    async def start(
        cls,
        context: Context | discord.Interaction,
        /,
        *,
        entries: list[DocItemT],
        per_page: int = 1,
        clamp_pages: bool = True,
        timeout: int = 450,
        search_for: bool = False,
        ephemeral: bool = False,
        **kwargs: Any,
    ) -> BasePaginator[DocItemT]:
        """Starts documentation paginator with optional selection menu"""
        self = cls(entries=entries, per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)
        self.ctx = context
        self.extras.update(kwargs)

        page = await self.format_page(self.pages[0])
        object_kwargs = self.resolve_msg_kwargs(page)

        # Dont need no pagination buttons
        self.clear_items()

        if len(self.entries) > 1:
            self.add_item(DocSelect(self))

        self.msg = await cls._send(context, ephemeral, **object_kwargs)
        return self
