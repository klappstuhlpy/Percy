from __future__ import annotations

import discord

from app.core.views import View


class PreExistingMuteRoleView(View):
    def __init__(self, member: discord.Member) -> None:
        super().__init__(timeout=120.0, members=member)
        self.merge: bool | None = None

    @discord.ui.button(label="Merge", style=discord.ButtonStyle.blurple)
    async def merge_button(self, interaction: discord.Interaction, _) -> None:
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.merge = True

    @discord.ui.button(label="Replace", style=discord.ButtonStyle.grey)
    async def replace_button(self, interaction: discord.Interaction, _) -> None:
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.merge = False

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def abort_button(self, _, __) -> None:
        self.merge = None
        if self.message is not None:
            await self.message.delete()
