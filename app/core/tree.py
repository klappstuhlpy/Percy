from __future__ import annotations

import traceback
from contextlib import suppress

import discord
from discord import app_commands

from app.core.models import AppBadArgument
from app.utils import helpers
from app.utils.lock import LockedResourceError
from config import Emojis

__all__ = ('CommandTree',)


class CommandTree(app_commands.CommandTree):
    """A custom command tree that implements a custom error handler."""

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        error = getattr(error, 'original', error)

        blacklist = (
            discord.Forbidden, discord.NotFound
        )
        if isinstance(error, blacklist):
            return None

        embed = discord.Embed(
            title=f'{Emojis.warning} App Command Error',
            timestamp=interaction.created_at,
            colour=helpers.Colour.burgundy()
        )

        command = interaction.command
        if command is not None:
            if command._has_any_error_handlers():
                return None

            embed.add_field(name='Name', value=command.qualified_name)

        handle_elsewhere = (
            app_commands.CommandOnCooldown, app_commands.CommandInvokeError, app_commands.TransformerError,
            LockedResourceError, app_commands.BotMissingPermissions, AppBadArgument
        )
        if isinstance(error, handle_elsewhere):
            interaction.client.dispatch('command_error', interaction._baton, error)
            return None

        embed.add_field(
            name='User',
            value=f'[{interaction.user}](https://discord.com/users/{interaction.user.id}) (ID: {interaction.user.id})')

        fmt = f'Channel: [#{interaction.channel}]({interaction.channel.jump_url if interaction.channel else ''}) (ID: {interaction.channel_id})\n'
        if interaction.guild:
            fmt += f'Guild: {interaction.guild} (ID: {interaction.guild.id})'
        else:
            fmt += 'Guild: *<Private Message>*'

        embed.add_field(name='Location', value=fmt, inline=False)

        namespace = interaction.namespace.__dict__
        embed.add_field(name='Namespace(s)', value=', '.join(f'{k}: {v!r}' for k, v in namespace.items()), inline=False)

        exc = ''.join(traceback.format_exception(type(error), error, error.__traceback__, chain=False))
        embed.description = f'### Retrieved Traceback\n```py\n{exc}\n```'
        embed.set_footer(text='occurred at')

        with suppress(discord.HTTPException, ValueError):
            await interaction.client.stats_webhook.send(embed=embed)  # type: ignore[attr-defined]
