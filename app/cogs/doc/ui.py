from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from app.core import Context, LayoutView

if TYPE_CHECKING:
    from .cog import Documentation
    from .models import DocItem


class DocSelect(discord.ui.Select):
    """Switches the rendered symbol when several similar matches were found."""

    def __init__(self, view: DocView) -> None:
        super().__init__(placeholder="Select a similar symbol…", max_values=1)
        self._docview: DocView = view  # `parent`/`view` are managed by discord.py
        for index, item in enumerate(view.entries):
            self.add_option(label=item.symbol_id, description=item.group, value=str(index))

    async def callback(self, interaction: discord.Interaction) -> None:
        self._docview.index = int(self.values[0])
        await self._docview.refresh(interaction)


class DocView(LayoutView):
    """A Components V2 documentation card with an optional symbol switcher.

    Replaces the old embed-based ``DocPaginator``: the symbol body, its fields and a
    "View Documentation" link all live in one CV2 container, with a select beneath it
    when the lookup returned more than one candidate symbol.
    """

    def __init__(self, cog: Documentation, entries: list[DocItem], *, author: discord.abc.Snowflake) -> None:
        super().__init__(members=author, timeout=450)
        self.cog: Documentation = cog
        self.entries: list[DocItem] = entries
        self.index: int = 0

    async def _compose(self) -> None:
        self.clear_items()
        container = await self.cog.create_symbol_container(self.entries[self.index])
        if container is not None:
            self.add_item(container)
        if len(self.entries) > 1:
            self.add_item(discord.ui.ActionRow(DocSelect(self)))

    async def refresh(self, interaction: discord.Interaction) -> None:
        await self._compose()
        await interaction.response.edit_message(view=self)

    @classmethod
    async def start(cls, ctx: Context, *, entries: list[DocItem], cog: Documentation) -> DocView:
        """Build and send the documentation card for the first matched symbol."""
        self = cls(cog, entries, author=ctx.author)
        await self._compose()
        self.message = await ctx.send(view=self)
        return self
