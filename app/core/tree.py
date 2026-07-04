from __future__ import annotations

import logging
import traceback
from contextlib import suppress
from typing import Any

import discord
from discord import app_commands

from app.core.command import assign_native_permissions
from app.core.models import AppBadArgument
from app.utils import helpers
from app.utils.lock import LockedResourceError
from config import Emojis

__all__ = ("CommandTree",)

log = logging.getLogger(__name__)


class CommandTree(app_commands.CommandTree):
    """A custom command tree that implements a custom error handler."""

    def _get_all_commands(
        self, *, guild: discord.abc.Snowflake | None = None
    ) -> list[app_commands.Command[Any, ..., Any] | app_commands.Group | app_commands.ContextMenu]:
        """Re-derive native command permissions whenever the sync payload is built.

        Runs :func:`assign_native_permissions` over the bot's commands at the exact choke point
        every sync path funnels through — ``CommandTree._get_all_commands`` is what both
        discord.py's :meth:`sync` *and* ``jishaku``'s ``jsk sync`` (which calls
        ``bot.http.bulk_upsert_*`` directly and never touches :meth:`sync`) use to gather the
        commands before serialising them to ``to_dict``. Hooking here — rather than overriding
        :meth:`sync` — guarantees ``default_member_permissions`` reflects the current gates no
        matter what triggered the sync, even after a cog reload rebuilt the app-command objects.

        This is a visibility default only: Discord keeps per-guild admin overrides in a separate
        store that this never writes to, so an admin's customisation is preserved across syncs.
        Applying the permissions is a pure, idempotent attribute-set, so re-running it on every
        payload build is safe.
        """
        gated = assign_native_permissions(self.client.walk_commands())
        log.info("Applied native slash-command permissions to %d command(s) before sync.", gated)
        return super()._get_all_commands(guild=guild)

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        error = getattr(error, "original", error)

        blacklist = (discord.Forbidden, discord.NotFound)
        if isinstance(error, blacklist):
            return None

        embed = discord.Embed(
            title=f"{Emojis.warning} App Command Error", timestamp=interaction.created_at, colour=helpers.Colour.burgundy()
        )

        command = interaction.command
        if command is not None:
            if command._has_any_error_handlers():
                return None

            embed.add_field(name="Name", value=command.qualified_name)

        handle_elsewhere = (
            app_commands.CommandOnCooldown,
            app_commands.CommandInvokeError,
            app_commands.TransformerError,
            LockedResourceError,
            app_commands.BotMissingPermissions,
            AppBadArgument,
        )
        if isinstance(error, handle_elsewhere):
            interaction.client.dispatch("command_error", interaction._baton, error)
            return None

        embed.add_field(
            name="User",
            value=f"[{interaction.user}](https://discord.com/users/{interaction.user.id}) (ID: {interaction.user.id})",
        )

        fmt = f"Channel: [#{interaction.channel}]({interaction.channel.jump_url if interaction.channel else ''}) (ID: {interaction.channel_id})\n"
        if interaction.guild:
            fmt += f"Guild: {interaction.guild} (ID: {interaction.guild.id})"
        else:
            fmt += "Guild: *<Private Message>*"

        embed.add_field(name="Location", value=fmt, inline=False)

        namespace = interaction.namespace.__dict__
        embed.add_field(name="Namespace(s)", value=", ".join(f"{k}: {v!r}" for k, v in namespace.items()), inline=False)

        exc = "".join(traceback.format_exception(type(error), error, error.__traceback__, chain=False))
        embed.description = f"### Retrieved Traceback\n```py\n{exc}\n```"
        embed.set_footer(text="occurred at")

        with suppress(discord.HTTPException, ValueError):
            await interaction.client.stats_webhook.send(embed=embed)  # type: ignore[attr-defined]
