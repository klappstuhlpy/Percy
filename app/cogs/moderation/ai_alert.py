"""UI for AI moderation flags: a rich embed plus one-click moderator action buttons.

The AI only *flags*; a human still decides. This view turns a flag into actionable buttons
(Delete / Warn / Kick / Ban). Each button checks the clicking moderator's own permission for
that action and the role hierarchy, performs it, and records a mod case via the standard
``mod_action`` dispatch — exactly like the equivalent slash command would.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

import discord

from app.core.views import View
from app.utils import get_asset_url, truncate
from config import Emojis

if TYPE_CHECKING:
    from app.core import Bot
    from app.services.ai import ModerationVerdict

__all__ = ('AIModerationAlertView', 'build_ai_moderation_embed')

#: Discord brand red for the flag; turns green once a moderator resolves it.
_FLAG_COLOUR = discord.Colour(0xED4245)


def build_ai_moderation_embed(message: discord.Message, verdict: ModerationVerdict) -> discord.Embed:
    """A structured, skimmable embed describing an AI moderation flag."""
    author = message.author
    embed = discord.Embed(
        title='\N{SHIELD} AI Moderation Flag',
        description=(
            f'A message by {author.mention} was flagged for review.\n'
            f'**[Jump to message]({message.jump_url})**'
        ),
        colour=_FLAG_COLOUR,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=str(author), icon_url=get_asset_url(author))
    embed.add_field(name='User', value=f'{author.mention} `{author.id}`', inline=True)
    embed.add_field(name='Channel', value=message.channel.mention, inline=True)
    embed.add_field(name='Category', value=f'`{verdict.category}`', inline=True)
    embed.add_field(name='Confidence', value=f'`{verdict.confidence:.0%}`', inline=True)
    embed.add_field(name='Reason', value=verdict.reason or 'Flagged as potentially harmful.', inline=False)

    content = truncate(message.content, 1000) if message.content else ''
    embed.add_field(name='Message', value=f'>>> {content}' if content else '*no text content*', inline=False)
    embed.set_footer(text='AI moderation • buttons below are logged as mod cases')
    return embed


class AIModerationAlertView(View):
    """Delete / Warn / Kick / Ban buttons under an AI moderation flag.

    Not gated to one user — any moderator with the matching permission may act. Each button
    re-checks that permission (and hierarchy) at click time, so it is safe in a shared
    mod channel.
    """

    def __init__(
        self,
        bot: Bot,
        *,
        guild_id: int,
        channel_id: int,
        message_id: int,
        target_id: int,
        reason: str,
    ) -> None:
        # Long-lived (3 days) but not persistent across restarts; mod flags are acted on fast.
        super().__init__(timeout=259200.0, clear_on_timeout=True)
        self.bot = bot
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.message_id = message_id
        self.target_id = target_id
        self.reason = reason

    # -- shared guards -------------------------------------------------------

    async def _require(self, interaction: discord.Interaction, permission: str, verb: str) -> discord.Member | None:
        """Ensure the clicker is a member with ``permission``; else reject ephemerally."""
        user = interaction.user
        if not isinstance(user, discord.Member) or not getattr(user.guild_permissions, permission, False):
            await interaction.response.send_message(
                f'{Emojis.error} You need the **{verb}** permission to do that.', ephemeral=True
            )
            return None
        return user

    def _resolve_target(
        self, guild: discord.Guild, moderator: discord.Member, action: str, *, allow_absent: bool = False
    ) -> tuple[discord.Member | discord.Object | None, str | None]:
        """Resolve the flagged user and run the self/owner/hierarchy guards."""
        member = guild.get_member(self.target_id)
        if member is None:
            if allow_absent:
                return discord.Object(id=self.target_id), None
            return None, f'That member is no longer in the server, so you can\'t {action} them.'

        if member.id == moderator.id:
            return None, f'You can\'t {action} yourself.'
        if member.id == guild.owner_id:
            return None, f'You can\'t {action} the server owner.'
        if moderator.id != guild.owner_id and moderator.top_role <= member.top_role:
            return None, f'You can\'t {action} someone with a role equal to or higher than yours.'
        if guild.me.top_role <= member.top_role:
            return None, f'I can\'t {action} someone with a role equal to or higher than mine.'
        return member, None

    def _action_reason(self, moderator: discord.Member) -> str:
        return truncate(f'AI flag actioned by {moderator} (ID: {moderator.id}): {self.reason}', 480)

    async def _mark_resolved(self, interaction: discord.Interaction, summary: str) -> None:
        """Disable the view and annotate the alert embed as resolved."""
        self.disable_all()
        self.stop()
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else None
        if embed is not None:
            embed.add_field(name='Resolved', value=summary, inline=False)
            embed.colour = discord.Colour(0x57F287)  # green
        await interaction.response.edit_message(embed=embed, view=self)

    # -- buttons -------------------------------------------------------------

    @discord.ui.button(label='Delete', style=discord.ButtonStyle.secondary, emoji='\N{WASTEBASKET}')
    async def delete_message(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        moderator = await self._require(interaction, 'manage_messages', 'Manage Messages')
        if moderator is None:
            return

        channel = self.bot.get_channel(self.channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            await interaction.response.send_message(f'{Emojis.error} That channel is gone.', ephemeral=True)
            return

        try:
            await channel.get_partial_message(self.message_id).delete()
        except discord.NotFound:
            await interaction.response.send_message(f'{Emojis.warning} The message was already deleted.', ephemeral=True)
        except discord.HTTPException as exc:
            await interaction.response.send_message(f'{Emojis.error} Couldn\'t delete it: {exc}', ephemeral=True)
            return
        else:
            await interaction.response.send_message(f'{Emojis.success} Message deleted.', ephemeral=True)

        # Deleting isn't terminal — keep Warn/Kick/Ban available; just disable Delete.
        button.disabled = True
        with suppress(discord.HTTPException):
            await interaction.message.edit(view=self)  # type: ignore[union-attr]

    @discord.ui.button(label='Warn', style=discord.ButtonStyle.secondary, emoji='\N{WARNING SIGN}')
    async def warn_member(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        moderator = await self._require(interaction, 'kick_members', 'Kick Members')
        if moderator is None or interaction.guild is None:
            return

        target, error = self._resolve_target(interaction.guild, moderator, 'warn')
        if error or not isinstance(target, discord.Member):
            await interaction.response.send_message(f'{Emojis.error} {error}', ephemeral=True)
            return

        self.bot.dispatch('mod_action', self.guild_id, 'warn', self.target_id, moderator.id, self.reason)
        with suppress(discord.HTTPException):
            await target.send(f'{Emojis.warning} You were warned in **{interaction.guild.name}**: {self.reason}')
        await self._mark_resolved(interaction, f'\N{WARNING SIGN} Warned by {moderator.mention}')

    @discord.ui.button(label='Kick', style=discord.ButtonStyle.primary, emoji='\N{WOMANS BOOTS}')
    async def kick_member(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        moderator = await self._require(interaction, 'kick_members', 'Kick Members')
        if moderator is None or interaction.guild is None:
            return

        target, error = self._resolve_target(interaction.guild, moderator, 'kick')
        if error or not isinstance(target, discord.Member):
            await interaction.response.send_message(f'{Emojis.error} {error}', ephemeral=True)
            return

        try:
            await interaction.guild.kick(target, reason=self._action_reason(moderator))
        except discord.HTTPException as exc:
            await interaction.response.send_message(f'{Emojis.error} Couldn\'t kick them: {exc}', ephemeral=True)
            return
        self.bot.dispatch('mod_action', self.guild_id, 'kick', self.target_id, moderator.id, self.reason)
        await self._mark_resolved(interaction, f'\N{WOMANS BOOTS} Kicked by {moderator.mention}')

    @discord.ui.button(label='Ban', style=discord.ButtonStyle.danger, emoji='\N{HAMMER}')
    async def ban_member(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        moderator = await self._require(interaction, 'ban_members', 'Ban Members')
        if moderator is None or interaction.guild is None:
            return

        target, error = self._resolve_target(interaction.guild, moderator, 'ban', allow_absent=True)
        if error or target is None:
            await interaction.response.send_message(f'{Emojis.error} {error}', ephemeral=True)
            return

        try:
            await interaction.guild.ban(target, reason=self._action_reason(moderator))
        except discord.HTTPException as exc:
            await interaction.response.send_message(f'{Emojis.error} Couldn\'t ban them: {exc}', ephemeral=True)
            return
        self.bot.dispatch('mod_action', self.guild_id, 'ban', self.target_id, moderator.id, self.reason)
        await self._mark_resolved(interaction, f'\N{HAMMER} Banned by {moderator.mention}')
