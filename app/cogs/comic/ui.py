from __future__ import annotations

import discord


class JumpToTopButton(discord.ui.Button):
    def __init__(self, message: discord.Message) -> None:
        assert message.guild is not None
        super().__init__(
            label='Jump to the Top',
            style=discord.ButtonStyle.link,
            url=f'https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id}',
        )
