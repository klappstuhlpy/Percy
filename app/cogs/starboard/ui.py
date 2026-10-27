"""Presentation for the starboard.

Covers three surfaces:

* the mirrored post (a Components V2 card with a jump-link button),
* the read-only configuration embed shown by ``starboard show``, and
* the manager-only :class:`StarboardConfigView` interactive settings panel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from app.cogs.starboard.engine import color_for_stars, star_emoji_for
from app.cogs.starboard.models import DEFAULT_EMOJI, DEFAULT_THRESHOLD
from app.core.components_v2 import NoticeView
from app.core.views import View
from app.utils import helpers

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.cogs.starboard.cog import Starboard
    from app.cogs.starboard.models import StarboardConfig
    from app.core import Context

__all__ = (
    'POST_MENTIONS',
    'StarboardConfigView',
    'build_config_embed',
    'build_starboard_content',
    'build_starboard_view',
)

#: File extensions treated as previewable media for the gallery.
_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
_VIDEO_EXTENSIONS = ('.mp4', '.mov', '.webm')

#: How much of a replied-to message to keep before truncating.
_REPLY_SNIPPET_LEN = 100

#: Discord's limits for one Components V2 message / one media gallery.
_TEXT_BUDGET = 4000
_GALLERY_LIMIT = 10

#: Starred content is mirrored verbatim, and in a CV2 text display (unlike an embed) a mention
#: would ping. Every send/edit of a starboard post must pass this.
POST_MENTIONS = discord.AllowedMentions.none()


def build_starboard_content(emoji: str, star_count: int, channel: discord.abc.GuildChannel | discord.Thread) -> str:
    """The one-line star header of a starboard post.

    Uses the configured emoji when it's customized; otherwise escalates the default
    star through its popularity tiers.
    """
    display = star_emoji_for(star_count) if emoji == DEFAULT_EMOJI else emoji
    return f"{display} **{star_count}** {channel.mention}"


def _truncate(text: str, length: int = _REPLY_SNIPPET_LEN) -> str:
    text = ' '.join(text.split())  # collapse newlines/runs of whitespace into single spaces
    return text if len(text) <= length else text[: length - 1].rstrip() + '…'


def _clip(text: str, length: int) -> str:
    """Truncate keeping line breaks (for the message body)."""
    if length <= 0:
        return ''
    return text if len(text) <= length else text[: length - 1].rstrip() + '…'


def _is_media(attachment: discord.Attachment) -> bool:
    content_type = attachment.content_type or ''
    name = attachment.filename.lower()
    return (
        content_type.startswith(('image/', 'video/'))
        or name.endswith(_IMAGE_EXTENSIONS)
        or name.endswith(_VIDEO_EXTENSIONS)
    )


def _gallery_items(message: discord.Message) -> list[discord.MediaGalleryItem]:
    """Media attachments (spoilers stay spoilered) followed by image/GIF embed previews."""
    items = [
        discord.MediaGalleryItem(a.url, spoiler=a.is_spoiler()) for a in message.attachments if _is_media(a)
    ]
    for embed in message.embeds:
        url = embed.image.url or (embed.thumbnail.url if embed.type in ('image', 'gifv') else None)
        if url:
            items.append(discord.MediaGalleryItem(url))
    return items[:_GALLERY_LIMIT]


def _reply_line(message: discord.Message) -> str | None:
    """A compact 'replying to …' line when the starred message is a reply."""
    ref = message.reference
    if ref is None:
        return None
    resolved = ref.resolved
    if not isinstance(resolved, discord.Message):
        return None

    snippet = _truncate(resolved.content) if resolved.content else '*[no text]*'
    return f"-# \N{LEFTWARDS ARROW WITH HOOK} Replying to **{resolved.author.display_name}**: {snippet}"


def _describe_attachments(attachments: Sequence[discord.Attachment]) -> str | None:
    """A summary of attachments that the gallery can't show (files, audio, ...)."""
    extras = [a for a in attachments if not _is_media(a)]
    if not extras:
        return None

    lines = [
        f"{'\N{WARNING SIGN} ' if a.is_spoiler() else ''}\N{PAPERCLIP} [{_truncate(a.filename, 80)}]({a.url})"
        for a in extras[:5]
    ]
    if len(extras) > 5:
        lines.append(f"…and {len(extras) - 5} more")
    return '\n'.join(lines)


def build_starboard_view(
    message: discord.Message, *, emoji: str, star_count: int, threshold: int
) -> discord.ui.LayoutView:
    """Builds the Components V2 card mirroring ``message`` on the starboard.

    The rail colour warms as ``star_count`` climbs past ``threshold``. Send/edit it with
    ``allowed_mentions=POST_MENTIONS`` and no content/embeds.
    """
    container = discord.ui.Container(accent_colour=discord.Colour(color_for_stars(star_count, threshold)))

    header = (
        f"{build_starboard_content(emoji, star_count, message.channel)}\n"  # type: ignore[arg-type]
        f"**{message.author.display_name}**"
    )
    container.add_item(
        discord.ui.Section(header, accessory=discord.ui.Thumbnail(message.author.display_avatar.url))
    )

    reply = _reply_line(message)
    attachments = _describe_attachments(message.attachments)
    stickers = ', '.join(s.name for s in message.stickers)
    stickers_line = f"-# Stickers: {stickers}" if stickers else None
    footer = f"-# {discord.utils.format_dt(message.created_at, 'f')} · ID: {message.id}"

    fixed = [header, reply, attachments, stickers_line, footer]
    budget = _TEXT_BUDGET - sum(len(t) for t in fixed if t) - 50

    if reply:
        container.add_item(discord.ui.TextDisplay(reply))
    if message.content:
        container.add_item(discord.ui.TextDisplay(_clip(message.content, budget)))

    gallery = _gallery_items(message)
    if gallery:
        container.add_item(discord.ui.MediaGallery(*gallery))
    for extra in (attachments, stickers_line):
        if extra:
            container.add_item(discord.ui.TextDisplay(extra))

    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(footer))

    view = NoticeView(container)
    row = discord.ui.ActionRow()
    row.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label='Jump to message', url=message.jump_url))
    view.add_item(row)
    return view


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
        self.config = await self.cog.update_config(self.guild.id, **columns)
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
