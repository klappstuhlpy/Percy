from __future__ import annotations

import discord

from app.core.views import LayoutView
from app.utils.helpers import Colour


class PreExistingMuteRoleView(LayoutView):
    def __init__(self, member: discord.Member, *, content: str | None = None) -> None:
        super().__init__(timeout=120.0, members=member, delete_on_timeout=True)
        self.merge: bool | None = None

        self._merge_btn = discord.ui.Button(label="Merge", style=discord.ButtonStyle.blurple)
        self._merge_btn.callback = self._on_merge  # type: ignore[assignment]

        self._replace_btn = discord.ui.Button(label="Replace", style=discord.ButtonStyle.grey)
        self._replace_btn.callback = self._on_replace  # type: ignore[assignment]

        self._cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.red)
        self._cancel_btn.callback = self._on_cancel  # type: ignore[assignment]

        container = discord.ui.Container(accent_colour=Colour.warning_accent())
        if content:
            container.add_item(discord.ui.TextDisplay(content))
            container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(self._merge_btn, self._replace_btn, self._cancel_btn))
        self.add_item(container)

    async def _on_merge(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.merge = True
        self.stop()

    async def _on_replace(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.merge = False
        self.stop()

    async def _on_cancel(self, interaction: discord.Interaction) -> None:  # noqa: ARG002
        self.merge = None
        if self.message is not None:
            await self.message.delete()
        self.stop()
