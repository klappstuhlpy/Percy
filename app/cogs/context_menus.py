"""Right-click ("Apps") message actions.

Context-menu commands are app commands with no typed arguments, so each one is a
thin wrapper that reuses an existing subsystem: the translation client, the quote
image renderer, the reminder/timer scheduler. They give common actions a
discoverable, syntax-free entry point that complements the prefix/slash commands.
"""

from __future__ import annotations

import datetime

import discord
from discord import app_commands
from discord.ext import commands

from app.clients import TranslateClient, TranslationError
from app.clients.base import HTTPClientError
from app.core import Accent, Bot, Cog, make_notice
from app.utils import timetools, truncate
from config import Emojis

#: Quote images get unreadable past a sentence or two; keep the rendered text short.
MAX_QUOTE_CHARS = 256


class RemindModal(discord.ui.Modal, title='Remind me about this'):
    """Asks *when* to be reminded, then schedules a reminder linking back to the message."""

    when: discord.ui.TextInput = discord.ui.TextInput(
        label='When?',
        placeholder='e.g. 30m, 2h, 1d, 1w',
        max_length=100,
    )

    def __init__(self, bot: Bot, message: discord.Message) -> None:
        super().__init__()
        self.bot = bot
        self.message = message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            parsed = await timetools.ShortTime.transform(interaction, self.when.value)
        except commands.BadArgument as exc:
            await interaction.response.send_message(f'{Emojis.error} {exc}', ephemeral=True)
            return

        when = parsed.dt
        if when < discord.utils.utcnow() + datetime.timedelta(seconds=15):
            await interaction.response.send_message(
                f'{Emojis.error} That time is too close to now — pick at least 15 seconds out.', ephemeral=True
            )
            return

        text = truncate(self.message.content, 200) if self.message.content else 'this message'
        zone = await self.bot.db.get_user_timezone(interaction.user.id)
        await self.bot.timers.create(
            when,
            'reminder',
            interaction.user.id,
            interaction.channel_id,
            text,
            created=interaction.created_at,
            message_id=self.message.id,
            timezone=zone or 'UTC',
            recur=None,
            recur_label=None,
            recur_remaining=None,
        )
        await interaction.response.send_message(
            f"{Emojis.success} I'll remind you {discord.utils.format_dt(when, 'R')} about that message.",
            ephemeral=True,
        )


class ContextMenus(Cog):
    """Message right-click actions: translate, quote as image, bookmark, and remind."""

    emoji = '\N{WHITE UP POINTING INDEX}'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self.translate_client: TranslateClient = TranslateClient(bot.session)

        # Context menus can't be declared with a decorator inside a cog, so build them
        # from bound methods and register them on the tree (removed again on unload).
        self._menus: tuple[app_commands.ContextMenu, ...] = (
            app_commands.ContextMenu(name='Translate', callback=self.translate_message),
            app_commands.ContextMenu(name='Quote as Image', callback=self.quote_message),
            app_commands.ContextMenu(name='Bookmark to DMs', callback=self.bookmark_message),
            app_commands.ContextMenu(name='Remind Me About This', callback=self.remind_message),
        )
        for menu in self._menus:
            bot.tree.add_command(menu)

    async def cog_unload(self) -> None:
        for menu in self._menus:
            self.bot.tree.remove_command(menu.name, type=menu.type)

    async def translate_message(self, interaction: discord.Interaction, message: discord.Message) -> None:
        """Translate the selected message into English."""
        if not message.content:
            await interaction.response.send_message(
                f'{Emojis.error} That message has no text to translate.', ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.translate_client.translate(message.content, target='en')
        except (TranslationError, HTTPClientError):
            await interaction.followup.send(
                f'{Emojis.error} Could not translate that right now — please try again later.', ephemeral=True
            )
            return

        view = make_notice(
            'Translation',
            truncate(result.text, 3900),
            accent=Accent.info,
            fields=[('Original', truncate(message.content, 1000))],
            footer=f'{result.source_language} → {result.target_language}',
        )
        await interaction.followup.send(view=view, ephemeral=True)

    async def quote_message(self, interaction: discord.Interaction, message: discord.Message) -> None:
        """Render the selected message as a quote image attributed to its author."""
        if not message.content:
            await interaction.response.send_message(
                f'{Emojis.error} That message has no text to quote.', ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)
        file = await self.bot.render.quote(message.author, truncate(message.content, MAX_QUOTE_CHARS))
        await interaction.followup.send(file=file)

    async def bookmark_message(self, interaction: discord.Interaction, message: discord.Message) -> None:
        """DM the invoker a saved copy of the message with a jump link."""
        location = f'#{message.channel}' if message.guild else 'a direct message'
        fields = [
            ('Author', message.author.mention),
            ('Jump', f'[Go to message]({message.jump_url}) in {location}'),
        ]
        if message.attachments:
            fields.append(('Attachments', '\n'.join(a.url for a in message.attachments[:5])))

        thumbnail = next((a.url for a in message.attachments if a.content_type and a.content_type.startswith('image/')), None)
        view = make_notice(
            '\N{BOOKMARK} Bookmark',
            truncate(message.content, 3500) if message.content else '*No text content.*',
            accent=Accent.neutral,
            thumbnail=thumbnail,
            fields=fields,
            footer=discord.utils.format_dt(message.created_at, 'f'),
        )

        try:
            await interaction.user.send(view=view)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"{Emojis.error} I couldn't DM you — enable direct messages from server members and try again.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(f'{Emojis.success} Bookmarked — check your DMs.', ephemeral=True)

    async def remind_message(self, interaction: discord.Interaction, message: discord.Message) -> None:
        """Open a modal to schedule a reminder that links back to the message."""
        await interaction.response.send_modal(RemindModal(self.bot, message))


async def setup(bot: Bot) -> None:
    await bot.add_cog(ContextMenus(bot))
