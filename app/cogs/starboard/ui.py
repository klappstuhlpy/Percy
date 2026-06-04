"""Presentation for the starboard: the mirrored message content and embed."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from app.cogs.starboard.engine import star_emoji_for
from app.cogs.starboard.models import DEFAULT_EMOJI
from app.utils import helpers

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('build_starboard_content', 'build_starboard_embed')

#: File extensions treated as previewable images for the embed thumbnail.
_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.webp')


def build_starboard_content(emoji: str, star_count: int, channel: discord.abc.GuildChannel | discord.Thread) -> str:
    """The one-line header shown above the mirrored embed.

    Uses the configured emoji when it's customized; otherwise escalates the default
    star through its popularity tiers.
    """
    display = star_emoji_for(star_count) if emoji == DEFAULT_EMOJI else emoji
    return f"{display} **{star_count}** {channel.mention}"


def _first_image(attachments: Sequence[discord.Attachment]) -> discord.Attachment | None:
    for attachment in attachments:
        if attachment.filename.lower().endswith(_IMAGE_EXTENSIONS):
            return attachment
    return None


def build_starboard_embed(message: discord.Message) -> discord.Embed:
    """Builds the embed mirroring an original message onto the starboard."""
    embed = discord.Embed(
        description=message.content or None,
        colour=helpers.Colour.energy_yellow(),
        timestamp=message.created_at,
    )
    embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
    embed.add_field(name='Source', value=f'[Jump to message]({message.jump_url})', inline=False)

    image = _first_image(message.attachments)
    if image is not None:
        embed.set_image(url=image.url)
    elif message.embeds and message.embeds[0].type == 'image' and message.embeds[0].url:
        embed.set_image(url=message.embeds[0].url)

    # Surface a non-image attachment as a hint, since it can't be previewed inline.
    if image is None and message.attachments:
        embed.add_field(name='Attachment', value=message.attachments[0].filename, inline=False)

    embed.set_footer(text=f'ID: {message.id}')
    return embed
