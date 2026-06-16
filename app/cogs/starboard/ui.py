"""Presentation for the starboard.

Covers three surfaces:

* the mirrored post (content line + rich embed + a jump-link button),
* the read-only configuration embed shown by ``starboard show``, and
* the manager-only :class:`StarboardConfigView` interactive settings panel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from app.cogs.starboard.engine import color_for_stars, star_emoji_for
from app.cogs.starboard.models import DEFAULT_EMOJI, DEFAULT_THRESHOLD
from app.core.views import View
from app.utils import helpers

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.cogs.starboard.cog import Starboard
    from app.cogs.starboard.models import StarboardConfig
    from app.core import Context

__all__ = (
    'StarboardConfigView',
    'build_config_embed',
    'build_starboard_content',
    'build_starboard_embed',
)

#: File extensions treated as previewable images for the embed thumbnail.
_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.webp')

#: How much of a replied-to message / overflow to keep before truncating.
_REPLY_SNIPPET_LEN = 100


def build_starboard_content(emoji: str, star_count: int, channel: discord.abc.GuildChannel | discord.Thread) -> str:
    """The one-line header shown above the mirrored embed.

    Uses the configured emoji when it's customized; otherwise escalates the default
    star through its popularity tiers.
    """
    display = star_emoji_for(star_count) if emoji == DEFAULT_EMOJI else emoji
    return f"{display} **{star_count}** {channel.mention}"


def _truncate(text: str, length: int = _REPLY_SNIPPET_LEN) -> str:
    text = ' '.join(text.split())  # collapse newlines/runs of whitespace into single spaces
    return text if len(text) <= length else text[: length - 1].rstrip() + '…'


def _first_image(attachments: Sequence[discord.Attachment]) -> discord.Attachment | None:
    """The first previewable, non-spoiler image attachment, if any."""
    for attachment in attachments:
        if attachment.is_spoiler():
            continue
        if attachment.filename.lower().endswith(_IMAGE_EXTENSIONS):
            return attachment
    return None


def _reply_line(message: discord.Message) -> str | None:
    """A compact 'replying to …' line when the starred message is a reply."""
    ref = message.reference
    if ref is None:
        return None
    resolved = ref.resolved
    if not isinstance(resolved, discord.Message):
        return None

    snippet = _truncate(resolved.content) if resolved.content else '*[no text]*'
    return f"\N{LEFTWARDS ARROW WITH HOOK} Replying to {resolved.author.mention}: {snippet}"


def _describe_attachments(
    attachments: Sequence[discord.Attachment], previewed: discord.Attachment | None
) -> str | None:
    """A human summary of attachments that aren't shown as the embed image."""
    extras = [a for a in attachments if a is not previewed]
    if not extras:
        return None

    lines: list[str] = []
    for attachment in extras[:5]:
        marker = '\N{WARNING SIGN} ' if attachment.is_spoiler() else ''
        lines.append(f"{marker}[{attachment.filename}]({attachment.url})")
    if len(extras) > 5:
        lines.append(f"…and {len(extras) - 5} more")
    return '\n'.join(lines)


def build_starboard_embed(message: discord.Message, *, star_count: int, threshold: int) -> tuple[discord.Embed, View]:
    """Builds the embed and jump-link view mirroring an original message.

    The embed colour warms as ``star_count`` climbs past ``threshold``. Returns a
    ``(embed, view)`` pair; the view holds a single link button to the source message.
    """
    description_parts: list[str] = []
    reply = _reply_line(message)
    if reply is not None:
        description_parts.append(reply)
    if message.content:
        description_parts.append(message.content)

    embed = discord.Embed(
        description='\n\n'.join(description_parts) or None,
        colour=discord.Colour(color_for_stars(star_count, threshold)),
        timestamp=message.created_at,
    )
    embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)

    image = _first_image(message.attachments)
    if image is not None:
        embed.set_image(url=image.url)
    elif message.embeds and message.embeds[0].type in ('image', 'gifv') and message.embeds[0].thumbnail.url:
        embed.set_image(url=message.embeds[0].thumbnail.url)

    attachment_summary = _describe_attachments(message.attachments, image)
    if attachment_summary is not None:
        embed.add_field(name='Attachments', value=attachment_summary, inline=False)

    if message.stickers:
        embed.add_field(name='Stickers', value=', '.join(s.name for s in message.stickers), inline=False)

    embed.set_footer(text=f"ID: {message.id}")

    view = View(timeout=None)
    view.add_item(
        discord.ui.Button(style=discord.ButtonStyle.link, label='Jump to message', url=message.jump_url)
    )
    return embed, view


# -- configuration display -------------------------------------------------


def _format_max_age(hours: int) -> str:
    if hours <= 0:
        return 'No limit'
    if hours % 24 == 0:
        days = hours // 24
        return f"{days} day{'s' if days != 1 else ''}"
    return f"{hours} hour{'s' if hours != 1 else ''}"


def build_config_embed(config: StarboardConfig | None, guild: discord.Guild) -> discord.Embed:
    """The read-only summary of a guild's starboard configuration."""
    embed = discord.Embed(title='Starboard Configuration', colour=helpers.Colour.energy_yellow())
    embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)

    if config is None:
        embed.description = (
            'The starboard is **not configured** yet.\n'
            'Set a channel with `starboard channel` or open `starboard config`.'
        )
        embed.add_field(name='Threshold', value=str(DEFAULT_THRESHOLD))
        embed.add_field(name='Emoji', value=DEFAULT_EMOJI)
        return embed

    channel = f'<#{config.channel_id}>' if config.channel_id else '*not set*'
    state = '🟢 Enabled' if config.enabled else '🔴 Disabled'
    ignored = ', '.join(f'<#{cid}>' for cid in config.ignored_channel_ids) or '*none*'

    embed.add_field(name='Status', value=state)
    embed.add_field(name='Channel', value=channel)
    embed.add_field(name='Threshold', value=f'{config.threshold} ⭐')
    embed.add_field(name='Emoji', value=config.emoji)
    embed.add_field(name='Allow self-star', value='Yes' if config.self_star else 'No')
    embed.add_field(name='Allow NSFW', value='Yes' if config.allow_nsfw else 'No')
    embed.add_field(name='Max message age', value=_format_max_age(config.max_age_hours))
    embed.add_field(name='Ignored channels', value=ignored, inline=False)
    return embed


# -- interactive panel -----------------------------------------------------


class _ThresholdModal(discord.ui.Modal, title='Set star threshold'):
    threshold = discord.ui.TextInput(label='Stars required (1-100)', min_length=1, max_length=3)

    def __init__(self, view: StarboardConfigView) -> None:
        super().__init__(timeout=120)
        self.view = view
        self.threshold.default = str(view.config.threshold if view.config else DEFAULT_THRESHOLD)

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        value = self.threshold.value.strip()
        if not value.isdigit() or not 1 <= int(value) <= 100:
            await interaction.response.send_message('Enter a whole number between 1 and 100.', ephemeral=True)
            return
        await self.view.apply(interaction, threshold=int(value))


class _EmojiModal(discord.ui.Modal, title='Set star emoji'):
    emoji = discord.ui.TextInput(label='Emoji', min_length=1, max_length=64)

    def __init__(self, view: StarboardConfigView) -> None:
        super().__init__(timeout=120)
        self.view = view
        self.emoji.default = view.config.emoji if view.config else DEFAULT_EMOJI

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        value = self.emoji.value.strip()
        if not value:
            await interaction.response.send_message('That does not look like a valid emoji.', ephemeral=True)
            return
        await self.view.apply(interaction, emoji=value)


class _MaxAgeModal(discord.ui.Modal, title='Set max message age'):
    hours = discord.ui.TextInput(label='Max age in hours (0 = no limit)', min_length=1, max_length=5)

    def __init__(self, view: StarboardConfigView) -> None:
        super().__init__(timeout=120)
        self.view = view
        self.hours.default = str(view.config.max_age_hours if view.config else 0)

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        value = self.hours.value.strip()
        if not value.isdigit():
            await interaction.response.send_message('Enter a whole number of hours (0 for no limit).', ephemeral=True)
            return
        await self.view.apply(interaction, max_age_hours=int(value))


class StarboardConfigView(View):
    """Manager-only interactive panel for editing a guild's starboard settings."""

    def __init__(self, cog: Starboard, ctx: Context, config: StarboardConfig | None) -> None:
        super().__init__(timeout=300, members=ctx.author, clear_on_timeout=False)
        self.cog = cog
        self.guild = ctx.guild  # set: command is guild-only
        self.config = config
        self._sync_labels()

    # -- helpers ----------------------------------------------------------

    def embed(self) -> discord.Embed:
        assert self.guild is not None
        return build_config_embed(self.config, self.guild)

    def _sync_labels(self) -> None:
        """Reflect the current config on the toggle buttons (label + style)."""
        cfg = self.config
        enabled = bool(cfg and cfg.enabled)
        self_star = bool(cfg and cfg.self_star)
        nsfw = bool(cfg and cfg.allow_nsfw)

        self.toggle_enabled.label = 'Disable' if enabled else 'Enable'
        self.toggle_enabled.style = discord.ButtonStyle.red if enabled else discord.ButtonStyle.green
        self.toggle_self_star.label = f"Self-star: {'on' if self_star else 'off'}"
        self.toggle_self_star.style = discord.ButtonStyle.green if self_star else discord.ButtonStyle.grey
        self.toggle_nsfw.label = f"NSFW: {'on' if nsfw else 'off'}"
        self.toggle_nsfw.style = discord.ButtonStyle.green if nsfw else discord.ButtonStyle.grey

    async def apply(self, interaction: discord.Interaction, **columns: object) -> None:
        """Persist a config change, refresh the panel, and acknowledge the interaction."""
        assert self.guild is not None
        self.config = await self.cog._update_config(self.guild.id, **columns)
        self._sync_labels()
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=self.embed(), view=self)
        else:
            await interaction.response.edit_message(embed=self.embed(), view=self)

    # -- components -------------------------------------------------------

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text, discord.ChannelType.news],
        placeholder='Select the starboard channel…',
        row=0,
    )
    async def select_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect) -> None:
        await self.apply(interaction, channel_id=select.values[0].id)

    @discord.ui.button(label='Enable', style=discord.ButtonStyle.green, row=1)
    async def toggle_enabled(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.apply(interaction, enabled=not (self.config and self.config.enabled))

    @discord.ui.button(label='Self-star: off', style=discord.ButtonStyle.grey, row=1)
    async def toggle_self_star(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.apply(interaction, self_star=not (self.config and self.config.self_star))

    @discord.ui.button(label='NSFW: off', style=discord.ButtonStyle.grey, row=1)
    async def toggle_nsfw(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.apply(interaction, allow_nsfw=not (self.config and self.config.allow_nsfw))

    @discord.ui.button(label='Threshold', style=discord.ButtonStyle.blurple, emoji='⭐', row=2)
    async def set_threshold(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(_ThresholdModal(self))

    @discord.ui.button(label='Emoji', style=discord.ButtonStyle.blurple, emoji='😀', row=2)
    async def set_emoji(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(_EmojiModal(self))

    @discord.ui.button(label='Max age', style=discord.ButtonStyle.blurple, emoji='⏳', row=2)
    async def set_max_age(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(_MaxAgeModal(self))
