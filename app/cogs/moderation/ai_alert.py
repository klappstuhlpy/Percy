"""UI for AI moderation flags: a rich embed plus persistent moderator action buttons.

The AI only *flags*; a human still decides. The flag carries Delete / Warn / Kick / Ban /
Dismiss buttons. Each checks the *clicking* moderator's own permission and the role hierarchy,
performs the action, and records a mod case via the standard ``mod_action`` dispatch — exactly
like the equivalent slash command would.

The buttons are **persistent**: they are :class:`discord.ui.DynamicItem`s whose per-message
state (target/channel/message ids) is encoded in each button's ``custom_id``, so they keep
working across bot restarts once :class:`AIModerationButton` is registered with
``bot.add_dynamic_items``. The reason text is read back from the embed at click time.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from app.utils import get_asset_url, truncate
from config import Emojis

if TYPE_CHECKING:
    import re

    from app.services.ai import ModerationVerdict

__all__ = ('AIModerationButton', 'build_ai_moderation_embed', 'build_ai_moderation_view')

_FLAG_COLOUR = discord.Colour(0xED4245)  # red while open
_RESOLVED_COLOUR = discord.Colour(0x57F287)  # green once actioned
_DISMISSED_COLOUR = discord.Colour(0x4E5058)  # grey once dismissed


@dataclass(frozen=True)
class _Spec:
    label: str
    style: discord.ButtonStyle
    emoji: str
    permission: str  # the guild_permissions attribute required to use the button
    verb: str  # human-readable permission name


_SPECS: dict[str, _Spec] = {
    'delete': _Spec('Delete', discord.ButtonStyle.secondary, '\N{WASTEBASKET}', 'manage_messages', 'Manage Messages'),
    'warn': _Spec('Warn', discord.ButtonStyle.secondary, '\N{WARNING SIGN}', 'kick_members', 'Kick Members'),
    'kick': _Spec('Kick', discord.ButtonStyle.primary, '\N{WOMANS BOOTS}', 'kick_members', 'Kick Members'),
    'ban': _Spec('Ban', discord.ButtonStyle.danger, '\N{HAMMER}', 'ban_members', 'Ban Members'),
    'dismiss': _Spec('Dismiss', discord.ButtonStyle.secondary, '\N{NO ENTRY SIGN}', 'manage_messages', 'Manage Messages'),
}
#: Button order in the alert message.
_ORDER = ('delete', 'warn', 'kick', 'ban', 'dismiss')


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


def build_ai_moderation_view(*, target_id: int, channel_id: int, message_id: int) -> discord.ui.View:
    """A timeout-less view holding the five persistent action buttons."""
    view = discord.ui.View(timeout=None)
    for action in _ORDER:
        view.add_item(AIModerationButton(action, target_id, channel_id, message_id))
    return view


def _reason_from(message: discord.Message | None) -> str:
    """Recover the AI reason from the alert embed (persistent buttons carry no state)."""
    if message and message.embeds:
        for field in message.embeds[0].fields:
            if field.name == 'Reason' and field.value:
                return field.value
    return 'AI-flagged message'


class AIModerationButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r'aimod:(?P<action>[a-z]+):(?P<target>\d+):(?P<channel>\d+):(?P<message>\d+)',
):
    """One persistent action button; ``action`` selects the behaviour at click time."""

    def __init__(self, action: str, target_id: int, channel_id: int, message_id: int) -> None:
        self.action = action
        self.target_id = target_id
        self.channel_id = channel_id
        self.message_id = message_id
        spec = _SPECS[action]
        super().__init__(
            discord.ui.Button(
                label=spec.label,
                style=spec.style,
                emoji=spec.emoji,
                custom_id=f'aimod:{action}:{target_id}:{channel_id}:{message_id}',
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ) -> AIModerationButton:
        return cls(match['action'], int(match['target']), int(match['channel']), int(match['message']))

    async def callback(self, interaction: discord.Interaction) -> None:
        handler = getattr(self, f'_do_{self.action}', None)
        if handler is not None:
            await handler(interaction)

    # -- shared guards -------------------------------------------------------

    async def _require(self, interaction: discord.Interaction, action: str) -> discord.Member | None:
        spec = _SPECS[action]
        user = interaction.user
        if not isinstance(user, discord.Member) or not getattr(user.guild_permissions, spec.permission, False):
            await interaction.response.send_message(
                f'{Emojis.error} You need the **{spec.verb}** permission to do that.', ephemeral=True
            )
            return None
        return user

    def _resolve_target(
        self, guild: discord.Guild, moderator: discord.Member, action: str, *, allow_absent: bool = False
    ) -> tuple[discord.Member | discord.Object | None, str | None]:
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

    def _action_reason(self, interaction: discord.Interaction, moderator: discord.Member) -> str:
        return truncate(f'AI flag actioned by {moderator} (ID: {moderator.id}): {_reason_from(interaction.message)}', 480)

    async def _resolve(self, interaction: discord.Interaction, summary: str, colour: discord.Colour) -> None:
        """Annotate the embed and remove all buttons (terminal outcome)."""
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else None
        if embed is not None:
            embed.add_field(name='Status', value=summary, inline=False)
            embed.colour = colour
        await interaction.response.edit_message(embed=embed, view=None)

    # -- actions -------------------------------------------------------------

    async def _do_delete(self, interaction: discord.Interaction) -> None:
        moderator = await self._require(interaction, 'delete')
        if moderator is None:
            return

        channel = interaction.client.get_channel(self.channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            await interaction.response.send_message(f'{Emojis.error} That channel is gone.', ephemeral=True)
            return
        try:
            await channel.get_partial_message(self.message_id).delete()
        except discord.NotFound:
            await interaction.response.send_message(f'{Emojis.warning} The message was already deleted.', ephemeral=True)
            return
        except discord.HTTPException as exc:
            await interaction.response.send_message(f'{Emojis.error} Couldn\'t delete it: {exc}', ephemeral=True)
            return
        # Deleting isn't terminal — leave Warn/Kick/Ban/Dismiss available.
        await interaction.response.send_message(
            f'{Emojis.success} Message deleted by {moderator.mention}.', ephemeral=True
        )

    async def _do_warn(self, interaction: discord.Interaction) -> None:
        moderator = await self._require(interaction, 'warn')
        if moderator is None or interaction.guild is None:
            return
        target, error = self._resolve_target(interaction.guild, moderator, 'warn')
        if error or not isinstance(target, discord.Member):
            await interaction.response.send_message(f'{Emojis.error} {error}', ephemeral=True)
            return

        interaction.client.dispatch('mod_action', interaction.guild.id, 'warn', self.target_id, moderator.id,
                                    _reason_from(interaction.message))
        with suppress(discord.HTTPException):
            await target.send(f'{Emojis.warning} You were warned in **{interaction.guild.name}**.')
        await self._resolve(interaction, f'\N{WARNING SIGN} Warned by {moderator.mention}', _RESOLVED_COLOUR)

    async def _do_kick(self, interaction: discord.Interaction) -> None:
        moderator = await self._require(interaction, 'kick')
        if moderator is None or interaction.guild is None:
            return
        target, error = self._resolve_target(interaction.guild, moderator, 'kick')
        if error or not isinstance(target, discord.Member):
            await interaction.response.send_message(f'{Emojis.error} {error}', ephemeral=True)
            return
        try:
            await interaction.guild.kick(target, reason=self._action_reason(interaction, moderator))
        except discord.HTTPException as exc:
            await interaction.response.send_message(f'{Emojis.error} Couldn\'t kick them: {exc}', ephemeral=True)
            return
        interaction.client.dispatch('mod_action', interaction.guild.id, 'kick', self.target_id, moderator.id,
                                    _reason_from(interaction.message))
        await self._resolve(interaction, f'\N{WOMANS BOOTS} Kicked by {moderator.mention}', _RESOLVED_COLOUR)

    async def _do_ban(self, interaction: discord.Interaction) -> None:
        moderator = await self._require(interaction, 'ban')
        if moderator is None or interaction.guild is None:
            return
        target, error = self._resolve_target(interaction.guild, moderator, 'ban', allow_absent=True)
        if error or target is None:
            await interaction.response.send_message(f'{Emojis.error} {error}', ephemeral=True)
            return
        try:
            await interaction.guild.ban(target, reason=self._action_reason(interaction, moderator))
        except discord.HTTPException as exc:
            await interaction.response.send_message(f'{Emojis.error} Couldn\'t ban them: {exc}', ephemeral=True)
            return
        interaction.client.dispatch('mod_action', interaction.guild.id, 'ban', self.target_id, moderator.id,
                                    _reason_from(interaction.message))
        await self._resolve(interaction, f'\N{HAMMER} Banned by {moderator.mention}', _RESOLVED_COLOUR)

    async def _do_dismiss(self, interaction: discord.Interaction) -> None:
        moderator = await self._require(interaction, 'dismiss')
        if moderator is None:
            return
        await self._resolve(interaction, f'\N{NO ENTRY SIGN} Dismissed by {moderator.mention}', _DISMISSED_COLOUR)
